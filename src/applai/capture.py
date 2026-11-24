"""Form capture, its first step: where an accepted offer's application form lives.

find_form() runs, with no model, right after the candidate accepts an offer. The form's URL
is the offer's URL when it is on a known ATS host; else the first link of the offer's page to
one; else the page's own apply link, for boards that host the form themselves. Only pages read
over HTTP are read (applai.details): an offer read by the extension, the LinkedIn reader or
from Google gets an error asking the candidate to paste the URL, as does a page with no link.
The application stays created: it moves when its form's fields are read.
"""

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx2
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from applai.details import on_linkedin, read_by_extension
from applai.models import Application, Offer

# One list, no ATS-specific code yet: which ATS deserve a script comes from the first offers.
ATS_HOSTS = ("ashbyhq.com", "lever.co", "greenhouse.io", "workable.com", "teamtailor.com",
             "welcometothejungle.com", "smartrecruiters.com", "myworkdayjobs.com",
             "myworkdaysite.com", "recruitee.com")  # fmt: skip
GOOGLE = "alert_google"  # the source of offers from Google's alerts (applai.alerts)
PASTE = "paste the form's URL"
_APPLY = re.compile(r"\b(postuler|candidater|apply)\b", re.IGNORECASE)


class FormNotFound(Exception):
    """An offer whose form's URL was not found; its message ends with PASTE."""


def find_form(engine: Engine, application_id: int, http: httpx2.Client) -> None:
    """Record on the application its form's URL, or why it was not found; commits."""
    with Session(engine) as session:
        application = session.get_one(Application, application_id)
        offer = session.get_one(Offer, application.offer_id)
        try:
            application.form_url, application.form_url_error = form_url(offer, http), None
        except FormNotFound as error:
            application.form_url, application.form_url_error = None, str(error)
        session.commit()


def form_url(offer: Offer, http: httpx2.Client) -> str:
    """The URL of the offer's form; raises FormNotFound."""
    if on_ats(offer.url):
        return offer.url
    if on_linkedin(offer.url) or read_by_extension(offer.url) or offer.source == GOOGLE:
        raise FormNotFound(f"ApplAI does not read this offer's page: {PASTE}.")
    try:
        response = http.get(offer.url)
        response.raise_for_status()
    except httpx2.HTTPStatusError as error:
        why = f"answered HTTP {error.response.status_code}"
        raise FormNotFound(f"The offer's page {why}: {PASTE}.") from None
    except httpx2.HTTPError as error:  # a message may hold the URL, which may hold a token
        why = f"was not read ({type(error).__name__})"
        raise FormNotFound(f"The offer's page {why}: {PASTE}.") from None
    page = str(response.url)  # after redirects, which may end on the ATS
    links = [(urljoin(page, href), text) for href, text in _Links.of(response.text)]
    links = [(url, text) for url, text in links if urlsplit(url).scheme in ("http", "https")]
    found = (
        ([page] if on_ats(page) else [])
        + [url for url, _ in links if on_ats(url) and urlsplit(url).path.strip("/")]
        + [url for url, text in links if _APPLY.search(text)]
    )
    if not found:
        raise FormNotFound(f"The offer's page links to no form: {PASTE}.")
    return found[0]


def on_ats(url: str) -> bool:
    """Whether url is on a known ATS host."""
    host = urlsplit(url).hostname or ""
    return any(host == ats or host.endswith(f".{ats}") for ats in ATS_HOSTS)


class _Links(HTMLParser):
    """A page's links, each with its text and aria-label, in the page's order."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._open: list[str] | None = None  # the text of the link being read

    @classmethod
    def of(cls, html: str) -> list[tuple[str, str]]:
        page = cls()
        page.feed(html)
        page.close()
        return page.links

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        if tag == "a" and values.get("href"):
            self.links.append((values["href"].strip(), ""))
            self._open = [values.get("aria-label", "")]

    def handle_data(self, data: str) -> None:
        if self._open is not None:
            self._open.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._open is not None:
            href, _ = self.links[-1]
            self.links[-1] = (href, " ".join(self._open))
            self._open = None
