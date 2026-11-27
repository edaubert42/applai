"""The public demo's applications, and the agent runs it replays (ADR 0007).

demo/candidate/offers/ holds fictional offers, each an offer case of the Drafting bench
(applai.bench.drafting). run_demo() runs the real pipeline on them in a new database: Triage
reads each; an offer it leaves triaged with a score of at least review_min_score is accepted,
as the candidate would, its form is the demo's own (applai.demo_fill), and it is drafted
(Drafting, the judge, CV tailoring). The others are only triaged. The candidate then acts on
the drafted ones (run_demo): the fill of the checked ones is replayed (applai.demo_fill) and
reviewed, and the first goes on to sent as the candidate's labels. Nothing reaches a form.

applai demo serves the demo (demo_app): each start builds the board again in a new
database, so whatever a visitor changed is gone at the next one.

applai demo record runs it with the real agents through a Recorder and writes every run to
demo/candidate/recordings.json: the runs the file holds are answered from it, so only the
missing ones call Claude, and the file keeps the others. It also plans the fill of every
drafted application, its answers checked as drafted, and records the fills demo/fills lacks.
"""

import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx2
from fastapi import FastAPI
from jsonschema import Draft202012Validator
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai import correct, demo_fill
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
from applai.bench.drafting import OfferCase, load_cases
from applai.bench.triage import DEMO_DIR, BenchError
from applai.correct import AnswerDecision, AnswerRequest
from applai.database import open_database
from applai.drafting import DraftingError, draft_application
from applai.models import LOCAL_USER_ID, Application, CvChange, DraftAnswer, Offer, OfferState
from applai.orchestrator import Actor, ApplicationState, move
from applai.profile import Profile, load_profile
from applai.review import Decision, DecisionRequest, Reason, decide
from applai.scan import Watch, local_now
from applai.settings import review_min_score
from applai.triage import TriageError, triage_offer
from applai.validation import (
    DropRequest,
    FormCheckedRequest,
    check_form,
    drop_application,
    mark_sent,
    validate_application,
)

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


def run_demo(runner: AgentRunner, database: Engine, data_dir: Path) -> list[str]:
    """Run every demo offer through the pipeline, in a new database; what failed, by offer or
    application. Fills write their files in data_dir, a copy of demo/candidate.

    A failed run or a refused draft stays on its offer or application, as in ApplAI, and the
    next offer runs. Then the candidate acts on the drafted applications, in offer order: the
    first waits for them (« vérifier les textes »), they check the answers of the next ones,
    whose fill is replayed and reviewed, and drop the last. The first of those goes on through
    the form check, final validation and sent, as the candidate's labels: nothing is sent.
    """
    failures, checked = _build(runner, database)
    _fill(runner, database, data_dir, checked)
    _send_first(database, checked)
    return failures + _stuck(database, checked)


def _build(runner: AgentRunner, database: Engine) -> tuple[list[str], list[int]]:
    """The offers through Drafting, then the candidate's choices on the drafted applications.
    What failed, by offer, and the applications whose answers the candidate checked."""
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
        checked = drafted[1:-1]
        for application_id in checked:
            _check_answers(session, application_id)
        if len(drafted) > 1:
            drop = DropRequest(reasons=[Reason.SALARY], note="Fourchette sous mes attentes.")
            drop_application(session, LOCAL_USER_ID, drafted[-1], drop)
    return failures, checked


def _fill(runner: AgentRunner, database: Engine, data_dir: Path, ids: list[int]) -> None:
    """Plan each application's fill, replay its recorded fill and review it (applai.demo_fill).
    A failure stays on the application, as for a visitor."""
    for application_id in ids:
        demo_fill.fill_in_background(database, data_dir, runner, application_id, local_now)


def _stuck(database: Engine, ids: list[int]) -> list[str]:
    """The applications still waiting for Filling or Review, with why, by application."""
    with Session(database) as session:
        stuck = [(a.id, a.fill_error or a.review_error) for a in session.scalars(
            select(Application).where(Application.id.in_(ids)).order_by(Application.id)
        ) if a.state in (ApplicationState.ANSWERS_CHECKED, ApplicationState.FILLED)]  # fmt: skip
    return [f"application {application_id}: {error}" for application_id, error in stuck]


def _send_first(database: Engine, checked: list[int]) -> None:
    """The first checked application, if reviewed, is form-checked, validated and sent: the
    candidate's labels, each a move(). It reaches no form: nothing is sent (ADR 0003)."""
    with Session(database) as session, session.begin():
        application = session.get(Application, checked[0]) if checked else None
        if application is None or application.state != ApplicationState.REVIEWED:
            return  # Filling or Review failed: shown on the application
        check_form(session, LOCAL_USER_ID, application.id, FormCheckedRequest())
        validate_application(session, LOCAL_USER_ID, application.id)
        mark_sent(session, LOCAL_USER_ID, application.id)


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


def demo_app(
    database_dir: Path, backup_dir: Path, public_host: str | None = None
) -> tuple[FastAPI, list[str]]:
    """The demo's app, on a new database in database_dir its board was built in, and what
    failed building it, by offer (run_demo); public_host is the name it is hosted at, if any.

    Its agents replay the recordings, its watch reaches no network and never scans, and it
    sends nothing (applai.app). It serves a copy of demo/candidate in database_dir, where a
    fill writes its files: demo/candidate is only read. The board is built without the
    replay's wait; a visitor waits it.
    """
    data_dir = shutil.copytree(DEMO_DIR, database_dir / "candidate")
    recordings = read_recordings(RECORDINGS)
    database = open_database(database_dir, backup_dir)
    try:
        replay = AgentRunner(ReplayBackend(recordings, max_wait_s=0))
        failures = run_demo(replay, database, data_dir)
    finally:
        database.dispose()
    watch = Watch(http=httpx2.Client(transport=httpx2.MockTransport(_refuse)),
                  runner=AgentRunner(ReplayBackend(recordings)), ms_client_id=None)  # fmt: skip
    app = create_app(data_dir, watch=watch, database_dir=database_dir, backup_dir=backup_dir,
                     demo=True, public_host=public_host)  # fmt: skip
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
        application.form_fields = demo_fill.demo_fields(case)
        application.form_url = f"/demo/forms/{application.id}"
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
        with TemporaryDirectory() as directory, TemporaryDirectory() as backup_dir:
            data_dir = shutil.copytree(DEMO_DIR, Path(directory) / "candidate")
            database = open_database(directory, backup_dir)
            try:
                runner = AgentRunner(recorder)
                failures, checked = _build(runner, database)
                failures += _record_fills(runner, database, data_dir, checked)
                _send_first(database, checked)
            finally:
                database.dispose()
    finally:
        write_recordings(path, recorder.recordings)
    return recorder, failures


def _record_fills(
    runner: AgentRunner, database: Engine, data_dir: Path, checked: list[int]
) -> list[str]:
    """Check the answers of the drafted application too, plan the fill of every checked
    application, record the fills demo/fills lacks (applai.demo_fill), then replay them and
    review: the runs Review lacks are recorded too. What failed, by application."""
    with Session(database) as session, session.begin():
        drafted = select(Application.id).where(Application.state == ApplicationState.DRAFTED)
        waiting = list(session.scalars(drafted.order_by(Application.id)))
        for application_id in waiting:
            _check_answers(session, application_id)
    ids = checked + waiting
    _fill(runner, database, data_dir, ids)  # planned; replayed where recorded
    with Session(database) as session:
        planned = select(Application).where(
            Application.id.in_(ids), Application.state == ApplicationState.ANSWERS_CHECKED,
            Application.fill_plan.is_not(None),
        )  # fmt: skip
        plans = [(a.id, a.form_fields or [], a.fill_plan or []) for a in session.scalars(planned)]
    failures = demo_fill.record_fills(data_dir, plans)
    _fill(runner, database, data_dir, [plan[0] for plan in plans])
    return failures + _stuck(database, ids)
