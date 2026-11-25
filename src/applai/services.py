"""Service health computed from what ApplAI's database and environment already record."""

import json
import os
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from applai.alerts import SOURCES
from applai.backup import BackupError, last_backup_at
from applai.browser import CONNECTED_FOR
from applai.details import offer_removed, on_linkedin, read_by_extension
from applai.drafting import INVALID_CITATION
from applai.linkedin import COMMAND_VARIABLE as LINKEDIN_VAR
from applai.mail import CLIENT_ID_VARIABLE as MAIL_VAR
from applai.mail import TOKEN_FILE
from applai.models import AgentRun, MailRead, Offer, OfferOrigin, OfferState, Scan, StateChange
from applai.notion import TOKEN_VARIABLE as NOTION_VAR
from applai.notion import last_write_status
from applai.profile import ProfileError, load_profile
from applai.scan import Clock, Scanner, Watch
from applai.settings import SettingsError, extension_id
from applai.trace import OK
from applai.triage import AGENT as TRIAGE_AGENT
from applai.triage import TRIAGE_MODEL

State = Literal["green", "orange", "red"]
_SITE_STATES = (OfferState.NEW, OfferState.DESCRIBED, OfferState.SCREENED_OUT, OfferState.TRIAGED)


class ServicesStatus(BaseModel):
    sites: list[dict[str, Any]]
    agents: list[dict[str, Any]]
    mailbox: list[dict[str, Any]]
    applai: list[dict[str, Any]]


type _Dt = datetime | None


def _iso(dt: _Dt) -> str | None:
    return dt.replace(tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _row(
    id: str,
    state: State,
    at: _Dt = None,
    err: str | None = None,
    err_at: _Dt = None,
    hint: str | None = None,
    **kw: Any,
) -> dict[str, Any]:
    return {
        "id": id,
        "state": state,
        "last_at": _iso(at),
        "last_error": err,
        "last_error_at": _iso(err_at),
        "hint": hint,
        **kw,
    }


def _state(now: datetime, ok: _Dt, fail: _Dt, *, conn: bool = True, lost: bool = False) -> State:
    if not conn or lost or (fail and (not ok or fail > ok)):
        return "red"
    return "orange" if fail and fail >= now - timedelta(hours=24) else "green"


def services_status(
    session: Session,
    uid: int,
    data_dir: Path,
    clock: Clock,
    asked: _Dt,
    scanner: Scanner | None,
    watch: Watch | None,
) -> ServicesStatus:
    """Compute the health of every service ApplAI depends on."""
    local_now = clock()
    now = local_now.astimezone(UTC).replace(tzinfo=None)
    q = select(Scan).where(Scan.user_id == uid)
    scans = list(session.scalars(q.order_by(Scan.started_at.desc(), Scan.id.desc()).limit(20)))
    return ServicesStatus(
        sites=_sites(session, uid, data_dir, local_now, now, asked),
        agents=_agents(session, uid, now),
        mailbox=[_mailbox(session, uid, data_dir, now, watch, scans)],
        applai=_applai(session, uid, data_dir, now, scanner, scans),
    )


def _site_health(
    now: datetime, offers: list[Offer], reads: dict[int, datetime], *, ext: bool, conn: bool = True
) -> tuple[State, datetime | None, datetime | None, str | None]:
    def fail_of(o: Offer) -> datetime | None:
        return (o.page_failed_at or o.detail_failed_at) if ext else o.detail_failed_at

    read_at = max((reads[o.id] for o in offers if o.id in reads), default=None)
    offers = [o for o in offers if not offer_removed(o.detail_error)]  # the site's, not ours
    fail_at = max((w for o in offers if (w := fail_of(o))), default=None)
    active = [(w or datetime.min, o.detail_error) for o in offers if o.detail_error]
    msg = max(active, key=lambda x: x[0])[1] if active else None
    lost = any(o.detail_error and not fail_of(o) for o in offers)
    return _state(now, read_at, fail_at, conn=conn, lost=lost), read_at, fail_at, msg


def _sites(
    s: Session, uid: int, data_dir: Path, local_now: datetime, now: datetime, asked: datetime | None
) -> list[dict[str, Any]]:
    offers = list(
        s.scalars(select(Offer).where(Offer.user_id == uid, Offer.origin == OfferOrigin.APPLAI))
    )
    sc = StateChange
    q_r = select(sc.offer_id, func.max(sc.changed_at)).where(
        sc.user_id == uid, sc.from_state == OfferState.NEW, sc.to_state == OfferState.DESCRIBED
    )
    reads = dict(s.execute(q_r.group_by(sc.offer_id)).all())
    ext_err, ext_id = _try(lambda: extension_id(data_dir), SettingsError)
    conn = ext_id is not None and asked is not None and local_now - asked <= CONNECTED_FOR
    est, eread, efail, emsg = _site_health(
        now,
        [o for o in offers if read_by_extension(o.url)],
        reads,
        ext=True,
        conn=conn and not ext_err,
    )
    ehint = ext_err or (
        None
        if est == "green"
        else "Set extension_id in settings.yaml."
        if not ext_id
        else "Reload the ApplAI extension in Chrome."
        if not conn
        else "Open the site in your Chrome so the extension reads it."
    )
    rows = [_row("extension", est, eread, ext_err or emsg, efail, ehint, connected=conn)]
    by_src: dict[str, list[Offer]] = defaultdict(list)
    for o in offers:
        if o.source:
            by_src[o.source].append(o)
    known = list(dict.fromkeys(SOURCES.values()))
    li_set = bool(os.environ.get(LINKEDIN_VAR, "").strip())
    for src in [*known, *sorted(set(by_src) - set(known))]:
        items = by_src.get(src, [])
        is_ext = any(read_by_extension(o.url) for o in items) if items else src == "alert_jobteaser"
        is_li = any(on_linkedin(o.url) for o in items) if items else src == "alert_linkedin"
        how = "linkedin_reader" if is_li else "extension" if is_ext else "alert_email"
        s_conn = li_set if is_li else None
        st, read_at, fail_at, msg = _site_health(
            now, items, reads, ext=is_ext, conn=s_conn is not False
        )
        hint = _site_hint(st, is_ext, is_li, s_conn)
        counts = {str(x): sum(1 for o in items if o.state == x) for x in _SITE_STATES}
        boards = sorted({o.board for o in items if o.board}) if src == "alert_google" else []
        errs = sum(1 for o in items if o.detail_error is not None)
        kw = {"how_read": how, "connected": s_conn, "boards": boards, "counts": counts}
        rows.append(_row(src, st, read_at, msg, fail_at, hint, **kw, error_count=errs))
    return rows


def _site_hint(st: State, is_ext: bool, is_li: bool, conn: bool | None) -> str | None:
    if st == "green":
        return None
    if is_li:
        return f"Set {LINKEDIN_VAR} to connect LinkedIn." if not conn else "Check LinkedIn session."
    if is_ext:
        return "Open the site in your Chrome so the extension reads it."
    return "Check the failing offer pages or wait for the next scan."


_AGENT_HINT = "Check Claude login, API key, or quota."
_AGENT_HINTS = {INVALID_CITATION: "A draft cited facts wrongly and was refused: draft it again."}


def _agents(s: Session, uid: int, now: datetime) -> list[dict[str, Any]]:
    q = select(AgentRun).where(AgentRun.user_id == uid)
    by_agent: dict[str, list[AgentRun]] = defaultdict(list)
    for r in s.scalars(q.order_by(AgentRun.started_at.desc(), AgentRun.id.desc())):
        by_agent[r.agent].append(r)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows: list[dict[str, Any]] = []
    for name in [TRIAGE_AGENT, *sorted(set(by_agent) - {TRIAGE_AGENT})]:
        items = by_agent.get(name, [])
        latest = items[0] if items else None
        last_ok = next((r.started_at for r in items if r.outcome == OK), None)
        fail = next((r for r in items if r.outcome != OK), None)
        fail_at = fail.started_at if fail else None
        st = _state(now, last_ok, fail_at, lost=latest is not None and latest.outcome != OK)
        cost = sum(r.cost_usd or 0.0 for r in items if r.started_at >= day)
        err = fail.outcome if fail else None
        hint = None if st == "green" else _AGENT_HINTS.get(err or "", _AGENT_HINT)
        model = latest.model if latest else TRIAGE_MODEL
        rows.append(_row(name, st, last_ok, err, fail_at, hint, model=model, cost_today_usd=cost))
    return rows


def _mailbox(
    s: Session, uid: int, data_dir: Path, now: datetime, watch: Watch | None, scans: list[Scan]
) -> dict[str, Any]:
    client_id = watch.ms_client_id if watch is not None else os.environ.get(MAIL_VAR)
    signed_in = bool(client_id) and _has_mail_account(data_dir)
    q = select(MailRead).where(MailRead.user_id == uid)
    mails = list(s.scalars(q.order_by(MailRead.read_at.desc(), MailRead.id.desc())))
    last_read = mails[0].read_at if mails else None
    last_ok = next((m.read_at for m in mails if m.error is None), None)
    m_fail = next(((m.read_at, m.error) for m in mails if m.error), None)
    done = [sc for sc in scans if sc.finished_at]
    s_fail = next(
        ((sc.finished_at, "; ".join(e)) for sc in done if sc.finished_at and (e := _mail_errs(sc))),
        None,
    )
    pairs = [p for p in (m_fail, s_fail) if p and p[1]]
    fail_at, msg = max(pairs, key=lambda p: p[0]) if pairs else (None, None)
    if done and not _mail_errs(done[0]) and done[0].finished_at:
        last_ok = max(t for t in (last_ok, done[0].finished_at) if t is not None)
    st = _state(now, last_ok, fail_at, conn=signed_in)
    hint = None if signed_in and st == "green" else "Run applai mail login."
    return _row("mailbox", st, last_read, msg, fail_at, hint, connected=signed_in)


def _applai(
    s: Session, uid: int, data_dir: Path, now: datetime, scanner: Scanner | None, scans: list[Scan]
) -> list[dict[str, Any]]:
    st = scanner.status() if scanner else None
    latest, running = scans[0] if scans else None, st.running if st else False
    crashed = latest is not None and latest.finished_at is None and not running
    ok_at = next((sc.finished_at for sc in scans if sc.finished_at and not sc.errors), None)
    failed = [
        sc for sc in scans if sc.errors or (not sc.finished_at and (not running or sc != latest))
    ]
    fail = failed[0] if failed else None
    fail_at = (fail.finished_at or fail.started_at) if fail else None
    scan_err = "Scan stopped early." if crashed else ("; ".join(fail.errors) if fail else None)
    scan_st = _state(now, ok_at, fail_at, lost=crashed or bool(latest and latest.errors))
    next_at = st.next_slot.astimezone(UTC).replace(tzinfo=None) if st and st.next_slot else None
    scan_at = (latest.finished_at or latest.started_at) if latest else None
    prof_err, _ = _try(lambda: load_profile(data_dir), ProfileError)
    bak_err, bak_at = _try(lambda: last_backup_at(data_dir), BackupError)
    bak_msg = bak_err or (None if bak_at else "No backup found.")
    tok = bool(os.environ.get(NOTION_VAR, "").strip())
    q_n = select(func.max(Offer.updated_at)).where(
        Offer.user_id == uid, Offer.origin == OfferOrigin.OLD_WORKFLOW
    )
    ws = last_write_status(data_dir)
    n_ok = max((t for t in (s.scalar(q_n), ws.last_ok_at) if t is not None), default=None)
    n_st = _state(now, n_ok, ws.last_error_at, conn=tok)
    s_hint = "Review scan errors." if scan_st != "green" else None
    p_hint = "Fix profile.yaml." if prof_err else None
    b_hint = "Check APPLAI_BACKUP_DIR." if bak_msg else None
    n_hint = (
        f"Set {NOTION_VAR}."
        if not tok
        else "Check Notion token and database access."
        if n_st != "green"
        else None
    )
    return [
        _row("scan", scan_st, scan_at, scan_err, fail_at, s_hint, next_at=_iso(next_at)),
        _row("profile", "red" if prof_err else "green", None, prof_err, None, p_hint),
        _row("backup", "green" if not bak_msg else "red", bak_at, bak_msg, None, b_hint),
        _row("notion", n_st, n_ok, ws.last_error, ws.last_error_at, n_hint, connected=tok),
    ]


def _try[T](fn: Callable[[], T], exc: type[Exception]) -> tuple[str | None, T | None]:
    try:
        return None, fn()
    except exc as err:
        return str(err), None


def _has_mail_account(data_dir: Path) -> bool:
    try:
        raw: Any = json.loads((data_dir / TOKEN_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(raw, dict) and bool(cast(dict[str, Any], raw).get("Account"))


def _mail_errs(scan: Scan) -> list[str]:
    return [err for err in scan.errors if err.startswith(("Outlook ", "A job alert "))]
