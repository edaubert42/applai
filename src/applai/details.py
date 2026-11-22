"""The full text and the logo of each offer the first screening kept, read from its page.

describe_offers() reads the page of each offer ApplAI found that is still new: its JobPosting
JSON-LD when it has one, else its main text. The description always comes from the page; the
rest only fills what the offer lacks, since screening judged the alert's location and
contract. The offer then moves new → described. A page that fails records its error on the
offer, which stays new and is read again on the next run. LinkedIn offers are left new: their
reader is another one, through the candidate's LinkedIn MCP server.

The logo is the posting's, else the page's og:image, else the site's icon, downloaded once per
company into logos/ in the data directory; none found, the UI shows initials. Every request
carries a generic User-Agent, never anything about the candidate.
"""

import json
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass, field
from html.parser import HTMLParser
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast
from urllib.parse import urljoin, urlsplit

import httpx2
from sqlalchemy import select
from sqlalchemy.orm import Session

from applai.models import LOCAL_USER_ID, Offer, OfferOrigin, OfferState
from applai.orchestrator import Actor, move
from applai.text import slugify

LOGOS_DIR = "logos"
TIMEOUT = 20.0  # seconds
USER_AGENT = f"ApplAI/{version('applai')}"
MAX_LOGO_BYTES = 1_000_000
# Raster images only: an SVG can hold scripts.
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
                "image/webp": ".webp", "image/x-icon": ".ico",
                "image/vnd.microsoft.icon": ".ico"}  # fmt: skip
# How Google's consent page opens, in lowercase, in the languages of the candidate's alerts.
_CONSENT_WALLS = ("before you continue to google", "avant d'accéder à google",
                  "avant de continuer sur google")  # fmt: skip
_SKIPPED = {"head", "script", "style", "noscript", "template", "svg", "nav", "header", "footer"}
_BLOCKS = {"br", "p", "div", "li", "tr", "section", "article", "main", "blockquote", "pre",
           "h1", "h2", "h3", "h4", "h5", "h6"}  # fmt: skip


@dataclass(frozen=True)
class Details:
    """What a page says about its offer; logos are candidate URLs, best first, as written."""

    description: str
    company: str | None = None
    location: str | None = None
    contract: str | None = None
    salary: str | None = None
    logos: tuple[str, ...] = ()


@dataclass
class Describing:
    described: int = 0
    errors: list[str] = field(default_factory=list[str])  # "offer <id>: error", one per offer


class PageError(Exception):
    """An offer's page that cannot be read."""


def watch_client(timeout: float = TIMEOUT) -> httpx2.Client:
    """The HTTP client the watch reads pages with."""
    return httpx2.Client(headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True)


def describe_offers(
    session: Session,
    data_dir: Path | str,
    http: httpx2.Client | None = None,
    user_id: int = LOCAL_USER_ID,
) -> Describing:
    """Describe every new offer ApplAI found, except LinkedIn's; commits after each one."""
    describing = Describing()
    offers = session.scalars(
        select(Offer)
        .where(
            Offer.user_id == user_id,
            Offer.state == OfferState.NEW,
            Offer.origin == OfferOrigin.APPLAI,  # the old workflow's are read only
        )
        .order_by(Offer.id)
    ).all()
    with nullcontext(http) if http else watch_client() as client:
        for offer in offers:
            if on_linkedin(offer.url):
                continue
            offer_id = offer.id
            try:
                describe_offer(session, offer, Path(data_dir), client)
                describing.described += 1
            except PageError as error:
                describing.errors.append(f"offer {offer_id}: {error}")
            session.commit()
    return describing


def on_linkedin(url: str) -> bool:
    """Whether an offer is LinkedIn's, which this module leaves to its own reader."""
    return (urlsplit(url).hostname or "").endswith("linkedin.com")


def describe_offer(session: Session, offer: Offer, data_dir: Path, http: httpx2.Client) -> None:
    """Read the offer's page and move it to described, or record why not and raise PageError.

    Writes in the caller's session and does not commit.
    """
    try:
        base, details = _read(http, offer.url)
    except PageError as error:
        offer.detail_error = str(error)
        raise
    offer.description = details.description
    for name in ("company", "location", "contract", "salary"):
        if getattr(details, name) and not getattr(offer, name):
            setattr(offer, name, getattr(details, name))
    candidates = [urljoin(base, url) for url in (*details.logos, "/favicon.ico")]
    offer.logo = _logo(http, offer.company, candidates, data_dir / LOGOS_DIR)
    offer.detail_error = None
    move(session, offer, OfferState.DESCRIBED, Actor.WATCH)


def read_page(html: str) -> Details:
    """What an offer's page says: from its JobPosting JSON-LD, else from its main text."""
    page = _Page.of(html)
    posting = next((item for block in page.json_ld for item in _postings(_json(block))), None)
    if posting is None:
        return Details(page.text(), logos=(*page.og_images, *page.icons))
    organization = posting.get("hiringOrganization")
    logo = _at(organization, "logo")
    return Details(
        _Page.of(str(posting.get("description") or "")).text(),
        company=_string(_at(organization, "name")) or _string(organization),
        location="; ".join(filter(None, map(_place, _list(posting.get("jobLocation"))))) or None,
        contract=", ".join(_strings(posting.get("employmentType"))) or None,
        salary=_salary(posting.get("baseSalary")),
        logos=(*_strings(_at(logo, "url") or logo), *page.og_images, *page.icons),
    )


def _read(http: httpx2.Client, url: str) -> tuple[str, Details]:
    try:
        response = http.get(url)
        response.raise_for_status()
    except httpx2.HTTPStatusError as error:
        raise PageError(f"the page answered HTTP {error.response.status_code}") from None
    except httpx2.TimeoutException:
        raise PageError("the page did not answer in time") from None
    except httpx2.HTTPError as error:  # a message may hold the URL, which may hold a token
        raise PageError(f"the page could not be read: {type(error).__name__}") from None
    details = read_page(response.text)
    if not details.description:
        raise PageError("the page has no text")
    if _is_consent_wall(details.description):
        raise PageError("Google's consent page, not the offer")
    return str(response.url), details


def _is_consent_wall(text: str) -> bool:
    """Whether text is Google's consent page, which a request without cookies gets."""
    text = text.replace("\N{RIGHT SINGLE QUOTATION MARK}", "'").lower()
    return any(opening in text for opening in _CONSENT_WALLS)


def _logo(http: httpx2.Client, company: str, candidates: list[str], logos: Path) -> str | None:
    """The company's logo file in logos, downloaded from the first candidate that is one."""
    stem = slugify(company)
    if not stem:
        return None
    cached = sorted(logos.glob(f"{stem}.*"))
    if cached:
        return cached[0].name
    for url in dict.fromkeys(candidates):
        try:
            response = http.get(url)
        except httpx2.HTTPError:
            continue
        kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
        size = len(response.content)
        if response.is_success and kind in IMAGE_TYPES and 0 < size <= MAX_LOGO_BYTES:
            logos.mkdir(parents=True, exist_ok=True)
            (logos / f"{stem}{IMAGE_TYPES[kind]}").write_bytes(response.content)
            return f"{stem}{IMAGE_TYPES[kind]}"
    return None


class _Page(HTMLParser):
    """A page's JSON-LD blocks, its og:image and icon links, and its text, main's apart."""

    def __init__(self) -> None:
        super().__init__()
        self.json_ld: list[str] = []
        self.og_images: list[str] = []
        self.icons: list[str] = []
        self._all: list[str] = []
        self._main: list[str] = []
        self._skipped = self._in_main = 0
        self._in_json = False

    @classmethod
    def of(cls, html: str) -> "_Page":
        page = cls()
        page.feed(html)
        page.close()
        return page

    def text(self) -> str:
        """The main element's text if it has any, else the page's; one line per block."""
        chunks = self._main if "".join(self._main).strip() else self._all
        lines = "".join(chunks).split("\n")
        return "\n".join(" ".join(line.split()) for line in lines if line.strip())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        if tag == "script" and values.get("type", "").lower() == "application/ld+json":
            self._in_json = True
            self.json_ld.append("")
        elif tag == "meta" and "og:image" in (values.get("property"), values.get("name")):
            self.og_images += filter(None, [values.get("content")])
        elif tag == "link" and "icon" in values.get("rel", "").lower().split():
            self.icons += filter(None, [values.get("href")])
        self._skipped += tag in _SKIPPED
        self._in_main += tag == "main"
        self._break(tag)

    def handle_endtag(self, tag: str) -> None:
        self._in_json = self._in_json and tag != "script"
        self._skipped = max(0, self._skipped - (tag in _SKIPPED))
        self._in_main = max(0, self._in_main - (tag == "main"))
        self._break(tag)

    def handle_data(self, data: str) -> None:
        if self._in_json:
            self.json_ld[-1] += data
        elif not self._skipped:
            self._all.append(data)
            if self._in_main:
                self._main.append(data)

    def _break(self, tag: str) -> None:
        if tag in _BLOCKS:
            self._all.append("\n")
            self._main.append("\n")


def _json(block: str) -> Any:
    try:
        return json.loads(block)
    except json.JSONDecodeError:
        return None


def _postings(data: Any) -> Iterator[dict[str, Any]]:
    for item in _list(data):
        if isinstance(item, list):
            yield from _postings(item)
        elif isinstance(item, dict):
            if "JobPosting" in _strings(_at(item, "@type")):
                yield cast(dict[str, Any], item)
            yield from _postings(_at(item, "@graph") or [])


def _place(place: Any) -> str | None:
    address = _at(place, "address")
    if isinstance(address, dict):
        parts = [_at(address, "addressLocality"), _at(address, "postalCode")]
        return " ".join(_strings(parts)) or None
    return _string(address or place)


def _salary(salary: Any) -> str | None:
    """A baseSalary as text: its amount or range, its currency, its unit."""
    value = _at(salary, "value")
    bounds = _strings([_at(value, "minValue"), _at(value, "maxValue")])
    amount = "-".join(bounds) or _string(_at(value, "value")) or _string(value)
    if not amount:
        return _string(salary)
    text = " ".join(_strings([amount, _at(salary, "currency")]))
    unit = _string(_at(value, "unitText"))
    return f"{text} per {unit.lower()}" if unit else text


def _at(value: Any, *keys: str) -> Any:
    """The value at keys in nested JSON objects; None where one is missing."""
    for key in keys:
        value = cast(dict[str, Any], value).get(key) if isinstance(value, dict) else None
    return value


def _list(value: Any) -> list[Any]:
    return cast(list[Any], value) if isinstance(value, list) else [value]


def _strings(value: Any) -> list[str]:
    return [text for item in _list(value) if (text := _string(item))]


def _string(value: Any) -> str | None:
    """A JSON-LD value as text, when it is a string or a number."""
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    return str(value).strip() or None
