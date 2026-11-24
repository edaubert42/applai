"""The full text of LinkedIn offers, read through the candidate's LinkedIn MCP server.

describe_linkedin_offers() reads one new LinkedIn offer per run with the server's
get_job_details tool, then moves it new → described, like applai.details does for public
pages. The server is the command line in APPLAI_LINKEDIN_MCP, started for the call over
stdio with only the MCP SDK's safe environment variables. A call that fails, or gives no
text, records its error on the offer, which stays new and is read again on a later run,
after the offers never tried.

The same server has tools that act on the candidate's account (connection requests, Easy
Apply): call_tool() refuses every tool but get_job_details (ADR 0003). Reads are paced:
at most MAX_READS_PER_DAY per UTC day, at least MIN_INTERVAL apart, failed calls included;
an offer over the limit stays new with no error, and is read on a later run.
"""

import json
import os
import re
import shlex
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlsplit

import anyio
from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.types import TextContent
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from applai.details import Describing
from applai.models import LOCAL_USER_ID, LinkedInRead, Offer, OfferOrigin, OfferState
from applai.orchestrator import Actor, move

COMMAND_VARIABLE = "APPLAI_LINKEDIN_MCP"
TOOL = "get_job_details"  # the only tool ApplAI calls on the server
MAX_READS_PER_DAY = 30  # module constants until a settings issue
MIN_INTERVAL = timedelta(minutes=2)
TIMEOUT = 120.0  # seconds, the server's start included: it may open a browser
_JOB_ID = re.compile(r"/jobs/view/(?:[^/]*-)?(\d+)/?$")


class LinkedInError(Exception):
    """A LinkedIn offer that cannot be read through the MCP server."""


class ToolRefused(Exception):
    """A call to a tool ApplAI never calls on the LinkedIn MCP server."""


def call_tool(command: str, tool: str, arguments: dict[str, Any], timeout: float = TIMEOUT) -> str:
    """Start the server command, call tool with arguments, return the text it gives.

    Refuses, before starting anything, every tool but get_job_details. Raises LinkedInError
    when the server cannot be started, fails or does not answer in time.
    """
    if tool != TOOL:
        raise ToolRefused(f"ApplAI calls only {TOOL} on the LinkedIn MCP server, not {tool}")
    try:
        argv = shlex.split(command)
    except ValueError:
        argv = []
    if not argv:
        raise LinkedInError(f"{COMMAND_VARIABLE} is not a command line")
    try:
        return anyio.run(_call, argv, tool, arguments, timeout)
    except LinkedInError:
        raise
    except Exception as error:  # messages may hold the command line, which may hold a token
        cause = _single(error)
        if isinstance(cause, LinkedInError):
            raise cause from None
        if isinstance(cause, TimeoutError):
            raise LinkedInError("the LinkedIn MCP server did not answer in time") from None
        if isinstance(cause, OSError):
            raise LinkedInError("the LinkedIn MCP server could not be started") from None
        raise LinkedInError(f"the LinkedIn MCP server failed: {type(cause).__name__}") from None


def describe_linkedin_offers(
    session: Session, now: datetime | None = None, user_id: int = LOCAL_USER_ID
) -> Describing:
    """Describe at most one new LinkedIn offer ApplAI found, if the limits allow; commits.

    now is a UTC time without a time zone, the current one by default.
    """
    describing = Describing()
    now = now or datetime.now(UTC).replace(tzinfo=None)
    offers = [offer for offer in _new_offers(session, user_id) if _is_linkedin(offer.url)]
    command = os.environ.get(COMMAND_VARIABLE, "").strip()
    if offers and not command:
        message = f"{COMMAND_VARIABLE} is not set: it starts the LinkedIn MCP server"
        for offer in offers:
            offer.detail_error = message
            offer.detail_failed_at = now
        describing.errors.append(message)  # once, not once per offer
        session.commit()
        return describing
    for offer in offers:
        match = _JOB_ID.search(urlsplit(offer.url).path)
        if match is None:
            _fail(describing, offer, "the LinkedIn URL has no job id", now)
            continue
        if _may_read(session, user_id, now):
            _describe(session, offer, command, match.group(1), now, describing)
        break
    session.commit()
    return describing


def _describe(
    session: Session, offer: Offer, command: str, job_id: str, now: datetime, into: Describing
) -> None:
    # The read counts before the call: a call that fails still reached LinkedIn.
    session.add(LinkedInRead(user_id=offer.user_id, offer_id=offer.id, read_at=now))
    session.commit()
    try:
        offer.description = _posting(call_tool(command, TOOL, {"job_id": job_id}))
    except LinkedInError as error:
        _fail(into, offer, str(error), now)
        return
    offer.detail_error = None
    move(session, offer, OfferState.DESCRIBED, Actor.WATCH)
    into.described += 1


def _new_offers(session: Session, user_id: int) -> Sequence[Offer]:
    """New offers ApplAI found, those never failed first, then by age."""
    return session.scalars(
        select(Offer)
        .where(
            Offer.user_id == user_id,
            Offer.state == OfferState.NEW,
            Offer.origin == OfferOrigin.APPLAI,  # the old workflow's are read only
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
    last = session.scalar(
        select(func.max(LinkedInRead.read_at)).where(LinkedInRead.user_id == user_id)
    )
    within_interval = last is None or now - last >= MIN_INTERVAL
    return reads_today(session, now, user_id) < MAX_READS_PER_DAY and within_interval


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


async def _call(argv: list[str], tool: str, arguments: dict[str, Any], timeout: float) -> str:
    server = StdioServerParameters(command=argv[0], args=argv[1:])
    with anyio.fail_after(timeout):
        async with stdio_client(server) as (read, write), ClientSession(read, write) as client:
            await client.initialize()
            result = await client.call_tool(tool, arguments)
    if result.is_error:
        raise LinkedInError("the LinkedIn MCP server could not read the offer")
    return "".join(block.text for block in result.content if isinstance(block, TextContent))


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
