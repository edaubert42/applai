"""The watch reads the job alerts in the candidate's mailbox, against a fake Microsoft network."""

import base64
import email
import email.policy
import hashlib
import json
import re
import stat
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx2
import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from applai.cli import main
from applai.database import open_database
from applai.mail import MailAuthError, Mailbox, read_alerts
from applai.models import MailRead, Offer

ALERTS = Path(__file__).parent / "fixtures/alerts"
TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"  # Microsoft's tenant of personal accounts
CLIENT_ID = "00000000-0000-0000-0000-0000000000a1"
ACCESS_TOKEN, REFRESH_TOKEN = "access-token-of-jeanne", "refresh-token-of-jeanne"
USER_CODE, SIGN_IN_PAGE = "JDPT0000", "https://www.microsoft.com/link"
OFFER_SITE = "jobs.example.org"  # where the fake click-tracking redirects lead
NOW = datetime.now(UTC).replace(microsecond=0)

# sender: (offers listed, the first two as (url, title, company, location, contract));
# a url of None is one the alert only gives behind a click-tracking redirect.
EXPECTED = {
    "linkedin": (6, [
        ("https://www.linkedin.com/comm/jobs/view/0000977062/", "IA Engineer", "Vistelle",
         "Courbevoie (Hybride)", None),
        ("https://www.linkedin.com/comm/jobs/view/0000450848/", "AI Engineer / FDE", "Citélien",
         "Ville de Paris (Hybride)", None),
    ]),
    "wttj": (2, [
        (None, "Software Engineer - Stagiaire/Alternant", "Grillon IA", "Paris", "Stage"),
        (None, "Data Engineer - Stage", "Mésange Robotique", "Paris", "Stage"),
    ]),
    "hellowork": (12, [
        (None, "Responsible ai Engineer H/F", "Strateo France", "Paris - 75", "CDI"),
        (None, "Data & ai Product & Process Design Expert H/F", "Pointcarré", "Paris - 75", "CDI"),
    ]),
    "jobteaser": (10, [
        (None, "Sales Development Intern, Services", "Logipass", "Paris (France)", "Stage"),
        (None, "TFE/PFE - Assistant Ingénieur Travaux (H/F)", "Batiroc Travaux",
         "Multilocalisation", "Stage"),
    ]),
    "lesjeudis": (5, [
        ("https://lesjeudis.com/offers/x00x000x-00xx-0000-xxxx-00000x394707",
         "Applied AI Engineer, CyberSecurity, Defense", "Nébulix IA", "Paris", None),
        ("https://lesjeudis.com/offers/0000x00x-0x00-000x-x00x-00xxxx364517",
         "Consultant junior AI Engineer - F/H/N", "Octaval Conseil", "Paris", None),
    ]),
    "google": (4, [
        (None, "Data Engineer Junior - Plateforme Média F/H", "Héron Data", "Paris, France", None),
        (None, "Analytics Engineer", "Banque Ortolan", "Île-de-France, France", None),
    ]),
}  # fmt: skip


@dataclass
class Mail:
    id: str
    sender: str
    received: datetime
    html: str


def _alert(sender: str, received: datetime = NOW - timedelta(hours=1), id: str = "") -> Mail:
    raw = (ALERTS / f"{sender}.eml").read_bytes()
    message = email.message_from_bytes(raw, policy=email.policy.default)
    body = message.get_body(("html",))  # type: ignore[attr-defined]
    assert body is not None
    address = message["From"].addresses[0].addr_spec
    return Mail(id or f"{sender}-{received:%d%H%M}", address, received, body.get_content())


def _b64(claims: Mapping[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")


class FakeMicrosoft:
    """Microsoft's sign-in, Graph, and the alerts' click-tracking redirects, as one network."""

    def __init__(self) -> None:
        self.mails: list[Mail] = []
        self.lifetime = 3600  # of an access token, in seconds
        self.refuses_refresh = False
        self.revoked = False
        self.redirects_loop = False  # a click-tracking link that never leads anywhere
        self.offers_by_query = False  # the offer's site tells its offers apart by the query
        self.filters: list[str] = []  # of each listing of the mailbox
        self.bodies_read: list[str] = []
        self.client = httpx2.Client(transport=httpx2.MockTransport(self._handle))

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "login.microsoftonline.com":
            return self._sign_in(request)
        if request.url.host == "graph.microsoft.com":
            return self._graph(request)
        if request.url.host == OFFER_SITE:
            return httpx2.Response(200, text="<h1>Offre</h1>")
        if self.redirects_loop:
            return httpx2.Response(302, headers={"Location": str(request.url)})
        where = hashlib.sha256(str(request.url).encode()).hexdigest()[:12]
        offer = (
            f"offer?id={where}&utm_source=a" if self.offers_by_query else f"{where}?utm_source=a"
        )
        return httpx2.Response(302, headers={"Location": f"https://{OFFER_SITE}/{offer}"})

    def _sign_in(self, request: httpx2.Request) -> httpx2.Response:
        base = "https://login.microsoftonline.com/consumers"
        path = request.url.path
        if path.endswith("discovery/instance"):
            aliases = ["login.microsoftonline.com", "login.windows.net", "login.microsoft.com"]
            return httpx2.Response(200, json={
                "tenant_discovery_endpoint": f"{base}/v2.0/.well-known/openid-configuration",
                "metadata": [{"preferred_network": aliases[0], "aliases": aliases}],
            })  # fmt: skip
        if path.endswith("openid-configuration"):
            return httpx2.Response(200, json={
                "authorization_endpoint": f"{base}/oauth2/v2.0/authorize",
                "token_endpoint": f"{base}/oauth2/v2.0/token",
                "device_authorization_endpoint": f"{base}/oauth2/v2.0/devicecode",
                "issuer": f"https://login.microsoftonline.com/{TENANT}/v2.0",
            })  # fmt: skip
        if path.endswith("devicecode"):
            message = f"To sign in, open the page {SIGN_IN_PAGE} and enter the code {USER_CODE}."
            return httpx2.Response(200, json={
                "device_code": "device-code", "user_code": USER_CODE, "interval": 0,
                "verification_uri": SIGN_IN_PAGE, "expires_in": 900, "message": message,
            })  # fmt: skip
        grant = parse_qs(request.content.decode())["grant_type"][0]
        if grant == "refresh_token" and self.refuses_refresh:
            return httpx2.Response(400, json={"error": "invalid_grant"})
        now = int(time.time())
        claims = {"aud": CLIENT_ID, "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
                  "iat": now, "exp": now + 3600, "oid": "jeanne", "tid": TENANT,
                  "preferred_username": "jeanne.dupont@example.org"}  # fmt: skip
        return httpx2.Response(200, json={
            "token_type": "Bearer", "scope": "Mail.Read", "expires_in": self.lifetime,
            "access_token": ACCESS_TOKEN, "refresh_token": REFRESH_TOKEN,
            "id_token": f"x.{_b64(claims)}.x", "client_info": _b64({"uid": "j", "utid": TENANT}),
        })  # fmt: skip

    def _graph(self, request: httpx2.Request) -> httpx2.Response:
        if self.revoked or request.headers.get("Authorization") != f"Bearer {ACCESS_TOKEN}":
            return httpx2.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})
        message_id = request.url.path.removeprefix("/v1.0/me/messages").strip("/")
        if message_id:
            self.bodies_read.append(message_id)
            mail = next(mail for mail in self.mails if mail.id == message_id)
            return httpx2.Response(
                200, json={"body": {"contentType": "html", "content": mail.html}}
            )
        params = request.url.params
        since = datetime.fromisoformat(params["$filter"].removeprefix("receivedDateTime ge "))
        skip = int(params.get("$skip", 0))
        if not skip:
            self.filters.append(params["$filter"])
        mails = sorted((m for m in self.mails if m.received >= since), key=lambda m: m.received)
        page: dict[str, object] = {"value": [{
            "id": mail.id, "receivedDateTime": f"{mail.received:%Y-%m-%dT%H:%M:%SZ}",
            "from": {"emailAddress": {"name": "Alerte", "address": mail.sender}},
        } for mail in mails[skip : skip + 2]]}  # fmt: skip
        if len(mails) > skip + 2:
            page["@odata.nextLink"] = str(request.url.copy_merge_params({"$skip": skip + 2}))
        return httpx2.Response(200, json=page)


@pytest.fixture
def microsoft() -> Iterator[FakeMicrosoft]:
    fake = FakeMicrosoft()
    yield fake
    fake.client.close()


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = open_database(tmp_path)
    yield engine
    engine.dispose()


def _signed_in(data_dir: Path, microsoft: FakeMicrosoft) -> Mailbox:
    mailbox = Mailbox(data_dir, CLIENT_ID, microsoft.client)
    mailbox.login(lambda _: None)
    return mailbox


def _offers(engine: Engine) -> list[Offer]:
    with Session(engine) as session:
        return list(session.scalars(select(Offer).order_by(Offer.id)))


def _mails_read(engine: Engine) -> list[MailRead]:
    with Session(engine) as session:
        return list(session.scalars(select(MailRead).order_by(MailRead.received_at)))


def test_mail_login_prints_microsofts_code_and_url_then_stores_the_token_cache_not_the_token(
    tmp_path: Path,
    microsoft: FakeMicrosoft,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.setenv("APPLAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APPLAI_MS_CLIENT_ID", CLIENT_ID)

    main(["mail", "login"], http=microsoft.client)

    printed = capsys.readouterr()
    assert USER_CODE in printed.out
    assert SIGN_IN_PAGE in printed.out
    for token in (ACCESS_TOKEN, REFRESH_TOKEN):
        assert token not in printed.out + printed.err
    cache = tmp_path / "ms_token.json"
    assert stat.S_IMODE(cache.stat().st_mode) == 0o600  # the candidate's only
    # A later run reads the mailbox with the stored cache, without signing in again.
    microsoft.mails.append(_alert("lesjeudis"))
    read_alerts(engine, Mailbox(tmp_path, CLIENT_ID, microsoft.client))
    assert len(_offers(engine)) == 5


@pytest.mark.parametrize("sender", sorted(EXPECTED))
def test_each_senders_alert_yields_its_offers(
    sender: str, tmp_path: Path, microsoft: FakeMicrosoft, engine: Engine
):
    alert = _alert(sender)
    microsoft.mails.append(alert)

    read_alerts(engine, _signed_in(tmp_path, microsoft))

    count, first_two = EXPECTED[sender]
    offers = _offers(engine)
    assert len(offers) == count
    for offer, (url, *given) in zip(offers, first_two, strict=False):
        assert [offer.title, offer.company, offer.location, offer.contract] == given
        if url is None:  # where the redirect leads, without its tracking query
            assert offer.url.startswith(f"https://{OFFER_SITE}/")
            assert "?" not in offer.url
        else:
            assert offer.url == url
    assert len({offer.url for offer in offers}) == count
    for offer in offers:
        assert (offer.source, offer.state) == (f"alert_{sender}", "new")
        assert offer.received_at == alert.received.replace(tzinfo=None)


def test_a_mail_already_read_is_never_read_again(
    tmp_path: Path, microsoft: FakeMicrosoft, engine: Engine
):
    microsoft.mails += [_alert("lesjeudis"), _alert("wttj", NOW - timedelta(minutes=30))]
    mailbox = _signed_in(tmp_path, microsoft)
    read_alerts(engine, mailbox)
    first = [offer.url for offer in _offers(engine)]
    microsoft.bodies_read.clear()

    read_alerts(engine, mailbox)

    assert [offer.url for offer in _offers(engine)] == first
    assert len(first) == 7
    assert microsoft.bodies_read == []
    assert [mail.message_id for mail in _mails_read(engine)] == [m.id for m in microsoft.mails]


def test_an_offer_whose_url_is_known_is_not_created_again(
    tmp_path: Path, microsoft: FakeMicrosoft, engine: Engine
):
    known = EXPECTED["lesjeudis"][1][0][0]
    with Session(engine) as session:
        session.add(Offer(user_id=1, url=known, title="AI Engineer", company="Nébulix IA",
                          state="triaged"))  # fmt: skip
        session.commit()
    # The same offers again in the next day's alert.
    microsoft.mails += [_alert("lesjeudis", NOW - timedelta(days=1)), _alert("lesjeudis")]

    read_alerts(engine, _signed_in(tmp_path, microsoft))

    offers = _offers(engine)
    assert len(offers) == 5
    assert [(o.url, o.title, o.state) for o in offers[:1]] == [(known, "AI Engineer", "triaged")]


def test_reading_starts_after_the_last_mail_read_and_catches_up(
    tmp_path: Path, microsoft: FakeMicrosoft, engine: Engine
):
    last_read = NOW - timedelta(days=2)
    microsoft.mails.append(_alert("lesjeudis", last_read))
    mailbox = _signed_in(tmp_path, microsoft)
    read_alerts(engine, mailbox)
    # Received while ApplAI was closed: more than one page of Graph's results.
    for hours, sender in enumerate(["linkedin", "wttj", "jobteaser"], start=1):
        microsoft.mails.append(_alert(sender, last_read + timedelta(hours=hours)))

    read_alerts(engine, mailbox)

    assert microsoft.filters[-1] == f"receivedDateTime ge {last_read:%Y-%m-%dT%H:%M:%SZ}"
    sources = {offer.source for offer in _offers(engine)}
    assert sources == {"alert_lesjeudis", "alert_linkedin", "alert_wttj", "alert_jobteaser"}


@pytest.mark.parametrize("token", ["missing", "expired", "refused by Graph"])
def test_an_expired_or_missing_token_raises_mail_auth_error(
    token: str, tmp_path: Path, microsoft: FakeMicrosoft, engine: Engine
):
    microsoft.mails.append(_alert("lesjeudis"))
    microsoft.lifetime = 60  # MSAL refreshes a token this close to its end
    mailbox = Mailbox(tmp_path, CLIENT_ID, microsoft.client)
    if token != "missing":
        mailbox.login(lambda _: None)
    microsoft.refuses_refresh = token == "expired"
    microsoft.revoked = token == "refused by Graph"

    with pytest.raises(MailAuthError):
        read_alerts(engine, mailbox)

    assert _offers(engine) == []


def test_an_alert_that_cannot_be_parsed_is_recorded_as_an_error_with_its_sender(
    tmp_path: Path,
    microsoft: FakeMicrosoft,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.setenv("APPLAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APPLAI_MS_CLIENT_ID", CLIENT_ID)
    _signed_in(tmp_path, microsoft)
    unknown_layout = _alert("linkedin", NOW - timedelta(hours=2), id="linkedin-new-layout")
    unknown_layout.html = "<p>Votre profil a été consulté 3 fois cette semaine.</p>"
    microsoft.mails += [unknown_layout, _alert("lesjeudis")]

    main(["mail", "read"], http=microsoft.client)

    assert "jobalerts-noreply@linkedin.com" in capsys.readouterr().out
    engine = open_database(tmp_path)
    try:
        mails = [(m.message_id, m.sender, m.error is not None) for m in _mails_read(engine)]
        offers = _offers(engine)
    finally:
        engine.dispose()
    assert mails == [
        ("linkedin-new-layout", "jobalerts-noreply@linkedin.com", True),
        (f"lesjeudis-{NOW - timedelta(hours=1):%d%H%M}", "emploi@alertes.lesjeudis.com", False),
    ]
    assert len(offers) == 5


def test_a_tracking_link_that_leads_nowhere_is_recorded_as_an_error_and_stops_nothing(
    tmp_path: Path, microsoft: FakeMicrosoft, engine: Engine
):
    microsoft.redirects_loop = True
    microsoft.mails += [_alert("wttj", NOW - timedelta(hours=2)), _alert("lesjeudis")]
    mailbox = _signed_in(tmp_path, microsoft)

    reading = read_alerts(engine, mailbox)

    assert [(m.sender, m.error is not None) for m in _mails_read(engine)] == [
        ("alerts@welcometothejungle.com", True),
        ("emploi@alertes.lesjeudis.com", False),
    ]
    assert [offer.source for offer in _offers(engine)] == ["alert_lesjeudis"] * 5
    assert len(reading.errors) == 1
    assert "alerts@welcometothejungle.com" in reading.errors[0]
    microsoft.bodies_read.clear()
    read_alerts(engine, mailbox)
    assert microsoft.bodies_read == []  # the failed alert is not read again


def test_offers_that_differ_only_by_their_query_stay_two_offers_without_the_tracking(
    tmp_path: Path, microsoft: FakeMicrosoft, engine: Engine
):
    microsoft.offers_by_query = True
    microsoft.mails.append(_alert("wttj"))

    read_alerts(engine, _signed_in(tmp_path, microsoft))

    urls = [offer.url for offer in _offers(engine)]
    assert len(urls) == 2
    assert all(url.startswith(f"https://{OFFER_SITE}/offer?id=") for url in urls)
    assert all("utm_source" not in url and "#" not in url for url in urls)


def test_two_google_offers_with_the_same_title_and_company_in_two_places_stay_two_offers(
    tmp_path: Path, microsoft: FakeMicrosoft, engine: Engine
):
    microsoft.mails.append(_alert("google"))

    read_alerts(engine, _signed_in(tmp_path, microsoft))

    same = [offer for offer in _offers(engine) if offer.company == "Pinson Médias"]
    assert [(offer.title, offer.location) for offer in same] == [
        ("Data Engineer Streaming - Paris (H/F)", "Saint-Denis, France"),
        ("Data Engineer Streaming - Paris (H/F)", "Nanterre, France"),
    ]
    assert same[0].url != same[1].url


def test_a_google_alert_is_read_as_graph_returns_it_without_spaces_in_its_styles(
    tmp_path: Path, microsoft: FakeMicrosoft, engine: Engine
):
    alert = _alert("google")
    microsoft.mails.append(replace(alert, html=re.sub(r":\s+", ":", alert.html)))

    read_alerts(engine, _signed_in(tmp_path, microsoft))

    assert len(_offers(engine)) == 4
