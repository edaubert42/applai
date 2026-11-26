"""The full text of LinkedIn offers, read through the candidate's LinkedIn MCP server.

describe_linkedin_offers() reads every new LinkedIn offer, oldest first, with the server's
get_job_details tool, and moves each new → described, like applai.details does for public
pages. The server is the command line in APPLAI_LINKEDIN_MCP, started for each call over
stdio with only the MCP SDK's safe environment variables. A call that fails, or gives no
text, records its error on the offer, which stays new and is read again on a later run,
after the offers never tried; after FAILURES_IN_A_ROW of them the run stops reading.

The same server has tools that act on the candidate's account (connection requests, Easy
Apply): call_tool() refuses every tool but get_job_details (ADR 0003). Reads are paced:
at most MAX_READS_PER_DAY per UTC day, at least MIN_INTERVAL apart, failed calls included;
the run waits out the interval holding no transaction, and stops at the daily limit. An
offer it did not reach stays new with no error, and is read on a later run.
"""

import json
import os
import re
import shlex
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import ExitStack, asynccontextmanager, closing
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlsplit

import anyio
from anyio.from_thread import BlockingPortal, start_blocking_portal
from mcp import ClientSession, MCPError, StdioServerParameters, stdio_client
from mcp.types import CONNECTION_CLOSED, CallToolResult, TextContent
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from applai.details import Describing
from applai.models import LOCAL_USER_ID, LinkedInRead, Offer, OfferOrigin, OfferState
from applai.orchestrator import Actor, TransitionError, move

COMMAND_VARIABLE = "APPLAI_LINKEDIN_MCP"
TOOL = "get_job_details"  # the only tool ApplAI calls on the server
MAX_READS_PER_DAY = 50  # module constants until a settings issue
MIN_INTERVAL = timedelta(seconds=10)
FAILURES_IN_A_ROW = 3  # failed calls after which a run stops: LinkedIn may be limiting reads
STOPPED = (
    f"{FAILURES_IN_A_ROW} calls failed in a row, so the scan stopped reading LinkedIn:"
    " LinkedIn may be limiting the account"
)
TIMEOUT = 120.0  # seconds, the server's start included: it may open a browser
_JOB_ID = re.compile(r"/jobs/view/(?:[^/]*-)?(\d+)/?$")

type Progress = Callable[[int, int], None]  # offers a run went through, and offers it has


class LinkedInError(Exception):
    """A LinkedIn offer that cannot be read through the MCP server."""


class ToolRefused(Exception):
    """A call to a tool ApplAI never calls on the LinkedIn MCP server."""


class LinkedInServer:
    """The server command, started on the first call and kept open for the next until closed.

    A server that fails a call, dies or does not answer in time is stopped, and started again
    on the next call: one start per call at most. Its event loop runs in a thread of its own,
    so the caller may wait between calls holding nothing open but the server.
    """

    def __init__(self, command: str, timeout: float = TIMEOUT) -> None:
        self._command = command
        self._timeout = timeout
        self._open = ExitStack()
        self._portal: BlockingPortal | None = None
        self._client: ClientSession | None = None

    def call_tool(self, tool: str, arguments: dict[str, Any]) -> str:
        """Call tool with arguments, starting the server if needed; return the text it gives.

        Refuses, before starting or calling anything, every tool but get_job_details. Raises
        LinkedInError when the server cannot be started, fails or does not answer in time.
        """
        if tool != TOOL:
            raise ToolRefused(f"ApplAI calls only {TOOL} on the LinkedIn MCP server, not {tool}")
        try:
            argv = shlex.split(self._command)
        except ValueError:
            argv = []
        if not argv:
            raise LinkedInError(f"{COMMAND_VARIABLE} is not a command line")
        try:
            if self._portal is None or self._client is None:
                self._portal = self._open.enter_context(start_blocking_portal())
                self._client = self._open.enter_context(
                    self._portal.wrap_async_context_manager(_session(argv, self._timeout))
                )
            result = self._portal.call(_call, self._client, tool, arguments, self._timeout)
        except Exception as error:  # messages may hold the command line, which may hold a token
            self.close()
            raise _failure(error) from None
        if result.is_error:  # the server answered: it stays open for the next call
            raise LinkedInError("the LinkedIn MCP server could not read the offer")
        return _payload([block.text for block in result.content if isinstance(block, TextContent)])

    def close(self) -> None:
        """Stop the server, if started; the next call starts it again."""
        opened, self._open = self._open, ExitStack()
        self._portal = self._client = None
        try:
            opened.close()
        except Exception:  # a dead server's own errors on the way out: its process is stopped
            pass


def call_tool(command: str, tool: str, arguments: dict[str, Any], timeout: float = TIMEOUT) -> str:
    """Start the server command, call tool with arguments, stop the server, return the text.

    Refuses, before starting anything, every tool but get_job_details. Raises LinkedInError
    when the server cannot be started, fails or does not answer in time.
    """
    with closing(LinkedInServer(command, timeout)) as server:
        return server.call_tool(tool, arguments)


def _failure(error: Exception) -> LinkedInError:
    """The LinkedInError to raise for an error starting or calling the server."""
    cause = _single(error)
    if isinstance(cause, LinkedInError):
        return cause
    if isinstance(cause, TimeoutError):
        return LinkedInError("the LinkedIn MCP server did not answer in time")
    if isinstance(cause, OSError):
        return LinkedInError("the LinkedIn MCP server could not be started")
    if isinstance(cause, MCPError) and cause.error.code == CONNECTION_CLOSED:
        return LinkedInError("the LinkedIn MCP server stopped")
    return LinkedInError(f"the LinkedIn MCP server failed: {type(cause).__name__}")


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def describe_linkedin_offers(
    session: Session,
    clock: Callable[[], datetime] = _utc_now,
    sleep: Callable[[float], None] = time.sleep,
    user_id: int = LOCAL_USER_ID,
    progress: Progress | None = None,
    stop: Callable[[], bool] | None = None,
    described: Callable[[int], None] | None = None,
) -> Describing:
    """Describe the new LinkedIn offers ApplAI found, as far as the limits allow; commits each.

    clock gives UTC times without a time zone; sleep waits a number of seconds. progress,
    when given, hears how many offers the run went through, after each one. stop, when given,
    is checked before each offer and around waits so a running scan can stop cleanly.
    described, when given, hears the id of each offer this run described, once committed.
    """
    describing = Describing()
    offers = [offer for offer in _new_offers(session, user_id) if _is_linkedin(offer.url)]
    command = os.environ.get(COMMAND_VARIABLE, "").strip()
    if offers and not command:
        message = f"{COMMAND_VARIABLE} is not set: it starts the LinkedIn MCP server"
        for offer in offers:
            offer.detail_error = message
            offer.detail_failed_at = clock()
        describing.errors.append(message)  # once, not once per offer
        session.commit()
        return describing
    failures = 0
    if progress and offers:
        progress(0, len(offers))  # the first call may take a while: the server opens a browser
    with closing(LinkedInServer(command)) as server:  # started by the first call, if any
        for done, offer in enumerate(offers, start=1):
            if stop and stop():
                break
            match = _JOB_ID.search(urlsplit(offer.url).path)
            if match is None:
                _fail(describing, offer, "the LinkedIn URL has no job id", clock())
            else:
                now = _wait_turn(session, user_id, clock, sleep, stop)
                if now is None:
                    break  # the daily limit or stopped: the offers left wait for a later run
                if offer.state == OfferState.NEW:  # reloaded: another part of ApplAI moves offers
                    read = _describe(session, offer, server, match.group(1), now, describing)
                    failures = 0 if read else failures + 1
            session.commit()
            if described and offer.state == OfferState.DESCRIBED:
                described(offer.id)
            if progress:
                progress(done, len(offers))
            if failures == FAILURES_IN_A_ROW:
                describing.errors.append(STOPPED)
                break
    session.commit()
    return describing


def read_linkedin_offer(session: Session, offer: Offer, url: str, now: datetime) -> str:
    """Read one LinkedIn offer URL through the MCP server within pacing; return its text."""
    command = os.environ.get(COMMAND_VARIABLE, "").strip()
    if not command:
        raise LinkedInError(f"{COMMAND_VARIABLE} is not set: it starts the LinkedIn MCP server")
    match = _JOB_ID.search(urlsplit(url).path)
    if match is None:
        raise LinkedInError("the LinkedIn URL has no job id")
    if not _may_read(session, offer.user_id, now):
        raise LinkedInError("LinkedIn's daily limit or read interval is reached")
    session.add(LinkedInRead(user_id=offer.user_id, offer_id=offer.id, read_at=now))
    session.commit()
    return _posting(call_tool(command, TOOL, {"job_id": match.group(1)}))


def _wait_turn(
    session: Session,
    user_id: int,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
    stop: Callable[[], bool] | None = None,
) -> datetime | None:
    """Wait until a read is MIN_INTERVAL after the last one; return its time, or None at the
    daily limit or when stopped. No transaction stays open while waiting."""
    while True:
        if stop and stop():
            return None
        now = clock()
        if reads_today(session, now, user_id) >= MAX_READS_PER_DAY:
            return None
        last = _last_read(session, user_id)
        wait = 0.0 if last is None else (last + MIN_INTERVAL - now).total_seconds()
        session.commit()
        if wait <= 0:
            return now
        sleep(wait)


def _describe(
    session: Session,
    offer: Offer,
    server: LinkedInServer,
    job_id: str,
    now: datetime,
    into: Describing,
) -> bool:
    """Read the offer through the server; return whether the call succeeded."""
    # The read counts before the call: a call that fails still reached LinkedIn.
    session.add(LinkedInRead(user_id=offer.user_id, offer_id=offer.id, read_at=now))
    session.commit()
    try:
        description = _posting(server.call_tool(TOOL, {"job_id": job_id}))
    except LinkedInError as error:
        _fail(into, offer, str(error), now)
        return False
    try:
        move(session, offer, OfferState.DESCRIBED, Actor.WATCH)
    except TransitionError:  # moved by the rest of ApplAI during the call: it keeps its state
        return True
    offer.description, offer.detail_error = description, None
    into.described += 1
    return True


def _new_offers(session: Session, user_id: int) -> Sequence[Offer]:
    """New offers ApplAI found, those never failed first, then by age."""
    return session.scalars(
        select(Offer)
        .where(
            Offer.user_id == user_id,
            Offer.state == OfferState.NEW,
            Offer.origin != OfferOrigin.OLD_WORKFLOW,  # the old workflow's are read only
        )
        .order_by(Offer.detail_error.is_not(None), Offer.id)
    ).all()


def reads_today(session: Session, now: datetime, user_id: int = LOCAL_USER_ID) -> int:
    """The reads made on now's UTC day, failed calls included; now has no time zone."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        session.scalar(
            select(func.count())
            .select_from(LinkedInRead)
            .where(LinkedInRead.user_id == user_id, LinkedInRead.read_at >= day)
        )
        or 0
    )


def _may_read(session: Session, user_id: int, now: datetime) -> bool:
    """Whether a read now stays within the daily limit and the interval between reads."""
    last = _last_read(session, user_id)
    within_interval = last is None or now - last >= MIN_INTERVAL
    return reads_today(session, now, user_id) < MAX_READS_PER_DAY and within_interval


def _last_read(session: Session, user_id: int) -> datetime | None:
    return session.scalar(
        select(func.max(LinkedInRead.read_at)).where(LinkedInRead.user_id == user_id)
    )


def _posting(text: str) -> str:
    """The posting's text in get_job_details's JSON; raises LinkedInError when it has none."""
    try:
        details = json.loads(text)
    except json.JSONDecodeError:
        raise LinkedInError("the LinkedIn MCP server did not answer in JSON") from None
    posting = _at(details, "sections", "job_posting")
    if isinstance(posting, str) and posting.strip():
        return posting.strip()  # kept even with a section error: maybe incomplete, still text
    kind = _at(details, "section_errors", "job_posting", "error_type")
    reason = f" ({kind})" if isinstance(kind, str) and kind else ""
    raise LinkedInError(f"LinkedIn gave no text for the offer{reason}")


@asynccontextmanager
async def _session(argv: list[str], timeout: float) -> AsyncIterator[ClientSession]:
    """The started server, open until the block ends; starting it fails after timeout."""
    server = StdioServerParameters(command=argv[0], args=argv[1:])
    async with stdio_client(server) as (read, write), ClientSession(read, write) as client:
        with anyio.fail_after(timeout):
            await client.initialize()
        yield client


async def _call(
    client: ClientSession, tool: str, arguments: dict[str, Any], timeout: float
) -> CallToolResult:
    with anyio.fail_after(timeout):
        return await client.call_tool(tool, arguments)


def _payload(texts: list[str]) -> str:
    """The JSON object block when the server adds extra text (e.g. an update notice), else all."""
    for text in texts:
        try:
            if isinstance(json.loads(text), dict):
                return text
        except json.JSONDecodeError:
            continue
    return "".join(texts)


def _fail(describing: Describing, offer: Offer, error: str, failed_at: datetime) -> None:
    """Record on the offer why it could not be read; it stays new."""
    offer.detail_error = error
    offer.detail_failed_at = failed_at
    describing.errors.append(f"offer {offer.id}: {error}")


def _is_linkedin(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == "linkedin.com" or host.endswith(".linkedin.com")


def _single(error: BaseException) -> BaseException:
    """The one exception inside nested exception groups, or the group itself."""
    while isinstance(error, BaseExceptionGroup):
        inner: tuple[BaseException, ...] = cast(BaseExceptionGroup[BaseException], error).exceptions
        if len(inner) != 1:
            break
        error = inner[0]
    return cast(BaseException, error)


def _at(value: Any, *keys: str) -> Any:
    for key in keys:
        value = cast(dict[str, Any], value).get(key) if isinstance(value, dict) else None
    return value
