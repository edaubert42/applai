"""The scan: the watch finding new offers without the candidate asking.

run_scan() reads the job alerts (applai.mail), then screens each new offer and reads its
page (applai.screening, applai.details), reads the screened LinkedIn offers through the
candidate's LinkedIn MCP server within its pacing (applai.linkedin), and triages each offer
as soon as it is read (applai.triage), so a stopped scan keeps every triage it made; at the
end it triages the described offers read before or by the extension. JobTeaser's pages
are left to the ApplAI extension (applai.browser).
A failure is recorded in plain words and the scan goes on: one offer's never stops the
others. Without the profile nothing is screened, so nothing is read or triaged unscreened.

Scanner runs one scan at a time: at each of scan_times while applai serve runs, at startup
when the last slot has no scan, and by hand, refused for COOLDOWN after a scan without
errors. Every time comes from its clock: tests move the clock instead of waiting.
"""

import math
import threading
import time as time_module
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import httpx2
from pydantic import BaseModel, Field
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.agents import AgentError, AgentRunner, AlreadyRunning
from applai.details import PageError, describe_offer, on_linkedin, read_by_extension
from applai.linkedin import MAX_READS_PER_DAY, Progress, describe_linkedin_offers, reads_today
from applai.mail import CLIENT_ID_VARIABLE, MailAuthError, Mailbox, MailError, read_alerts
from applai.models import LOCAL_USER_ID, Offer, OfferOrigin, OfferState, Scan
from applai.orchestrator import TransitionError
from applai.profile import ProfileError, load_profile
from applai.screening import screen_offer
from applai.triage import TriageError, triage_offer

SCAN_TIMES = ("08:30", "17:30")  # the candidate's local time
COOLDOWN = timedelta(minutes=30)
CHECK_EVERY_S = 300.0  # after the machine slept, a missed slot runs this late at most
# Errors whose message is written for the candidate; another one is named by its type only.
_PLAIN = (MailError, PageError, TriageError, AgentError, TransitionError)

type Clock = Callable[[], datetime]  # the time now, aware, in the candidate's time zone
type ServiceReport = Callable[[str], None]  # service id currently used by a running scan


def local_now() -> datetime:
    """The time now in this machine's time zone, the candidate's."""
    return datetime.now().astimezone()


@dataclass(frozen=True)
class Watch:
    """What a scan reads with: the network, the agents, ApplAI's application id at Microsoft,
    and how it waits between two LinkedIn reads."""

    http: httpx2.Client
    runner: AgentRunner
    ms_client_id: str | None
    sleep: Callable[[float], None] = time_module.sleep


@dataclass
class Found:
    new_offers: int = 0
    errors: list[str] = field(default_factory=list[str])


def run_scan(
    engine: Engine,
    data_dir: Path,
    watch: Watch,
    user_id: int = LOCAL_USER_ID,
    clock: Clock = local_now,
    progress: Progress | None = None,
    on_service: ServiceReport | None = None,
    cancel: threading.Event | None = None,
) -> Found:
    """Find the new offers, and take each as far as Triage; return what the scan found.

    progress hears how far the LinkedIn reads went, after each offer; on_service hears each
    service id the scan uses as it reaches it; cancel, when set, stops cleanly after the
    current item.
    """
    report: ServiceReport = on_service or _ignore_service
    stopped = cancel.is_set if cancel is not None else _never_stop
    found = Found()
    report("mailbox")
    found.new_offers = _read_mail(engine, data_dir, watch, user_id, found.errors)
    if stopped():
        return found
    try:
        profile = load_profile(data_dir)
    except ProfileError as error:
        found.errors.append(f"No offer was screened: {error}")
        return found
    tried: set[int] = set()

    def triage(offer_id: int) -> None:
        tried.add(offer_id)
        report("triage")
        try:
            triage_offer(watch.runner, engine, offer_id, profile)
        except AlreadyRunning:  # its page's Triage runs (applai.browser) and keeps the card
            pass
        except Exception as error:
            found.errors.append(f"Offer {offer_id} was not triaged: {_plain(error)}.")

    for offer_id in _offers(engine, OfferState.NEW, user_id):
        if stopped():
            return found
        with Session(engine) as session:
            offer = session.get_one(Offer, offer_id)
            try:
                url = offer.url  # LinkedIn's and the extension's have their own readers
                if screen_offer(session, offer, profile) is None and not (
                    on_linkedin(url) or read_by_extension(url)
                ):
                    if offer.source:
                        report(offer.source)
                    describe_offer(session, offer, data_dir, watch.http)
            except PageError as error:  # recorded on the offer too: committed below
                found.errors.append(f"Offer {offer_id}: its page was not read: {error}.")
            except Exception as error:
                session.rollback()
                found.errors.append(f"Offer {offer_id} was not screened nor read: {_plain(error)}.")
            session.commit()
            read = offer.state == OfferState.DESCRIBED
        if read:  # triaged at once, so a stop keeps the triage of every offer read
            triage(offer_id)
    if stopped():
        return found
    sleep = _cancellable_sleep(watch.sleep, cancel)
    _read_linkedin(engine, user_id, clock, sleep, progress, report, stopped, found.errors, triage)
    if stopped():
        return found
    for offer_id in _offers(engine, OfferState.DESCRIBED, user_id):  # read before, or elsewhere
        if stopped():
            return found
        if offer_id not in tried:
            triage(offer_id)
    return found


def _ignore_service(_service: str) -> None:
    pass


def _never_stop() -> bool:
    return False


def _cancellable_sleep(
    sleep: Callable[[float], None], cancel: threading.Event | None
) -> Callable[[float], None]:
    """Wake a real sleep immediately when cancel is set; keep a test clock's sleep untouched."""
    if cancel is not None and sleep is time_module.sleep:
        event = cancel

        def _wait(seconds: float) -> None:
            event.wait(seconds)

        return _wait
    return sleep


def _read_mail(
    engine: Engine, data_dir: Path, watch: Watch, user_id: int, errors: list[str]
) -> int:
    if not watch.ms_client_id:
        errors.append(
            f"Outlook is not set up: set {CLIENT_ID_VARIABLE}, then run applai mail login."
        )
        return 0
    try:
        reading = read_alerts(engine, Mailbox(data_dir, watch.ms_client_id, watch.http), user_id)
    except MailAuthError:
        errors.append("Outlook connection expired: run applai mail login.")
        return 0
    except httpx2.HTTPError:
        errors.append("Outlook could not be reached: the next scan tries again.")
        return 0
    except Exception as error:
        errors.append(f"Outlook could not be read: {_plain(error)}.")
        return 0
    errors.extend(f"A job alert was not read: {error}." for error in reading.errors)
    return reading.offers


def _read_linkedin(
    engine: Engine,
    user_id: int,
    clock: Clock,
    sleep: Callable[[float], None],
    progress: Progress | None,
    report: ServiceReport,
    stop: Callable[[], bool],
    errors: list[str],
    described: Callable[[int], None],
) -> None:
    """Read the screened LinkedIn offers, as far as the reader's pacing allows."""

    def on_progress(done: int, total: int) -> None:
        report("alert_linkedin")
        if progress:
            progress(done, total)

    try:
        with Session(engine) as session:
            describing = describe_linkedin_offers(
                session, lambda: _utc(clock()), sleep, user_id, on_progress, stop, described
            )
    except Exception as error:
        errors.append(f"LinkedIn: the reads stopped: {_plain(error)}.")  # the read ones stay
        return
    errors.extend(f"LinkedIn: {error}." for error in describing.errors)


def _offers(engine: Engine, state: OfferState, user_id: int) -> list[int]:
    with Session(engine) as session:
        return list(session.scalars(select(Offer.id).where(
            Offer.user_id == user_id, Offer.state == state, Offer.origin != OfferOrigin.OLD_WORKFLOW
        ).order_by(Offer.id)))  # fmt: skip


def _plain(error: Exception) -> str:
    # A message from elsewhere may hold a URL with a token, or the candidate's data.
    return str(error).rstrip(".") if isinstance(error, _PLAIN) else type(error).__name__


class ScanRefused(Exception):
    """A scan that cannot start now; seconds_left says when one can, when time is the reason."""

    def __init__(self, message: str, seconds_left: int | None = None) -> None:
        super().__init__(message)
        self.seconds_left = seconds_left


class LastScan(BaseModel):
    started_at: datetime
    finished_at: datetime | None  # none: running, or ApplAI stopped during it
    by_hand: bool
    new_offers: int
    errors: list[str]


class LinkedInReadProgress(BaseModel):
    read: int  # the LinkedIn offers the running scan went through, read or failed
    total: int  # the new LinkedIn offers it had to read


class ScanStatus(BaseModel):
    running: bool
    stopping: bool = False
    active_service: str | None = None
    services_used: list[str] = Field(default_factory=list)
    last_scan: LastScan | None
    next_slot: datetime | None
    manual_scan_in_s: int | None  # seconds before a scan by hand is allowed; none: now
    linkedin_reads_today: int
    linkedin_reads_per_day: int
    linkedin_progress: LinkedInReadProgress | None  # none: no scan is reading LinkedIn


class Scanner:
    """Runs scan, one at a time, at the slots of scan_times and by hand; records each in scans.

    scan is given callbacks for LinkedIn progress and active services, plus a cancel event.
    """

    def __init__(
        self,
        engine: Engine,
        scan: Callable[[Progress, ServiceReport, threading.Event], Found],
        clock: Clock = local_now,
        scan_times: Sequence[str] = SCAN_TIMES,
        user_id: int = LOCAL_USER_ID,
    ) -> None:
        self._engine, self._scan, self._clock, self._user_id = engine, scan, clock, user_id
        self._times = [time.fromisoformat(slot) for slot in scan_times]
        self._running = threading.Lock()  # held from a scan's start to its end
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._schedule: threading.Thread | None = None
        self._linkedin: LinkedInReadProgress | None = None  # replaced whole: read by other threads
        self._active_service: str | None = None
        self._services_used: tuple[str, ...] = ()

    def start(self, by_hand: bool = True) -> int:
        """Start a scan in the background and return its id, or raise ScanRefused."""
        if not self._running.acquire(blocking=False):
            raise ScanRefused("A scan is running.")
        try:
            now = _utc(self._clock())
            with Session(self._engine) as session:
                if by_hand:
                    _refuse_if_too_soon(self._last(session), now)
                scan = Scan(user_id=self._user_id, by_hand=by_hand, started_at=now)
                session.add(scan)
                session.commit()
                scan_id = scan.id
        except BaseException:
            self._running.release()
            raise
        self._cancel.clear()
        self._active_service = None
        self._services_used = ()
        self._thread = threading.Thread(target=self._run, args=(scan_id,), daemon=True)
        self._thread.start()
        return scan_id

    def request_stop(self) -> bool:
        """Ask the running scan to stop cleanly; return False when no scan is running."""
        if not self._running.locked():
            return False
        self._cancel.set()
        return True

    def tick(self) -> int | None:
        """Start the scan of the last slot if it has none; return its id."""
        slot = max(self._slots(self._clock(), before=True), default=None)
        if slot is None:
            return None
        with Session(self._engine) as session:
            done = session.scalar(select(Scan.id).where(  # a scan cut off by a stop did not
                Scan.user_id == self._user_id, Scan.started_at >= _utc(slot),
                Scan.finished_at.is_not(None),
            ).limit(1))  # fmt: skip
        if done is not None:
            return None
        try:
            return self.start(by_hand=False)
        except ScanRefused:  # one is running; the next tick sees whether it covered the slot
            return None

    def wait(self, timeout: float | None = None) -> None:
        """Wait for the scan running, if any, to end."""
        if self._thread:
            self._thread.join(timeout)

    def start_schedule(self) -> None:
        """Scan now if the last slot was missed, then at each slot until stop_schedule()."""
        self.tick()
        self._schedule = threading.Thread(target=self._keep_schedule, daemon=True)
        self._schedule.start()

    def stop_schedule(self) -> None:
        """Stop the schedule and wake any sleeping scan."""
        self._stop.set()
        self._cancel.set()
        if self._schedule:
            self._schedule.join()

    def status(self) -> ScanStatus:
        with Session(self._engine) as session:
            last = self._last(session)
            running = self._running.locked()
            return ScanStatus(
                running=running,
                stopping=running and self._cancel.is_set(),
                active_service=self._active_service if running else None,
                services_used=list(self._services_used) if running else [],
                last_scan=None if last is None else LastScan(
                    started_at=last.started_at.replace(tzinfo=UTC),
                    finished_at=last.finished_at.replace(tzinfo=UTC) if last.finished_at else None,
                    by_hand=last.by_hand, new_offers=last.new_offers, errors=last.errors,
                ),
                next_slot=min(self._slots(self._clock(), before=False), default=None),
                manual_scan_in_s=_cooldown_left(last, _utc(self._clock())),
                linkedin_reads_today=reads_today(session, _utc(self._clock()), self._user_id),
                linkedin_reads_per_day=MAX_READS_PER_DAY,
                linkedin_progress=self._linkedin,
            )  # fmt: skip

    def _keep_schedule(self) -> None:
        while not self._stop.wait(self._seconds_to_next_check()):
            self.tick()

    def _seconds_to_next_check(self) -> float:
        now = self._clock()
        slot = min(self._slots(now, before=False), default=None)
        until = (slot - now).total_seconds() if slot else CHECK_EVERY_S
        return max(0.0, min(until, CHECK_EVERY_S))

    def _slots(self, now: datetime, before: bool) -> Iterator[datetime]:
        """The slots of yesterday, today and tomorrow up to now, or after it."""
        for days in (-1, 0, 1):
            day = now.date() + timedelta(days=days)
            for at in self._times:
                # local_now()'s offset is today's: across a change of summer time, yesterday's
                # and tomorrow's slots are an hour off, which only delays or advances one scan.
                slot = datetime.combine(day, at, tzinfo=now.tzinfo)
                if (slot <= now) == before:
                    yield slot

    def _last(self, session: Session) -> Scan | None:
        latest = Scan.started_at.desc(), Scan.id.desc()
        return session.scalar(
            select(Scan).where(Scan.user_id == self._user_id).order_by(*latest).limit(1)
        )

    def _run(self, scan_id: int) -> None:
        try:
            try:
                found = self._scan(self._report_linkedin, self._report_service, self._cancel)
            except Exception as error:
                found = Found(errors=[f"The scan stopped: {_plain(error)}."])
            with Session(self._engine) as session:
                scan = session.get_one(Scan, scan_id)
                scan.finished_at = _utc(self._clock())
                scan.new_offers, scan.errors = found.new_offers, found.errors
                session.commit()
        finally:
            self._linkedin = None
            self._active_service = None
            self._services_used = ()
            self._cancel.clear()
            self._running.release()

    def _report_linkedin(self, read: int, total: int) -> None:
        self._linkedin = LinkedInReadProgress(read=read, total=total)

    def _report_service(self, service: str) -> None:
        self._active_service = service
        if service not in self._services_used:
            self._services_used = (*self._services_used, service)


def _cooldown_left(last: Scan | None, now: datetime) -> int | None:
    """The seconds before a scan by hand is allowed, as the database keeps times; none: now."""
    if last is None or last.finished_at is None or last.errors:
        return None
    left = COOLDOWN - (now - last.finished_at)
    return math.ceil(left.total_seconds()) if left > timedelta(0) else None


def _refuse_if_too_soon(last: Scan | None, now: datetime) -> None:
    seconds_left = _cooldown_left(last, now)
    if seconds_left is not None:
        ago = (int(COOLDOWN.total_seconds()) - seconds_left) // 60
        raise ScanRefused(
            f"The last scan ended {ago} minutes ago, without errors: the next one can start"
            f" in {math.ceil(seconds_left / 60)} minutes.",
            seconds_left=seconds_left,
        )


def _utc(moment: datetime) -> datetime:
    """UTC without a time zone, as the database keeps times."""
    return moment.astimezone(UTC).replace(tzinfo=None)
