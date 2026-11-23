"""The candidate's Outlook.com mailbox, where the watch reads job alerts.

Access is Microsoft Graph with Mail.Read only: ApplAI never sends, moves or deletes mail.
applai mail login signs in once with the device-code flow. MSAL's token cache, which holds
a refresh token, goes to ms_token.json in the data directory, readable by the candidate only;
nothing ApplAI prints or raises holds a token.

read_alerts() reads the alerts received since the last one read, and records each alert
read, with the error that kept its offers from being read if any. A network failure stops
it before the alert is recorded, so the next run reads that alert again. A tracking link that
leads nowhere is not a network failure: it is recorded as the alert's error, like a layout
the reader does not know, and the alert's other offers are kept.
"""

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

import httpx2
import msal  # pyright: ignore[reportMissingTypeStubs]
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from applai.alerts import REDIRECTED, SOURCES, AlertError, read_alert
from applai.models import LOCAL_USER_ID, MailRead, Offer
from applai.profile import DATA_DIR_VARIABLE

CLIENT_ID_VARIABLE = "APPLAI_MS_CLIENT_ID"
TOKEN_FILE = "ms_token.json"
AUTHORITY = "https://login.microsoftonline.com/consumers"  # personal accounts: Outlook.com
SCOPES = ["Mail.Read"]
GRAPH = "https://graph.microsoft.com/v1.0"
FIRST_READ = timedelta(days=14)  # how far back the first read goes
# Query parameters that only follow the click, by name or prefix: the rest of a query may say
# which offer it is.
TRACKING_PARAMETERS = {"trk", "trackingid", "refid", "gclid", "fbclid", "mc_cid", "mc_eid"}
TRACKING_PREFIXES = ("utm_",)


class MailError(Exception):
    """The mailbox cannot be read."""


class MailAuthError(MailError):
    """No valid sign-in to the mailbox: the candidate must reconnect with applai mail login."""


@dataclass(frozen=True)
class Alert:
    message_id: str
    sender: str
    received_at: datetime  # UTC, without a time zone, as the database keeps it


class Mailbox:
    """The mailbox signed in to with client_id, its token cache in data_dir."""

    def __init__(self, data_dir: Path, client_id: str, http: httpx2.Client) -> None:
        self._cache_file = Path(data_dir) / TOKEN_FILE
        # MSAL has no type hints: its objects and the dicts it returns are Any here.
        self._cache: Any = msal.SerializableTokenCache()
        if self._cache_file.exists():
            self._cache.deserialize(self._cache_file.read_text())
        self._app: Any = msal.PublicClientApplication(
            client_id, authority=AUTHORITY, token_cache=self._cache, http_client=http
        )
        self._http = http

    @classmethod
    def from_environment(cls, http: httpx2.Client) -> "Mailbox":
        """The mailbox of the data directory and the client id the environment names."""
        values = [os.environ.get(name) for name in (DATA_DIR_VARIABLE, CLIENT_ID_VARIABLE)]
        if not values[0] or not values[1]:
            raise MailError(
                f"Set {DATA_DIR_VARIABLE} to the candidate's data directory and"
                f" {CLIENT_ID_VARIABLE} to ApplAI's application id at Microsoft."
            )
        return cls(Path(values[0]), values[1], http)

    def login(self, show: Callable[[str], None]) -> None:
        """Sign in with the device-code flow; show() gets Microsoft's code and URL."""
        flow = self._app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise MailAuthError(f"Microsoft refused to start the sign-in: {flow.get('error')}.")
        show(flow["message"])
        result = self._app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise MailAuthError(f"The sign-in did not complete: {result.get('error')}.")
        self._save_cache()

    def alerts(self, since: datetime) -> Iterator[Alert]:
        """The alerts received since then, oldest first; other mails are left unread."""
        url: str | None = f"{GRAPH}/me/messages"
        params: dict[str, str] | None = {
            "$select": "id,receivedDateTime,from",
            "$filter": f"receivedDateTime ge {since:%Y-%m-%dT%H:%M:%SZ}",
            "$orderby": "receivedDateTime asc",
            "$top": "50",
        }
        while url:
            page = self._graph(url, params)
            for message in page["value"]:
                sender = _sender(message)
                if sender in SOURCES:
                    received = datetime.fromisoformat(message["receivedDateTime"])
                    yield Alert(message["id"], sender, _utc(received))
            url, params = page.get("@odata.nextLink"), None  # the link holds the query

    def html(self, alert: Alert) -> str:
        message = self._graph(f"{GRAPH}/me/messages/{alert.message_id}", {"$select": "body"})
        return message["body"]["content"]

    def where_leads(self, link: str) -> str:
        """Where a click-tracking link leads, without reading the page it leads to."""
        with self._http.stream("GET", link, follow_redirects=True) as response:
            return str(response.url)

    def _graph(self, url: str, params: dict[str, str] | None) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "Prefer": 'IdType="ImmutableId"',  # an id that stays when the mail is moved
        }
        response = self._http.get(url, params=params, headers=headers)
        if response.status_code == 401:
            raise MailAuthError("Microsoft refused ApplAI's sign-in: run applai mail login.")
        response.raise_for_status()
        return response.json()

    def _access_token(self) -> str:
        accounts = self._app.get_accounts()
        if not accounts:
            raise MailAuthError("ApplAI is not signed in to the mailbox: run applai mail login.")
        result = self._app.acquire_token_silent(SCOPES, account=accounts[0])
        if not result or "access_token" not in result:
            raise MailAuthError("The mailbox sign-in has expired: run applai mail login.")
        if self._cache.has_state_changed:
            self._save_cache()
        return str(result["access_token"])

    def _save_cache(self) -> None:
        descriptor = os.open(self._cache_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as file:
            os.fchmod(descriptor, 0o600)  # O_CREAT sets the mode of a new file only
            file.write(self._cache.serialize())


@dataclass
class Reading:
    alerts: int = 0
    offers: int = 0
    errors: list[str] = field(default_factory=list[str])  # "sender: error", one per alert


def read_alerts(engine: Engine, mailbox: Mailbox, user_id: int = LOCAL_USER_ID) -> Reading:
    """Record as new offers those listed in the alerts not read yet."""
    reading = Reading()
    with Session(engine) as session:
        last = session.scalar(
            select(func.max(MailRead.received_at)).where(MailRead.user_id == user_id)
        )
        since = last or _utc(datetime.now(UTC)) - FIRST_READ
        read = set(session.scalars(select(MailRead.message_id).where(
            MailRead.user_id == user_id, MailRead.received_at >= since
        )))  # fmt: skip
        known = set(session.scalars(select(Offer.url).where(Offer.user_id == user_id)))
        for alert in mailbox.alerts(since):
            if alert.message_id in read:
                continue
            source = SOURCES[alert.sender]
            record = MailRead(user_id=user_id, message_id=alert.message_id, sender=alert.sender,
                              received_at=alert.received_at)  # fmt: skip
            problems: list[str] = []
            try:
                offers = read_alert(source, mailbox.html(alert))
            except AlertError as error:
                problems.append(str(error))
                offers = []
            for offer in offers:
                try:
                    link = mailbox.where_leads(offer.link) if source in REDIRECTED else offer.link
                    link = _out_of_consent(link)
                except httpx2.HTTPError as error:  # a redirect loop, a mailto:, a dead domain
                    problems.append(f"a link of {urlsplit(offer.link).netloc}: {_name(error)}")
                    continue
                # The path names the offer in an alert's own links; where a redirect leads, the
                # site is not known, and its query may be what names the offer.
                url = _without_tracking(link, whole_query=source not in REDIRECTED)
                if url not in known:
                    known.add(url)
                    session.add(Offer(
                        user_id=user_id, url=url, title=offer.title, company=offer.company,
                        location=offer.location, contract=offer.contract, source=source,
                        board=offer.board,
                        received_at=alert.received_at,
                    ))  # fmt: skip
                    reading.offers += 1
            if problems:
                record.error = "; ".join(problems)
                reading.errors.append(f"{alert.sender}: {record.error}")
            session.add(record)
            session.commit()
            read.add(alert.message_id)
            reading.alerts += 1
    return reading


def _sender(message: dict[str, Any]) -> str:
    """The address a Graph message came from, lowercase; empty for a draft, which has none."""
    sender: dict[str, Any] = message.get("from") or {}
    address: dict[str, Any] = sender.get("emailAddress") or {}
    return str(address.get("address") or "").lower()


def _out_of_consent(link: str) -> str:
    """Where Google's consent page (consent.google.*/ml) sends on, else the link itself."""
    url = urlsplit(link)
    if (url.hostname or "").startswith("consent.google.") and url.path == "/ml":
        return parse_qs(url.query).get("continue", [link])[0]
    return link


def _without_tracking(link: str, whole_query: bool) -> str:
    """The link without its fragment and its tracking: the whole query, or only the parameters
    that follow the click."""
    url = urlsplit(link)
    kept = (
        []
        if whole_query
        else [
            (name, value)
            for name, value in parse_qsl(url.query, keep_blank_values=True)
            if name.lower() not in TRACKING_PARAMETERS
            and not name.lower().startswith(TRACKING_PREFIXES)
        ]
    )
    return urlunsplit(url._replace(query=urlencode(kept), fragment=""))


def _name(error: Exception) -> str:
    return type(error).__name__  # a message may hold the link, which may hold a token


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)
