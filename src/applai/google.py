"""Google offers: parse Google's page and read only from employer, ATS or trusted boards."""

import re
from collections.abc import Sequence
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2
from sqlalchemy.orm import Session

from applai import details
from applai.linkedin import LinkedInError, read_linkedin_offer
from applai.models import Offer, OfferState
from applai.orchestrator import Actor, move
from applai.profile import Profile
from applai.text import fold

BOARD_RULE, UNTRUSTED_ONLY = "board", "posted only on untrusted sites"
GOOGLE_SITES = ("google.com", "google.fr")
# Career-page host suffixes of the 25 supported ATS platforms (Taleo through Jobaffinity).
ATS_DOMAINS = frozenset({
    "taleo.net", "myworkdayjobs.com", "myworkdaysite.com", "successfactors.com", "sapsf.com",
    "successfactors.eu", "sapsf.eu", "icims.com", "oraclecloud.com", "csod.com", "greenhouse.io",
    "lever.co", "ashbyhq.com", "smartrecruiters.com", "workable.com", "teamtailor.com",
    "recruitee.com", "personio.com", "personio.de", "bamboohr.com", "jobvite.com", "breezy.hr",
    "welcomekit.co", "talent-soft.com", "digitalrecruiters.com", "digitalrecruiters.net",
    "flatchr.io", "taleez.com", "beetween.com", "beetween.fr", "softy.pro", "jobaffinity.fr",
    "jobaffinity.com",
})  # fmt: skip
_ALIASES = {"francetravail": ("francetravail", "poleemploi"), "wttj": ("welcometothejungle",)}
_SLDS = frozenset({"co", "com", "org", "net", "gov", "gouv", "ac", "edu"})
# No "template": Google keeps each offer's details and apply links in one.
_SKIP = frozenset({"head", "script", "style", "noscript", "svg"})
_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                   "source", "wbr"})  # fmt: skip
_APPLY = re.compile(r"^(?:postuler(?:\s+directement)?\s+sur|apply(?:\s+directly)?\s+on)\s+.+", re.I)
_HEADINGS = frozenset({"", "postuler", "postuler sur", "apply", "apply on", "candidater"})
_AGE = re.compile(r"\b(?:il y a|ago|jour|jours|day|days|heure|heures|hour|hours)\b")
_CONTRACTS = frozenset({
    "cdi", "cdd", "stage", "alternance", "freelance", "independant", "a plein temps", "plein temps",
    "temps plein", "temps partiel", "full time", "part time", "internship", "contractor",
    "permanent", "fixed term", "apprenticeship",
})  # fmt: skip


class _Node:
    def __init__(self, tag: str, href: str = "", label: str = "") -> None:
        self.tag, self.href, self.label = tag, href, label
        self.children: list[_Node] = []
        self.texts: list[str] = []

    def apply_url(self) -> str | None:
        if self.tag != "a" or not self.href.startswith(("http://", "https://")):
            return None
        txt = " ".join(self.texts + [t for c in self.children for t in c.raw_texts()])
        return self.href if _APPLY.match(txt) or _APPLY.match(self.label) else None

    def raw_texts(self) -> list[str]:
        if self.apply_url():
            return []
        return [*self.texts, *(t for c in self.children for t in c.raw_texts())]

    def plain(self) -> str:
        return fold(" ".join(self.raw_texts()))

    def links(self) -> list[str]:
        return [u] if (u := self.apply_url()) else [u for c in self.children for u in c.links()]


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.root = _Node("root")
        self._stack, self._skip = [self.root], 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip += 1
        elif not self._skip:
            a, href = {k: v or "" for k, v in attrs}, ""
            if tag == "a" and (href := a.get("href", "").strip()):
                p = urlsplit(href)
                if p.path == "/url" and (not p.hostname or on_google(href)):
                    q = parse_qs(p.query)
                    href = (q.get("q") or q.get("url") or [href])[0].strip()
            node = _Node(tag, href, " ".join((a.get("aria-label") or a.get("title") or "").split()))
            self._stack[-1].children.append(node)
            if tag not in _VOID:
                self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif not self._skip and tag in (n.tag for n in self._stack[1:]):
            while self._stack.pop().tag != tag:
                pass

    def handle_data(self, data: str) -> None:
        if not self._skip and (text := " ".join(data.split())):
            self._stack[-1].texts.append(text)


def on_google(url: str) -> bool:
    """Whether an offer's URL is a Google search page."""
    h = (urlsplit(url).hostname or "").rstrip(".")
    return any(h == s or h.endswith(f".{s}") for s in GOOGLE_SITES)


def is_ats_host(host: str) -> bool:
    """Whether host belongs to one of the known ATS career-page domains."""
    h = host.rstrip(".").lower()
    return any(h == d or h.endswith(f".{d}") for d in ATS_DOMAINS)


def order_allowed_links(
    urls: Sequence[str], company: str, trusted_boards: Sequence[str]
) -> list[str]:
    """Allowed apply URLs in priority order: employer/ATS, then LinkedIn, then trusted boards."""
    company_key = fold(company).replace(" ", "")
    trusted = {
        a for b in trusted_boards if (k := fold(b).replace(" ", ""))
        for a in (k, *_ALIASES.get(k, ()))
    }  # fmt: skip
    buckets: tuple[list[str], list[str], list[str]] = ([], [], [])
    for url in urls:
        if not (host := (urlsplit(url).hostname or "").rstrip(".")):
            continue
        parts = host.split(".")
        idx = -3 if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in _SLDS else -2
        compact = fold(parts[idx]).replace(" ", "") if len(parts) >= 2 else ""
        employer = len(company_key) >= 2 and compact == company_key
        if details.on_linkedin(url):
            if "linkedin" in trusted or employer:
                buckets[1].append(url)
        elif employer or is_ats_host(host):
            buckets[0].append(url)
        elif compact in trusted:
            buckets[2].append(url)
    return list(dict.fromkeys([*buckets[0], *buckets[1], *buckets[2]]))


def parse_google_offer(
    html: str, title: str, company: str, location: str | None = None
) -> tuple[tuple[str, ...], str | None, str | None]:
    """Find this offer's apply links, location and contract in Google's HTML."""
    tree = _Tree()
    tree.feed(html)
    tree.close()
    page_text = " ".join(tree.root.raw_texts())
    if not page_text and not tree.root.links():
        raise details.PageError("the page has no text")
    if details.is_consent_wall(page_text):
        raise details.PageError("Google's consent page, not the offer")
    ft, fc, candidates = fold(title), fold(company), list[_Node]()

    def matches(n: _Node) -> bool:
        return bool(ft) and ft in (t := n.plain()) and (not fc or fc in t)

    def walk(node: _Node) -> bool:
        below = any(walk(c) for c in node.children)
        steals = any(matches(c) and not c.links() for c in node.children) and any(
            c.links() and not matches(c) and c.plain() not in _HEADINGS for c in node.children
        )
        if not below and matches(node) and node.links() and not steals:
            candidates.append(node)
            return True
        return below

    walk(tree.root)
    if not candidates:
        raise details.PageError("this offer was not found on Google's page")
    needles = [fold(p) for p in (location, location.split(",")[0])] if location else []
    chosen = next((n for s in needles if s for n in candidates if s in n.plain()), candidates[0])
    loc, contract, skip = None, None, {ft, fc} | _HEADINGS
    for piece in (p.strip() for r in chosen.raw_texts() for p in re.split(r"\s*[·•|]\s*", r)):
        if (f := fold(piece)) and f not in skip and not f.startswith("via "):
            if f in _CONTRACTS:
                contract = contract or piece
            elif loc is None and not _AGE.search(f):
                loc = piece
    return tuple(chosen.links()), loc, contract


def read_google_text(
    session: Session, offer: Offer, html: str, profile: Profile,
    http: httpx2.Client, now: datetime,
) -> str:  # fmt: skip
    """The full text of a Google offer, from its first allowed link that reads; else PageError.

    Reads the links as describe_google_offer does, LinkedIn's pacing included, and changes
    nothing on the offer: applai.reread decides what a text read later replaces.
    """
    links, _, _ = parse_google_offer(html, offer.title, offer.company, offer.location)
    allowed = order_allowed_links(links, offer.company, profile.triage.trusted_boards)
    if not allowed:
        raise details.PageError(UNTRUSTED_ONLY)
    error = "no allowed link could be read"
    for url in allowed:
        try:
            if details.on_linkedin(url):
                return read_linkedin_offer(session, offer, url, now)
            return details.read_description(http, url)
        except (details.PageError, LinkedInError) as failure:
            error = str(failure)
    raise details.PageError(error)


def describe_google_offer(
    session: Session, offer: Offer, html: str, profile: Profile,
    data_dir: Path, http: httpx2.Client, now: datetime,
) -> bool:  # fmt: skip
    """Describe a Google offer from its allowed apply links, or screen it out / fall back."""
    links, loc, contract = parse_google_offer(html, offer.title, offer.company, offer.location)
    allowed = order_allowed_links(links, offer.company, profile.triage.trusted_boards)
    if not allowed:
        move(session, offer, OfferState.SCREENED_OUT, Actor.WATCH)
        offer.screened_rule, offer.detail_error = BOARD_RULE, UNTRUSTED_ONLY
        return False
    last_error = "no allowed link could be read"
    for url in allowed:
        try:
            if details.on_linkedin(url):
                offer.description = read_linkedin_offer(session, offer, url, now)
                offer.detail_error = None
                move(session, offer, OfferState.DESCRIBED, Actor.WATCH)
            else:
                details.describe_offer(session, offer, data_dir, http, url=url)
            break
        except (details.PageError, LinkedInError) as error:
            last_error = str(error)
    offer.location, offer.contract = offer.location or loc, offer.contract or contract
    if offer.state == OfferState.NEW:
        offer.detail_error = last_error
        details.describe_from_alert(session, offer)
    return True
