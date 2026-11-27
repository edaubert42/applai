"""The answers gate (« Correct »): the drafted answers the candidate reads, and their labels.

Each decision on an answer is a label at the answers level (ADR 0004): accepting is not a
correction; editing and rejecting are. An edit keeps the candidate's text and its word diff
against the drafted text; a rejected answer is drafted again alone (applai.drafting). Once
every answer is accepted or edited, and every change of CV tailoring (applai.cv_tailoring) is
decided the same way, as a label at the "cv" level, the candidate moves the application to
answers_checked. Functions write in the caller's session and do not commit.
"""

import json
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from applai.cv_tailoring import headline_problem
from applai.drafting import AnswerStatus, sentence_text
from applai.models import Application, CvChange, DraftAnswer, Label, Offer
from applai.orchestrator import Actor, ApplicationState, move
from applai.profile import Profile

ANSWERS_LEVEL = "answers"
CV_LEVEL = "cv"
NOT_JUDGED = "not_judged"
_RESOLVED = (AnswerStatus.ACCEPTED, AnswerStatus.EDITED)


class AnswerDecision(StrEnum):
    ACCEPT = "accept"
    EDIT = "edit"
    REJECT = "reject"


_STATUS = {
    AnswerDecision.ACCEPT: AnswerStatus.ACCEPTED,
    AnswerDecision.EDIT: AnswerStatus.EDITED,
    AnswerDecision.REJECT: AnswerStatus.REJECTED,
}


class AnswerRequest(BaseModel):
    """A decision on an answer: an edit has its text, a reject its reason."""

    model_config = ConfigDict(extra="forbid")

    decision: AnswerDecision
    text: str | None = Field(default=None, max_length=20000)
    reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _what_each_decision_needs(self) -> "AnswerRequest":
        edits = self.decision == AnswerDecision.EDIT
        if edits != bool(self.text and self.text.strip()):
            raise ValueError("an edit, and only an edit, has a text")
        if self.decision == AnswerDecision.REJECT and not (self.reason and self.reason.strip()):
            raise ValueError("reject needs a reason")
        return self


class CitedFact(BaseModel):
    id: str
    text: str | None  # none: no longer in the profile


class Sentence(BaseModel):
    text: str
    facts: list[CitedFact]


class Answer(BaseModel):
    id: int
    kind: str  # question or cover_letter
    field_index: int
    label: str
    sentences: list[Sentence]
    text: str  # as drafted
    edited_text: str | None
    status: AnswerStatus
    warnings: list[str]  # the judge's: shown to the candidate, they never block the gate


class CvChangeView(BaseModel):
    id: int
    op: str  # reorder, reword or drop
    path: str
    old_text: str
    new_text: str | None  # as drafted; none for a drop
    edited_text: str | None
    old_items: list[str] | None  # a reorder's list, before and after
    new_items: list[str] | None
    facts: list[CitedFact]  # a reword's
    status: AnswerStatus


class Draft(BaseModel):
    application_id: int
    offer_id: int
    title: str
    company: str
    state: ApplicationState
    draft_error: str | None
    answers: list[Answer]
    cv_headline: str | None  # none: the CV source's
    cv_photo: str | None  # with, without, unknown; none: no tailoring
    cv_photo_reason: str | None
    cv_changes: list[CvChangeView]
    resolved: bool  # every answer accepted or edited and every CV change decided


class NotFound(Exception):
    """No such application of the candidate, or no such answer of the application."""


class GateRefused(Exception):
    """The application is not drafted, or an answer is not accepted nor edited yet."""


def _application(session: Session, user_id: int, application_id: int) -> Application:
    application = session.get(Application, application_id)
    if application is None or application.user_id != user_id:
        raise NotFound(application_id)
    return application


def _answers(session: Session, application_id: int) -> list[DraftAnswer]:
    query = select(DraftAnswer).where(DraftAnswer.application_id == application_id)
    return list(session.scalars(query.order_by(DraftAnswer.field_index, DraftAnswer.id)))


def judge_warnings(judge_issues: list[dict[str, Any]] | None) -> list[str]:
    """One warning per sentence the judge flagged, numbered from 1; one when it did not check.

    An answer the judge did not check (its run failed or has not run) must not read as clean.
    """
    if judge_issues is None:
        return [NOT_JUDGED]
    return [f"judge_sentence:{issue['sentence'] + 1} — {issue['reason']}" if issue["reason"]
            else f"judge_unsupported:{issue['sentence'] + 1}"
            for issue in judge_issues]  # fmt: skip


def answer_view(answer: DraftAnswer, profile: Profile) -> Answer:
    """An answer as the candidate reads it: each sentence with the text of its facts, and the
    judge's warnings."""
    facts = {fact.id: fact.text for fact in profile.facts}
    sentences = [
        Sentence(text=sentence_text(s["text"]),
                 facts=[CitedFact(id=i, text=facts.get(i)) for i in s["facts"]])
        for s in answer.sentences
    ]  # fmt: skip
    return Answer(id=answer.id, kind=answer.kind, field_index=answer.field_index,
                  label=answer.label, sentences=sentences, text=answer.text,
                  edited_text=answer.edited_text, status=AnswerStatus(answer.status),
                  warnings=judge_warnings(answer.judge_issues))  # fmt: skip


def _cv_changes(session: Session, application_id: int) -> list[CvChange]:
    query = select(CvChange).where(CvChange.application_id == application_id)
    return list(session.scalars(query.order_by(CvChange.id)))


def cv_change_view(change: CvChange, profile: Profile) -> CvChangeView:
    """A CV change as the candidate reads it: a reorder's lists, the text of a reword's facts."""
    facts = {fact.id: fact.text for fact in profile.facts}
    reorder = change.op == "reorder"
    return CvChangeView(
        id=change.id, op=change.op, path=change.path, old_text=change.old_text,
        new_text=change.new_text, edited_text=change.edited_text,
        old_items=json.loads(change.old_text) if reorder else None,
        new_items=json.loads(change.new_text or "[]") if reorder else None,
        facts=[CitedFact(id=i, text=facts.get(i)) for i in change.facts],
        status=AnswerStatus(change.status),
    )  # fmt: skip


def draft(session: Session, user_id: int, application_id: int, profile: Profile) -> Draft:
    """The application's drafted answers, in the order of its form, and its CV tailoring.

    Raises NotFound.
    """
    application = _application(session, user_id, application_id)
    offer = session.get_one(Offer, application.offer_id)
    answers = [answer_view(answer, profile) for answer in _answers(session, application_id)]
    changes = [cv_change_view(c, profile) for c in _cv_changes(session, application_id)]
    resolved = all(answer.status in _RESOLVED for answer in answers)
    return Draft(application_id=application.id, offer_id=offer.id, title=offer.title,
                 company=offer.company, state=ApplicationState(application.state),
                 draft_error=application.draft_error, answers=answers,
                 cv_headline=application.cv_headline, cv_photo=application.cv_photo,
                 cv_photo_reason=application.cv_photo_reason, cv_changes=changes,
                 resolved=resolved and all(c.status != AnswerStatus.PENDING for c in changes),
                 )  # fmt: skip


def word_diff(before: str, after: str) -> list[dict[str, str]]:
    """after against before, word by word: equal, delete and insert runs, in order."""
    old, new = before.split(), after.split()
    diff: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag == "equal":
            diff.append({"op": "equal", "text": " ".join(old[i1:i2])})
            continue
        if i2 > i1:
            diff.append({"op": "delete", "text": " ".join(old[i1:i2])})
        if j2 > j1:
            diff.append({"op": "insert", "text": " ".join(new[j1:j2])})
    return diff


def decide(
    session: Session, user_id: int, application_id: int, answer_id: int, request: AnswerRequest
) -> DraftAnswer:
    """Record the candidate's decision on an answer as a label, and set the answer's status.

    A rejected answer stays rejected until it is drafted again: it can be rejected again or
    edited, not accepted, which would accept the text the candidate rejected. Raises NotFound,
    and GateRefused when the application is not drafted or a rejected answer is accepted.
    """
    application = _application(session, user_id, application_id)
    answer = session.get(DraftAnswer, answer_id)
    if answer is None or answer.application_id != application.id:
        raise NotFound(answer_id)
    if application.state != ApplicationState.DRAFTED:
        raise GateRefused(f"Application {application_id} is {application.state}, not drafted.")
    if answer.status == AnswerStatus.REJECTED and request.decision == AnswerDecision.ACCEPT:
        raise GateRefused(f"Answer {answer_id} was rejected: edit it or reject it again.")
    edited = request.text.strip() if request.text else None
    answer.status, answer.edited_text = _STATUS[request.decision], edited
    session.add(
        Label(user_id=user_id, offer_id=application.offer_id, application_id=application.id,
              level=ANSWERS_LEVEL, decision=_STATUS[request.decision], reasons=[],
              note=request.reason, correction=request.decision != AnswerDecision.ACCEPT,
              diff=word_diff(answer.text, edited) if edited else None)
    )  # fmt: skip
    session.flush()
    return answer


def check_answers(session: Session, user_id: int, application_id: int) -> None:
    """Move a drafted application whose every answer is accepted or edited to answers_checked.

    Raises NotFound, GateRefused, and the orchestrator's TransitionError.
    """
    application = _application(session, user_id, application_id)
    if application.state != ApplicationState.DRAFTED:
        raise GateRefused(f"Application {application_id} is {application.state}, not drafted.")
    left = [a.label for a in _answers(session, application_id) if a.status not in _RESOLVED]
    if left:
        raise GateRefused(f"Accept or edit every answer first: {len(left)} left.")
    pending = [c for c in _cv_changes(session, application_id) if c.status == AnswerStatus.PENDING]
    if pending:
        raise GateRefused(f"Decide every CV change first: {len(pending)} left.")
    move(session, application, ApplicationState.ANSWERS_CHECKED, Actor.CANDIDATE)
    if application.form_failed_page is not None:  # a stored page's: it can't be posted again
        application.form_url_error = application.form_failed_page = None


class HeadlineRequest(BaseModel):
    """The candidate's own title line for the CV."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=200)


class PhotoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    photo: Literal["with", "without"]


def _tailored_draft(session: Session, user_id: int, application_id: int) -> Application:
    """The application, drafted and with a CV tailoring, for a decision on its CV."""
    application = _application(session, user_id, application_id)
    if application.state != ApplicationState.DRAFTED:
        raise GateRefused(f"Application {application_id} is {application.state}, not drafted.")
    if application.cv_photo is None:
        raise GateRefused(f"Application {application_id} has no CV tailoring.")
    return application


def _cv_label(application: Application, decision: str, correction: bool, **more: Any) -> Label:
    return Label(user_id=application.user_id, offer_id=application.offer_id,
                 application_id=application.id, level=CV_LEVEL, decision=decision, reasons=[],
                 correction=correction, **more)  # fmt: skip


def decide_cv_change(
    session: Session, user_id: int, application_id: int, change_id: int, request: AnswerRequest
) -> CvChange:
    """Record the candidate's decision on a CV change as a label, and set its status.

    Only a reword has a text to edit. A decision can be changed until the answers gate closes.
    Raises NotFound, and GateRefused when the application is not drafted or the edit is not
    of a reword.
    """
    application = _tailored_draft(session, user_id, application_id)
    change = session.get(CvChange, change_id)
    if change is None or change.application_id != application.id:
        raise NotFound(change_id)
    if request.decision == AnswerDecision.EDIT and change.op != "reword":
        raise GateRefused(f"Change {change_id} is a {change.op}: only a reword has a text.")
    edited = request.text.strip() if request.text else None
    change.status, change.edited_text = _STATUS[request.decision], edited
    diff = word_diff(change.new_text or "", edited) if edited else None
    session.add(_cv_label(application, _STATUS[request.decision],
                          request.decision != AnswerDecision.ACCEPT, note=request.reason,
                          diff=diff))  # fmt: skip
    session.flush()
    return change


def edit_cv_headline(
    session: Session, user_id: int, application_id: int, request: HeadlineRequest
) -> Application:
    """Replace the CV's title line with the candidate's, a correction. The rules the agent's
    headline obeys hold: GateRefused says which one it breaks."""
    application = _tailored_draft(session, user_id, application_id)
    text = request.text.strip()
    if problem := headline_problem(text):
        raise GateRefused(f"The headline {problem}.")
    before = application.cv_headline or ""
    application.cv_headline = text
    session.add(_cv_label(application, AnswerStatus.EDITED, True, diff=word_diff(before, text)))
    session.flush()
    return application


def switch_cv_photo(
    session: Session, user_id: int, application_id: int, request: PhotoRequest
) -> Application:
    """Set the CV's photo choice to with or without: the candidate's, a correction when it
    differs from the choice kept."""
    application = _tailored_draft(session, user_id, application_id)
    session.add(_cv_label(application, request.photo, request.photo != application.cv_photo))
    application.cv_photo = request.photo
    session.flush()
    return application
