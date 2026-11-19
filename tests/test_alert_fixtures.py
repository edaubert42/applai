"""Each anonymized job alert lists fictional offers behind its sender's real link format."""

import email
import email.policy
import re
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ALERTS = Path(__file__).parent / "fixtures/alerts"

# sender: (From address, where an offer link leads, two of the fictional companies it lists)
SENDERS = {
    "linkedin": (
        "jobalerts-noreply@linkedin.com",
        "www.linkedin.com/comm/jobs/view/",
        ["Vistelle", "Citélien"],
    ),
    "wttj": (
        "alerts@welcometothejungle.com",
        "t.welcometothejungle.com/ls/click",
        ["Grillon IA", "Mésange Robotique"],
    ),
    "hellowork": (
        "alerte@emails.hellowork.com",
        "emails.hellowork.com/clic/",
        ["Strateo France", "Pointcarré"],
    ),
    "jobteaser": (
        "no-reply@notification.prod.jobteaser.net",
        "link.jobteaser.com/ls/click",
        ["Logipass", "Batiroc Travaux"],
    ),
    "lesjeudis": (
        "emploi@alertes.lesjeudis.com",
        "lesjeudis.com/offers/",
        ["Nébulix IA", "Octaval Conseil"],
    ),
}


def _alert(sender: str) -> EmailMessage:
    raw = (ALERTS / f"{sender}.eml").read_bytes()
    return email.message_from_bytes(raw, policy=email.policy.default)  # type: ignore[return-value]


def _links(html: str) -> list[str]:
    """The links Outlook wrapped in Safe Links, unwrapped (anchors and stylesheets are not)."""
    links: list[str] = []
    for href in re.findall(r'href="([^"]+)"', html):
        url = urlsplit(str(href).replace("&amp;", "&"))
        if url.netloc.endswith("safelinks.protection.outlook.com"):
            links.append(parse_qs(url.query)["url"][0])
    return links


def test_there_is_one_alert_per_sender():
    assert sorted(p.stem for p in ALERTS.glob("*.eml")) == sorted(SENDERS)


@pytest.mark.parametrize("sender", sorted(SENDERS))
def test_an_alert_comes_from_its_sender_to_the_fictional_candidate(sender: str):
    alert = _alert(sender)
    assert alert["From"].addresses[0].addr_spec == SENDERS[sender][0]
    assert alert["To"].addresses[0].addr_spec == "jeanne.dupont@example.org"


@pytest.mark.parametrize("sender", sorted(SENDERS))
def test_an_alert_lists_two_fictional_offers_behind_the_real_link_format(sender: str):
    _, offer_link, companies = SENDERS[sender]
    body = _alert(sender).get_body(("html",))
    assert body is not None
    html = body.get_content()
    offers = {link for link in _links(html) if offer_link in link}
    assert len(offers) >= 2
    for company in companies:
        assert company in html
