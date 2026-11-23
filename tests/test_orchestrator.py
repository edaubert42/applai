from collections.abc import Iterator
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, bindparam, func, select, update
from sqlalchemy.orm import Session

from applai.database import open_database
from applai.models import Application, Offer, StateChange
from applai.orchestrator import Actor, StaleStateError, TransitionError, move

type Item = Offer | Application

# The issue's table, written out here so a wrong table in the code fails these tests.
OFFER_MOVES = {
    ("new", "screened_out"): "watch",
    ("new", "described"): "watch",
    ("described", "triaged"): "triage",
    ("described", "screened_out"): "watch",  # a rule found after Triage (#107)
    ("triaged", "accepted"): "candidate",
    ("triaged", "declined"): "candidate",
    ("triaged", "later"): "candidate",
    ("later", "accepted"): "candidate",
    ("later", "declined"): "candidate",
    ("screened_out", "accepted"): "candidate",
}
APPLICATION_CHAIN = [
    ("created", "form_captured", "form_capture"),
    ("form_captured", "drafted", "drafting"),
    ("drafted", "answers_checked", "candidate"),
    ("answers_checked", "filled", "filling"),
    ("filled", "reviewed", "review"),
    ("reviewed", "form_checked", "candidate"),
    ("form_checked", "validated", "candidate"),
    ("validated", "sent", "candidate"),
]
APPLICATION_MOVES = {(origin, to): actor for origin, to, actor in APPLICATION_CHAIN} | {
    (origin, "dropped"): "candidate" for origin, _, _ in APPLICATION_CHAIN
}
OFFER_STATES = sorted({state for pair in OFFER_MOVES for state in pair})
APPLICATION_STATES = sorted({state for pair in APPLICATION_MOVES for state in pair})
ACTORS = ["watch", "triage", "candidate", "form_capture", "drafting", "filling", "review"]
AGENTS = [actor for actor in ACTORS if actor != "candidate"]
GATES = ["accepted", "answers_checked", "form_checked", "validated", "sent"]
TERMINAL = {"offer": ["declined"], "application": ["sent", "dropped"]}
MOVES = {"offer": OFFER_MOVES, "application": APPLICATION_MOVES}
STATES = {"offer": OFFER_STATES, "application": APPLICATION_STATES}

_urls = count()


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = open_database(tmp_path)
    yield engine
    engine.dispose()


def _offer(session: Session, state: str) -> Offer:
    offer = Offer(
        user_id=1,
        url=f"https://jobs.example.org/brindille-logistique/{next(_urls)}",
        title="Data engineer",
        company="Brindille Logistique",
        state=state,
    )
    session.add(offer)
    session.flush()
    return offer


def _item(session: Session, kind: str, state: str) -> Item:
    """An offer or an application already in state, put there directly to set up a case."""
    if kind == "offer":
        return _offer(session, state)
    application = Application(user_id=1, offer_id=_offer(session, "accepted").id, state=state)
    session.add(application)
    session.flush()
    return application


def _changes(session: Session, item: Item) -> list[StateChange]:
    column = StateChange.offer_id if isinstance(item, Offer) else StateChange.application_id
    return list(session.scalars(select(StateChange).where(column == item.id)))


def _stored_state(session: Session, item: Item) -> str:
    model = type(item)
    state = session.scalar(select(model.state).where(model.id == item.id))
    assert state is not None
    return state


def _all_moves() -> list[tuple[str, str, str, str]]:
    return [
        (kind, origin, to, actor)
        for kind, moves in MOVES.items()
        for (origin, to), actor in moves.items()
    ]


@pytest.mark.parametrize(("kind", "origin", "to", "actor"), _all_moves())
def test_each_transition_made_by_its_actor_succeeds_and_is_recorded(
    engine: Engine, kind: str, origin: str, to: str, actor: str
):
    with Session(engine) as session:
        item = _item(session, kind, origin)
        session.commit()
        item_id, model = item.id, type(item)
        before = datetime.now(UTC).replace(tzinfo=None)

        move(session, item, to, actor)
        session.commit()
        after = datetime.now(UTC).replace(tzinfo=None)

    with Session(engine) as session:
        item = session.get_one(model, item_id)
        assert item.state == to
        changes = [change for change in _changes(session, item) if change.from_state == origin]
        assert len(changes) == 1
        change = changes[0]
        assert (change.from_state, change.to_state, change.actor) == (origin, to, actor)
        assert before <= change.changed_at <= after
        assert change.user_id == item.user_id
        if isinstance(item, Application):
            assert (change.offer_id, change.application_id) == (item.offer_id, item.id)
        else:
            assert change.application_id is None


@pytest.mark.parametrize("kind", ["offer", "application"])
def test_every_other_pair_and_every_other_actor_is_refused_and_changes_nothing(
    engine: Engine, kind: str
):
    refused = 0
    with Session(engine) as session:
        for origin in STATES[kind]:
            for to in STATES[kind]:
                for actor in ACTORS:
                    if MOVES[kind].get((origin, to)) == actor:
                        continue
                    item = _item(session, kind, origin)

                    with pytest.raises(TransitionError) as error:
                        move(session, item, to, actor)

                    assert not isinstance(error.value, StaleStateError)
                    message = str(error.value)
                    for name in (origin, to, actor):
                        assert name in message, (origin, to, actor)
                    assert item.state == origin
                    assert _stored_state(session, item) == origin
                    assert _changes(session, item) == []
                    refused += 1
    states = len(STATES[kind])
    assert refused == states * states * len(ACTORS) - len(MOVES[kind])


@pytest.mark.parametrize("kind", ["offer", "application"])
def test_no_agent_actor_can_reach_a_gate_state(engine: Engine, kind: str):
    with Session(engine) as session:
        for origin in STATES[kind]:
            for gate in [state for state in GATES if state in STATES[kind]]:
                for actor in AGENTS:
                    item = _item(session, kind, origin)

                    with pytest.raises(TransitionError):
                        move(session, item, gate, actor)

                    assert _stored_state(session, item) == origin
                    assert session.scalar(select(func.count()).select_from(StateChange)) == 0


@pytest.mark.parametrize("origin", ["triaged", "later", "screened_out"])
def test_accepting_an_offer_creates_its_application_in_the_same_transaction(
    engine: Engine, origin: str
):
    with Session(engine) as session:
        offer = _offer(session, origin)
        session.commit()
        offer_id = offer.id

        move(session, offer, "accepted", Actor.CANDIDATE)
        session.rollback()

        assert _stored_state(session, offer) == origin
        assert session.scalars(select(Application)).all() == []

        move(session, offer, "accepted", Actor.CANDIDATE)
        session.commit()

    with Session(engine) as session:
        application = session.scalars(select(Application)).one()
        assert (application.offer_id, application.state) == (offer_id, "created")
        changes = _changes(session, application)
        assert [(c.from_state, c.to_state, c.actor) for c in changes] == [
            (None, "created", "candidate")
        ]


@pytest.mark.parametrize(
    ("kind", "origin", "to", "actor"),
    [
        ("offer", "described", "triaged", "triage"),
        ("offer", "triaged", "accepted", "candidate"),
        ("application", "drafted", "answers_checked", "candidate"),
    ],
)
def test_a_second_session_moving_from_the_same_read_state_is_refused_as_stale(
    engine: Engine, kind: str, origin: str, to: str, actor: str
):
    with Session(engine) as setup:
        item = _item(setup, kind, origin)
        item_id, model = item.id, type(item)
        setup.commit()

    with Session(engine) as first, Session(engine) as second:
        first_item = first.get_one(model, item_id)
        second_item = second.get_one(model, item_id)
        assert first_item.state == second_item.state == origin

        move(first, first_item, to, actor)
        first.commit()
        with pytest.raises(StaleStateError) as error:
            move(second, second_item, to, actor)
        second.rollback()

        for name in (origin, to, actor):
            assert name in str(error.value)

    with Session(engine) as session:
        item = session.get_one(model, item_id)
        assert item.state == to
        assert [c.to_state for c in _changes(session, item) if c.from_state == origin] == [to]
        applications = session.scalar(select(func.count()).select_from(Application))
        assert applications == (1 if kind == "application" or to == "accepted" else 0)


@pytest.mark.parametrize(
    ("kind", "terminal"),
    [(kind, state) for kind, states in TERMINAL.items() for state in states],
)
def test_sent_declined_and_dropped_are_terminal(engine: Engine, kind: str, terminal: str):
    with Session(engine) as session:
        for to in STATES[kind]:
            for actor in ACTORS:
                item = _item(session, kind, terminal)

                with pytest.raises(TransitionError):
                    move(session, item, to, actor)

                assert _stored_state(session, item) == terminal


def test_a_state_assigned_outside_move_is_refused_at_flush(engine: Engine):
    with Session(engine) as session:
        offer = _offer(session, "triaged")
        session.commit()

        offer.state = "accepted"
        with pytest.raises(ValueError, match="move"):
            session.flush()
        session.rollback()

        assert _stored_state(session, offer) == "triaged"


def _core_update(item: Item, form: str, run: str, column: str, value: object) -> tuple[Any, Any]:
    """A Core UPDATE of item that sets column to value, and the parameters to run it with.

    form "values" sets it in the statement; "parameters" in the parameters it runs with.
    """
    model = type(item)
    if form == "values":
        return update(model).where(model.id == item.id).values({column: value}), None
    if run == "session":  # the ORM's bulk update by primary key
        return update(model), [{"id": item.id, column: value}]
    statement = update(model).where(model.id == bindparam("item_id"))
    return statement, [{"item_id": item.id, column: value}]


@pytest.mark.parametrize(
    ("kind", "origin", "gate"),
    [("offer", "triaged", "accepted"), ("application", "drafted", "sent")],
)
@pytest.mark.parametrize("form", ["values", "parameters"])
@pytest.mark.parametrize("run", ["session", "connection"])
def test_a_core_update_setting_a_state_outside_move_is_refused(
    engine: Engine, kind: str, origin: str, gate: str, form: str, run: str
):
    with Session(engine) as session:
        item = _item(session, kind, origin)
        session.commit()
        model, item_id = type(item), item.id

        def execute(column: str, value: object) -> None:
            statement, parameters = _core_update(item, form, run, column, value)
            if run == "session":
                session.execute(statement, parameters)
            else:
                session.connection().execute(statement, parameters)

        with pytest.raises(ValueError, match="move"):
            execute("state", gate)
        session.rollback()

        assert _stored_state(session, item) == origin
        assert session.scalar(select(func.count()).select_from(StateChange)) == 0

        # The guard is about the state: the same UPDATE of another column goes through.
        execute("user_id", 2)
        session.commit()
        assert session.scalar(select(model.user_id).where(model.id == item_id)) == 2
