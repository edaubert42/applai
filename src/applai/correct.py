"""The answers gate (« Correct »): the drafted answers the candidate reads, and their labels.

Each decision on an answer is a label at the answers level (ADR 0004): accepting is not a
correction; editing and rejecting are. An edit keeps the candidate's text and its word diff
against the drafted text; a rejected answer is drafted again alone (applai.drafting). Once
every answer is accepted or edited, the candidate moves the application to answers_checked.
Functions write in the caller's session and do not commit.
"""

from difflib import SequenceMatcher
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from applai.drafting import AnswerStatus, sentence_text
from applai.models import Application, DraftAnswer, Label, Offer
from applai.orchestrator import Actor, ApplicationState, move
from applai.profile import Profile

ANSWERS_LEVEL = "answers"
NOT_JUDGED = "Not checked by Judge."
_NO_REASON = "not supported by its cited facts."
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


class Draft(BaseModel):
    application_id: int
    offer_id: int
    title: str
    company: str
    state: ApplicationState
    draft_error: str | None
    answers: list[Answer]
    resolved: bool  # every answer accepted or edited, also when there is none


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
    return [f"Sentence {issue['sentence'] + 1}: {issue['reason'] or _NO_REASON}"
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


def draft(session: Session, user_id: int, application_id: int, profile: Profile) -> Draft:
    """The application's drafted answers, in the order of its form. Raises NotFound."""
    application = _application(session, user_id, application_id)
    offer = session.get_one(Offer, application.offer_id)
    answers = [answer_view(answer, profile) for answer in _answers(session, application_id)]
    return Draft(application_id=application.id, offer_id=offer.id, title=offer.title,
                 company=offer.company, state=ApplicationState(application.state),
                 draft_error=application.draft_error, answers=answers,
                 resolved=all(answer.status in _RESOLVED for answer in answers))  # fmt: skip


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
    move(session, application, ApplicationState.ANSWERS_CHECKED, Actor.CANDIDATE)
