"""The tables ApplAI keeps in the candidate's database.

Every row carries the user_id of the candidate it belongs to, ready for multi-user;
there is no users table yet. States are plain text until the orchestrator gives them
rules. Times are UTC, stored without a time zone, which SQLite cannot keep.
"""

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, MetaData, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

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
    source: Mapped[str | None]  # where the watch found it: alert_linkedin…
    received_at: Mapped[datetime | None]  # when the job alert listing it was received
    state: Mapped[str] = mapped_column(server_default="new")
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


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int]
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id"), unique=True)
    state: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=_utc_now, onupdate=_utc_now)


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
