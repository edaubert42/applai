"""The public demo's applications, and the agent runs it replays (ADR 0007).

demo/candidate/offers/ holds fictional offers, each an offer case of the Drafting bench
(applai.bench.drafting). run_demo() runs the real pipeline on them in a new database: Triage
reads each; an offer it leaves triaged with a score of at least review_min_score is accepted,
as the candidate would, its form is its questions as textareas and the cover letter's field
when asked, and it is drafted (Drafting, the judge, CV tailoring). The others are only
triaged. Nothing fills a form, nothing is sent.

applai demo serves the demo (demo_app): each start builds the board again in a new
database, so whatever a visitor changed is gone at the next one.

applai demo record runs it with the real agents through a Recorder and writes every run to
demo/candidate/recordings.json: the runs the file holds are answered from it, so only the
missing ones call Claude, and the file keeps the others.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

import httpx2
from fastapi import FastAPI
from jsonschema import Draft202012Validator
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai import correct
from applai.agents import (
    AgentError,
    AgentResult,
    AgentRunner,
    AgentSpec,
    Backend,
    FailureKind,
    ReplayBackend,
    read_recordings,
    recording_key,
    write_recordings,
)
from applai.app import create_app
from applai.bench.drafting import OfferCase, case_fields, load_cases
from applai.bench.triage import DEMO_DIR, BenchError
from applai.correct import AnswerDecision, AnswerRequest
from applai.database import open_database
from applai.drafting import DraftingError, draft_application
from applai.models import LOCAL_USER_ID, Application, CvChange, DraftAnswer, Offer, OfferState
from applai.orchestrator import Actor, ApplicationState, move
from applai.profile import Profile, load_profile
from applai.review import Decision, DecisionRequest, Reason, decide
from applai.scan import Watch
from applai.settings import review_min_score
from applai.triage import TriageError, triage_offer
from applai.validation import DropRequest, drop_application

OFFERS = DEMO_DIR / "offers"
RECORDINGS = DEMO_DIR / "recordings.json"


def demo_offers(profile: Profile) -> list[tuple[str, OfferCase]]:
    """The demo's offers, with their file names, in file-name order."""
    offers: list[tuple[str, OfferCase]] = []
    for name, case in load_cases(OFFERS, profile):
        if not isinstance(case, OfferCase):
            raise BenchError(f"{name}: not an offer")
        offers.append((name, case))
    return offers


def run_demo(runner: AgentRunner, database: Engine) -> list[str]:
    """Run every demo offer through the pipeline, in a new database; what failed, by offer.

    A failed run or a refused draft stays on its offer or application, as in ApplAI, and the
    next offer runs. Then the candidate acts on the drafted applications, in offer order: the
    first waits for them (« vérifier les textes »), they check the answers of the next ones,
    which wait for Filling, and drop the last.
    """
    profile = load_profile(DEMO_DIR)
    min_score = review_min_score(DEMO_DIR)
    failures: list[str] = []
    for name, case in demo_offers(profile):
        try:
            _run_offer(runner, database, name, case, profile, min_score)
        except (AgentError, TriageError, DraftingError) as error:  # they quote no offer
            failures.append(f"{name}: {error}")
    with Session(database) as session, session.begin():
        drafted = list(session.scalars(
            select(Application.id).where(Application.state == ApplicationState.DRAFTED)
            .order_by(Application.id)
        ))  # fmt: skip
        for application_id in drafted[1:-1]:
            _check_answers(session, application_id)
        if len(drafted) > 1:
            drop = DropRequest(reasons=[Reason.SALARY], note="Fourchette sous mes attentes.")
            drop_application(session, LOCAL_USER_ID, drafted[-1], drop)
    return failures


def _check_answers(session: Session, application_id: int) -> None:
    accept = AnswerRequest(decision=AnswerDecision.ACCEPT)
    answers = select(DraftAnswer.id).where(DraftAnswer.application_id == application_id)
    for answer_id in session.scalars(answers).all():
        correct.decide(session, LOCAL_USER_ID, application_id, answer_id, accept)
    changes = select(CvChange.id).where(CvChange.application_id == application_id)
    for change_id in session.scalars(changes).all():
        correct.decide_cv_change(session, LOCAL_USER_ID, application_id, change_id, accept)
    correct.check_answers(session, LOCAL_USER_ID, application_id)


def _refuse(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("The demo reaches no network.", request=request)


def demo_app(database_dir: Path, backup_dir: Path) -> tuple[FastAPI, list[str]]:
    """The demo's app, on a new database in database_dir its board was built in, and what
    failed building it, by offer (run_demo).

    Its agents replay the recordings, its watch reaches no network and never scans, and it
    sends nothing (applai.app): demo/candidate is only read. The board is built without the
    replay's wait; a visitor waits it.
    """
    recordings = read_recordings(RECORDINGS)
    database = open_database(database_dir, backup_dir)
    try:
        failures = run_demo(AgentRunner(ReplayBackend(recordings, max_wait_s=0)), database)
    finally:
        database.dispose()
    watch = Watch(http=httpx2.Client(transport=httpx2.MockTransport(_refuse)),
                  runner=AgentRunner(ReplayBackend(recordings)), ms_client_id=None)  # fmt: skip
    app = create_app(DEMO_DIR, watch=watch, database_dir=database_dir, backup_dir=backup_dir,
                     demo=True)  # fmt: skip
    return app, failures


def _run_offer(
    runner: AgentRunner,
    database: Engine,
    name: str,
    case: OfferCase,
    profile: Profile,
    min_score: int,
) -> None:
    with Session(database) as session, session.begin():
        offer = Offer(user_id=LOCAL_USER_ID, url=f"https://jobs.example.org/demo/{name}",
                      title=case.title, company=case.company, description=case.description,
                      state=OfferState.DESCRIBED)  # fmt: skip
        session.add(offer)
        session.flush()
        offer_id = offer.id
    triage = triage_offer(runner, database, offer_id, profile)
    with Session(database) as session, session.begin():
        if session.get_one(Offer, offer_id).state != OfferState.TRIAGED or triage.score < min_score:
            return
        decide(session, LOCAL_USER_ID, offer_id, DecisionRequest(decision=Decision.INTERESTED),
               min_score)  # fmt: skip
        application = session.scalars(
            select(Application).where(Application.offer_id == offer_id)
        ).one()
        application.form_fields = case_fields(case)
        move(session, application, ApplicationState.FORM_CAPTURED, Actor.FORM_CAPTURE)
        application_id = application.id
    draft_application(runner, database, application_id, profile, DEMO_DIR)


class Recorder:
    """Answers each spec from the recordings, or else with the backend, and keeps its result.

    A run the backend fails, or whose output the schema refuses, is counted and raised:
    nothing is recorded for it.
    """

    def __init__(self, backend: Backend, recordings: dict[str, AgentResult]) -> None:
        self.backend = backend
        self.recordings = recordings
        self.new: list[AgentResult] = []
        self.failed = 0

    def run(self, spec: AgentSpec) -> AgentResult:
        key = recording_key(spec)
        if key not in self.recordings:
            try:
                result = self.backend.run(spec)
                if not Draft202012Validator(spec.json_schema).is_valid(  # pyright: ignore[reportUnknownMemberType]
                    result.output
                ):
                    raise AgentError(spec.name, FailureKind.SCHEMA_MISMATCH)
            except Exception:
                self.failed += 1
                raise
            self.recordings[key] = result
            self.new.append(result)
        return self.recordings[key]


def record(backend: Backend, path: Path) -> tuple[Recorder, list[str]]:
    """Run the demo, with the backend for the runs the file at path lacks, and write them all.

    The runs are written even when the demo stops on the way: they were paid for. A file that
    cannot be read is left as it is. Returns the recorder and what failed, by offer.
    """
    recorder = Recorder(backend, read_recordings(path))
    try:
        with TemporaryDirectory() as data_dir, TemporaryDirectory() as backup_dir:
            database = open_database(data_dir, backup_dir)
            try:
                failures = run_demo(AgentRunner(recorder), database)
            finally:
                database.dispose()
    finally:
        write_recordings(path, recorder.recordings)
    return recorder, failures
