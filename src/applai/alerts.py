"""The offers a job alert lists, read from the alert's HTML.

Each sender lays out an offer as a card: one HTML element holding the offer's texts and
links. A sender's reader picks its cards and says which text is which, as the alert gives
it. Outlook wraps every link in Safe Links: a card keeps the link Safe Links wraps.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

# The alert senders the watch reads, by address, with the source of their offers.
SOURCES = {
    "jobalerts-noreply@linkedin.com": "alert_linkedin",
    "alerts@welcometothejungle.com": "alert_wttj",
    "alerte@emails.hellowork.com": "alert_hellowork",
    "no-reply@notification.prod.jobteaser.net": "alert_jobteaser",
    "emploi@alertes.lesjeudis.com": "alert_lesjeudis",
    "notify-noreply@google.com": "alert_google",
}
# Sources whose links are click-tracking redirects: the offer is where they lead.
REDIRECTED = {"alert_wttj", "alert_hellowork", "alert_jobteaser", "alert_google"}

_VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "wbr",
}


class AlertError(Exception):
    """An alert whose offers cannot be read: its layout is not the one its reader knows."""


@dataclass(frozen=True)
class AlertOffer:
    link: str  # as the alert gives it, out of Safe Links
    title: str
    company: str
    location: str | None
    contract: str | None


@dataclass
class _Card:
    texts: list[str] = field(default_factory=list[str])
    links: list[str] = field(default_factory=list[str])
    classes: list[set[str]] = field(default_factory=list[set[str]])  # those around each text

    def texts_in(self, html_class: str) -> list[str]:
        pairs = zip(self.texts, self.classes, strict=True)
        return [text for text, around in pairs if html_class in around]


type _IsCard = Callable[[str, dict[str, str]], bool]


class _Cards(HTMLParser):
    def __init__(self, is_card: _IsCard) -> None:
        super().__init__()
        self._is_card = is_card
        self._open: list[tuple[str, str]] = []  # (tag, class) open in the card, the card first
        self.cards: list[_Card] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        if tag == "a" and "href" in attributes:
            attributes["href"] = _out_of_safe_links(attributes["href"])
        if not self._open and self._is_card(tag, attributes):
            self.cards.append(_Card())
        elif not self._open or tag in _VOID:
            return
        self._open.append((tag, attributes.get("class", "")))
        if tag == "a" and attributes.get("href", "#") != "#":
            self.cards[-1].links.append(attributes["href"])

    def handle_endtag(self, tag: str) -> None:
        if tag in (open_tag for open_tag, _ in self._open):
            while self._open.pop()[0] != tag:
                pass

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if self._open and text:
            self.cards[-1].texts.append(text)
            self.cards[-1].classes.append({word for _, c in self._open for word in c.split()})


def _out_of_safe_links(link: str) -> str:
    url = urlsplit(link)
    if url.netloc.endswith(".safelinks.protection.outlook.com"):
        return parse_qs(url.query).get("url", [link])[0]
    return link


def _with(attribute: str, word: str) -> _IsCard:
    return lambda _, attributes: word in attributes.get(attribute, "").split()


def _linkedin(card: _Card) -> AlertOffer:
    company, location = card.texts[1].split(" · ", 1)
    link = next(link for link in card.links if "/jobs/view/" in link)
    return AlertOffer(link, card.texts[0], company, location, None)


def _company_title_contract_location(card: _Card) -> AlertOffer:
    contract, location = card.texts[2].split(" - ", 1)
    return AlertOffer(card.links[0], card.texts[1], card.texts[0], location, contract)


def _hellowork(card: _Card) -> AlertOffer:
    location, contract = card.texts_in("bg-dark-tags")[:2]  # then the salary, if given
    return AlertOffer(card.links[0], card.texts[0], card.texts[1], location, contract)


def _lesjeudis(card: _Card) -> AlertOffer:
    company, location = card.texts[1].split(" · ", 1)
    return AlertOffer(card.links[0], card.texts[0], company, location, None)


def _google(card: _Card) -> AlertOffer:
    # Title, company, location, then "via <job board>"; a logo may add a letter before them.
    via = next(i for i, text in enumerate(card.texts) if text.startswith("via "))
    if via < 3:
        raise ValueError("title, company or location missing")
    title, company, location = card.texts[via - 3 : via]
    return AlertOffer(card.links[0], title, company, location, None)


def _google_card(tag: str, attributes: dict[str, str]) -> bool:
    # The offers are the cells with a bottom border; the header and the footer have none.
    # Graph returns the styles without the space the alert's own HTML has after the colon.
    style = re.sub(r":\s+", ":", attributes.get("style", ""))
    return tag == "td" and "border-bottom:1px solid #E5E5E5" in style


_READERS: dict[str, tuple[_IsCard, Callable[[_Card], AlertOffer]]] = {
    "alert_linkedin": (_with("data-test-id", "job-card"), _linkedin),
    "alert_wttj": (_with("class", "job-item"), _company_title_contract_location),
    "alert_hellowork": (_with("class", "bg-dark-cards"), _hellowork),
    "alert_jobteaser": (_with("class", "card-content"), _company_title_contract_location),
    "alert_lesjeudis": (
        lambda tag, attributes: (
            tag == "a" and "lesjeudis.com/offers/" in attributes.get("href", "")
        ),
        _lesjeudis,
    ),
    "alert_google": (_google_card, _google),
}


def read_alert(source: str, html: str) -> list[AlertOffer]:
    """The offers listed in the HTML of an alert from source, in the alert's order."""
    is_card, read = _READERS[source]
    parser = _Cards(is_card)
    parser.feed(html)
    parser.close()
    if not parser.cards:
        raise AlertError("no offer found: the alert's layout may have changed")
    offers: list[AlertOffer] = []
    for number, card in enumerate(parser.cards, start=1):
        try:
            offers.append(read(card))
        except (IndexError, ValueError, StopIteration):
            raise AlertError(f"offer {number} does not have the layout expected") from None
    return offers
