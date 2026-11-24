"""The tables ApplAI keeps in the candidate's database.

Every row carries the user_id of the candidate it belongs to, ready for multi-user;
there is no users table yet. States are plain text; applai.orchestrator.move() is the
only way to change one, along its transition table: a state assigned on an object, or set
by an UPDATE that move() did not mark with MOVE_OPTION, is refused. Times are UTC, stored
without a time zone, which SQLite cannot keep.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Connection,
    Engine,
    Executable,
    ForeignKey,
    MetaData,
    UniqueConstraint,
    Update,
    event,
    inspect,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Mapper, mapped_column

LOCAL_USER_ID = 1  # the one candidate of a local ApplAI, until accounts come


class OfferState(StrEnum):
    """The states an offer can be in; the orchestrator says which moves are allowed."""

    NEW = "new"
    SCREENED_OUT = "screened_out"
    DESCRIBED = "described"
    TRIAGED = "triaged"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    LATER = "later"


class OfferOrigin(StrEnum):
    """Who found an offer: ApplAI, or the old workflow, read from Notion (ADR 0006)."""

    APPLAI = "applai"
    OLD_WORKFLOW = "old_workflow"


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    # Named constraints, so a later migration can find them to drop or change them.
    metadata = MetaData(
        naming_convention={
            "pk": "pk_%(table_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ix": "ix_%(column_0_N_label)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
        }
    )


class Offer(Base):
    __tablename__ = "offers"
    # The official URL identifies an offer (ADR 0006); stored as given, not normalized.
    __table_args__ = (UniqueConstraint("user_id", "url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int]
    url: Mapped[str]
    reference: Mapped[str | None]  # the ATS's or the company's, not unique
    title: Mapped[str]
    company: Mapped[str]
    location: Mapped[str | None]  # this and contract as the source gives them
    contract: Mapped[str | None]
    work_mode: Mapped[str | None]  # remote, hybrid or onsite; none when unknown
    years_required: Mapped[int | None]
    screened_rule: Mapped[str | None]  # the rule that set it aside (applai.screening)
    screen_note: Mapped[str | None]  # remote or grande_couronne: Triage marks it down
    source: Mapped[str | None]  # where the watch found it: alert_linkedin…
    board: Mapped[str | None]  # the job board its alert says it comes via (Google's)
    received_at: Mapped[datetime | None]  # when the job alert listing it was received
    description: Mapped[str | None]  # the offer's text; Triage refuses an offer without it
    salary: Mapped[str | None]  # as the offer's page gives it
    logo: Mapped[str | None]  # a file name in the data directory's logos/; none: initials
    detail_error: Mapped[str | None]  # why the offer's page could not be read (applai.details)
    detail_failed_at: Mapped[datetime | None]  # the last of them, never cleared
    page_failures: Mapped[int] = mapped_column(server_default="0")  # by the extension
    page_failed_at: Mapped[datetime | None]  # the last of them (applai.browser)
    reread_requested_at: Mapped[datetime | None]  # the button: set until it ends (applai.reread)
    reread_error: Mapped[str | None]  # why the last read again failed, kept until the next
    reread_failed_at: Mapped[datetime | None]
    state: Mapped[str] = mapped_column(server_default="new")
    origin: Mapped[str] = mapped_column(server_default=OfferOrigin.APPLAI)
    # The row's status in the old workflow's Notion database, as text: never a state.
    notion_status: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=_utc_now, onupdate=_utc_now)


class MailRead(Base):
    """A job alert the watch has read, so it is never read again.

    error says why its offers could not be read; none means they were.
    """

    __tablename__ = "mails_read"
    __table_args__ = (UniqueConstraint("user_id", "message_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int]
    message_id: Mapped[str]  # Graph's immutable id of the mail
    sender: Mapped[str]  # the address the alert came from
    received_at: Mapped[datetime]
    error: Mapped[str | None]
    read_at: Mapped[datetime] = mapped_column(default=_utc_now)


class LinkedInRead(Base):
    """One call to get_job_details on the candidate's LinkedIn MCP server (applai.linkedin).

    Counted per UTC day against the daily limit, failed calls included.
    """

    __tablename__ = "linkedin_reads"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int]
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id"))
    read_at: Mapped[datetime] = mapped_column(index=True)


class Scan(Base):
    """One run of the watch (applai.scan): the new offers it found and each step's error.

    finished_at stays none while it runs, or if ApplAI stopped during it. Its times are the
    scan's clock's.
    """

    __tablename__ = "scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int]
    by_hand: Mapped[bool]  # else at a slot of the schedule
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    new_offers: Mapped[int] = mapped_column(default=0)
    errors: Mapped[list[str]] = mapped_column(JSON, default=list)  # in plain words


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int]
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id"), unique=True)
    state: Mapped[str]
    form_url: Mapped[str | None]  # where the offer's form lives (applai.capture)
    form_url_error: Mapped[str | None]  # why it was not found or read: the candidate pastes it
    # The form's fields as the extension read them (applai.capture.FormField), none until then.
    form_fields: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    form_failures: Mapped[int] = mapped_column(server_default="0")  # by the extension
    form_failed_at: Mapped[datetime | None]  # the last of them
    draft_error: Mapped[str | None]  # why the last Drafting failed (applai.drafting)
    created_at: Mapped[datetime] = mapped_column(default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=_utc_now, onupdate=_utc_now)


class DraftAnswer(Base):
    """One answer, or the cover letter, the Drafting agent wrote for a field of a form.

    sentences are the agent's, each {text, facts} citing validated facts (ADR 0008); text
    is them joined without the facts' ids. status is pending until the candidate's label
    (applai.correct); edited_text is the candidate's, when they edited it.
    """

    __tablename__ = "draft_answers"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int]
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), index=True)
    agent_run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"))
    kind: Mapped[str]  # question or cover_letter
    field_index: Mapped[int]  # the field of applications.form_fields it fills
    label: Mapped[str]  # the field's
    sentences: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    text: Mapped[str]
    edited_text: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default="pending")  # accepted, edited, rejected
    created_at: Mapped[datetime] = mapped_column(default=_utc_now)


class StateChange(Base):
    """One move of an offer, or of its application, from one state to another."""

    __tablename__ = "state_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int]
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id"))
    application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id"))
    from_state: Mapped[str | None]  # none for the first state
    to_state: Mapped[str]
    actor: Mapped[str]
    changed_at: Mapped[datetime] = mapped_column(default=_utc_now)


class AgentRun(Base):
    """One agent call, as the trace of its offer keeps it (applai.trace).

    outcome is "ok", or the kind of the error. A failed run has no tokens and no cost.
    """

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int]
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id"), index=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id"))
    agent: Mapped[str]
    model: Mapped[str]  # the model that ran, or the spec's when the run failed
    started_at: Mapped[datetime]
    duration_ms: Mapped[int]
    input_tokens: Mapped[int | None]  # cached input included
    output_tokens: Mapped[int | None]
    cost_usd: Mapped[float | None]  # estimated at API list price
    outcome: Mapped[str]


class Triage(Base):
    """The card the Triage agent wrote for one offer, and the agent run that wrote it.

    Its fields are those of applai.triage.CARD_SCHEMA, which the card was checked against, but
    fixed_term_months; cons may end with what code adds about a CDD's length.
    """

    __tablename__ = "triages"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int]
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id"), unique=True)
    agent_run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"))
    score: Mapped[int]
    tags: Mapped[dict[str, Any]] = mapped_column(JSON)
    about: Mapped[str]
    mission: Mapped[list[str]] = mapped_column(JSON)
    pros: Mapped[list[str]] = mapped_column(JSON)
    cons: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=_utc_now)

    def card(self) -> dict[str, Any]:
        """The card as stored: the agent's, with what code added to its cons."""
        return {
            "score": self.score,
            "tags": self.tags,
            "about": self.about,
            "mission": self.mission,
            "pros": self.pros,
            "cons": self.cons,
        }


class Label(Base):
    """A decision the candidate recorded at a gate, with its reasons (GLOSSARY: label).

    level is "offer" (applai.review) or "answers" (applai.correct), whose labels also name
    their application. correction says the decision went against the agents' proposal.
    reasons is a list, empty unless the offer was declined. diff is an edited answer's,
    word by word against the drafted text.
    """

    __tablename__ = "labels"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int]
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id"), index=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id"))
    level: Mapped[str]
    decision: Mapped[str]
    reasons: Mapped[list[str]] = mapped_column(JSON)
    note: Mapped[str | None]
    correction: Mapped[bool]
    diff: Mapped[list[dict[str, str]] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=_utc_now)


MOVE_OPTION = "applai_move"  # the execution option move() marks its own UPDATE with
_REFUSED = "a state changes only through applai.orchestrator.move()."


@event.listens_for(Offer, "before_update")
@event.listens_for(Application, "before_update")
def _refuse_a_state_set_by_hand(_: Mapper[Any], __: Connection, item: Offer | Application) -> None:
    # move() changes a state with its own UPDATE, never through an assigned attribute.
    if inspect(item).attrs.state.history.has_changes():
        raise ValueError(f"{type(item).__name__} {item.id}: {_REFUSED}")


@event.listens_for(Engine, "before_execute")
def _refuse_a_state_set_by_a_statement(
    _: Connection,
    statement: Executable,
    multiparams: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    execution_options: Mapping[str, Any],
) -> None:
    # Every engine's statements come here, from a session or straight on a connection.
    # Raw SQL strings do not, and ApplAI writes none.
    if not isinstance(statement, Update) or execution_options.get(MOVE_OPTION):
        return
    table = getattr(statement.table, "name", None)
    if table not in (Offer.__tablename__, Application.__tablename__):
        return
    # SQLAlchemy has no public way to read a statement's SET columns.
    values = statement._values or {}  # pyright: ignore[reportPrivateUsage]
    columns = {key if isinstance(key, str) else key.key for key in values}
    for parameters in (params, *multiparams):  # an UPDATE also sets the columns these name
        columns.update(parameters)
    if "state" in columns:
        raise ValueError(f"UPDATE of {table}: {_REFUSED}")
