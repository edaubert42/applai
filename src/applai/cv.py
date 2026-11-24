"""The candidate's CV, rendered from cv/cv_{lang}.yaml in the data directory to a one-page A4 PDF.

The source is refused as a whole when a required key is missing: the error names the key,
never its value, which may be the real candidate's data (ADR 0001). The HTML comes from
templates/cv.html.j2 and Chromium (Playwright) prints it. The checks that go with the PDF are
the page count, em or en dashes, and the free space under the last line. No agent: code picks
the language and the contract of an offer (offer_language, offer_contract), this module renders.
"""

import base64
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Self, cast

import yaml
from jinja2 import Environment, PackageLoader
from playwright.sync_api import sync_playwright
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import ErrorDetails

from applai.text import fold

CV_DIR = "cv"
PHOTO_FILE = "photo.jpg"
Lang = Literal["fr", "en"]

# Layout of the one-page CV.
_PAGE_MM = (210, 297)
_MARGINS_MM = (8, 13, 7, 13)  # top, right, bottom, left
_LAYOUT = {
    "fs": "9.5pt",
    "lh": "1.22",
    "gap": "0.58",
    "pm": " ".join(f"{margin}mm" for margin in _MARGINS_MM),
    "ps": "25mm",
    "edu1": True,
    "gh_inline": True,
}
_DASHES = (chr(0x2014), chr(0x2013))  # em and en dash
_NO_BREAK_SPACE = chr(0xA0)
_PX_PER_MM = 96 / 25.4


class CvError(Exception):
    """A CV that cannot be rendered. The message never holds a value from the source."""


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Contact(_Section):
    city: str
    email: str
    phone: str


class Links(_Section):
    linkedin: Annotated[str, Field(pattern=r"^https?://")] | None = None
    github: Annotated[str, Field(pattern=r"^https?://")] | None = None


class Titles(_Section):
    skills: str
    experience: str
    projects: str
    education: str
    langcert: str


class Labeled(_Section):
    label: str
    text: str


class Role(_Section):
    title: str
    dates: str
    bullets: tuple[str, ...]


class Experience(_Section):
    title: str | None = None
    company: str  # "Company, City"
    dates: str
    bullets: tuple[str, ...] | None = None
    intro: str | None = None
    roles: tuple[Role, ...] | None = None

    @model_validator(mode="after")
    def _bullets_or_intro_and_roles(self) -> Self:
        if self.roles is not None and self.intro is not None and self.bullets is None:
            return self
        if self.bullets is not None and self.title is not None and self.roles is None:
            return self
        raise ValueError("needs title and bullets, or intro and roles")


class Project(_Section):
    name: str
    desc: str
    tech: str
    bullets: tuple[str, ...] = ()


class Education(_Section):
    school: str  # "School, City"
    degree: str
    note: str | None = None
    dates: str


class CvSource(_Section):
    name: str
    contact: Contact
    links: Links = Links()
    headlines: Mapping[str, str]
    summary: str
    summaries: Mapping[str, str] = {}
    titles: Titles
    skills: tuple[Labeled, ...]
    langcert: tuple[Labeled, ...]
    experience: tuple[Experience, ...]
    projects: tuple[Project, ...]
    education: tuple[Education, ...]
    interests: Labeled | None = None  # one more line of langcert


@dataclass(frozen=True)
class CvChecks:
    pages: int
    dashes: int  # em and en dashes (U+2014, U+2013) in the text
    free_mm: float  # under the last line; 0 when the CV has several pages

    @property
    def problems(self) -> list[str]:
        found: list[str] = []
        if self.pages != 1:
            found.append(f"{self.pages} pages, expected 1")
        if self.dashes:
            found.append(f"{self.dashes} em or en dash found (U+2014, U+2013)")
        return found


@dataclass(frozen=True)
class RenderedCv:
    pdf: bytes
    checks: CvChecks


# Words of one language that are not words of the other, counted to tell an offer's language.
_FRENCH_WORDS = frozenset({
    "le", "la", "les", "des", "du", "un", "une", "et", "pour", "avec", "dans", "nous", "vous",
    "votre", "notre", "vos", "nos", "sur", "au", "aux", "que", "qui", "est", "sont", "ou", "ce",
    "cette", "ses", "par",
})  # fmt: skip
_ENGLISH_WORDS = frozenset({
    "the", "and", "of", "to", "for", "with", "you", "your", "our", "we", "are", "is", "will", "an",
    "this", "that", "be", "as", "or", "from", "by", "their",
})  # fmt: skip
# What an offer says of its contract, folded, in the profile's contract words.
_CONTRACTS = {
    **dict.fromkeys(["cdi", "permanent", "full time"], "permanent"),
    **dict.fromkeys(["stage", "internship", "intern"], "internship"),
    **dict.fromkeys(["alternance", "apprentissage", "apprenticeship"], "apprenticeship"),
    **dict.fromkeys(["cdd", "fixed term"], "fixed_term"),
}


def offer_language(title: str, description: str | None, sources: Collection[Lang]) -> Lang:
    """fr or en, by the common words of the offer's text; a tie or no text gives fr.

    Only a language the profile has a source for: with one source, that one.
    """
    words = fold(f"{title} {description or ''}").split()
    english = sum(word in _ENGLISH_WORDS for word in words)
    french = sum(word in _FRENCH_WORDS for word in words)
    found: Lang = "en" if english > french else "fr"
    if found in sources:
        return found
    return "fr" if "fr" in sources else "en"


def offer_contract(
    title: str, contract: str | None, headlines: Collection[str], targets: Sequence[str]
) -> str:
    """The offer's contract in the profile's words: the offer's own, then its title's.

    Unknown or without a headline: the first of targets that has one. CvError when none does.
    """
    for text in (contract or "", title):
        folded = f" {fold(text)} "
        found = next((word for key, word in _CONTRACTS.items() if f" {key} " in folded), None)
        if found in headlines:
            return cast(str, found)
    for target in targets:
        if target in headlines:
            return target
    raise CvError("the CV source has no headline for any of the profile's target contracts")


def source_headlines(data_dir: Path | str, source: Path | str) -> list[str]:
    """The contracts the CV source (a path inside data_dir) has a headline for."""
    return list(_load_source(Path(data_dir) / source).headlines)


def render_cv(
    data_dir: Path | str, lang: Lang, contract: str, photo: bool = False,
    source_file: Path | str | None = None,
) -> RenderedCv:  # fmt: skip
    """The PDF of the CV in lang for contract, with a photo or not, and its checks.

    The source is cv/cv_{lang}.yaml, or source_file (a path inside data_dir) when given.
    """
    directory = Path(data_dir) / CV_DIR
    path = Path(data_dir) / source_file if source_file else directory / f"cv_{lang}.yaml"
    source = _load_source(path)
    if contract not in source.headlines:
        raise CvError(f"{path}: headlines.{contract}: no headline")
    photo_uri = None
    if photo:
        photo_file = directory / PHOTO_FILE
        if not photo_file.is_file():
            raise CvError(f"{photo_file}: the photo is not a file")
        encoded = base64.b64encode(photo_file.read_bytes()).decode("ascii")
        photo_uri = f"data:image/jpeg;base64,{encoded}"
    html = _html(source, lang, contract, photo_uri)
    return _print(html)


def _load_source(path: Path) -> CvSource:
    try:
        content: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise CvError(f"{path}: cannot be read ({error.strerror})") from None
    except yaml.YAMLError:
        raise CvError(f"{path}: not valid YAML") from None  # PyYAML's message can quote it
    try:
        return CvSource.model_validate(content)
    except ValidationError as error:
        problems = [_describe(detail) for detail in error.errors()]
    raise CvError("\n  ".join([f"{path}: CV refused", *problems]))


def _describe(detail: ErrorDetails) -> str:
    """Where a problem is and what it is, without any value taken from the file."""
    loc = detail["loc"]
    if detail["type"] == "extra_forbidden":
        loc = (*loc[:-1], "<unknown field>")  # the name comes from the file too
    key = ""
    for part in loc:
        key += f"[{part}]" if isinstance(part, int) else f".{part}" if key else part
    return f"{key}: {detail['msg']}" if key else detail["msg"]


def _html(source: CvSource, lang: Lang, contract: str, photo_uri: str | None) -> str:
    data = _typography(source.model_dump(), lang)
    contact, links = source.contact, source.links
    contacts = [
        {"kind": "email", "href": f"mailto:{contact.email}", "text": contact.email},
        {"kind": "phone", "href": f"tel:{re.sub(r'[^+0-9]', '', contact.phone)}",
         "text": contact.phone},
    ]  # fmt: skip
    if links.linkedin:
        contacts.append({"kind": "link", "href": links.linkedin, "text": _shown(links.linkedin)})
    github = {"href": links.github, "text": _shown(links.github)} if links.github else None
    langcert = [*data["langcert"], *([data["interests"]] if data["interests"] else [])]
    summary = data["summaries"].get(contract, data["summary"])
    environment = Environment(loader=PackageLoader("applai"), autoescape=True)
    return environment.get_template("cv.html.j2").render(
        **_LAYOUT,
        lang=lang,
        photo=photo_uri,
        name=data["name"],
        headline=data["headlines"][contract],
        location=data["contact"]["city"],
        contacts=contacts,
        github=github,
        summary=_bound_last_sentence(summary),
        titles=data["titles"],
        skills=data["skills"],
        langcert=langcert,
        experience=[_with_place(entry, "company") for entry in data["experience"]],
        projects=data["projects"],
        education=[_with_place(entry, "school") for entry in data["education"]],
    )


def _shown(url: str) -> str:
    return re.sub(r"^https?://(www\.)?|/$", "", url)


def _with_place(entry: dict[str, Any], key: str) -> dict[str, Any]:
    """The entry with its "Name, Place" key split into the name and `loc`."""
    name, _, place = cast(str, entry[key]).rpartition(", ")
    return {**entry, key: name, "loc": place} if name else {**entry, "loc": ""}


def _bound_last_sentence(summary: str) -> str:
    """The summary with no line break inside its last sentence, when it has several."""
    *before, last = re.split(r"(?<=[.!?])\s+", summary.strip())
    if not before:
        return summary
    return " ".join([*before, last.replace(" ", _NO_BREAK_SPACE)])


# French sets a no-break space before ":", ";" and "%". Not in "https://" or "10:30".
_BEFORE_PUNCTUATION = re.compile(r"(?<=\S)[ \xa0]?(:(?![/\d])|;|%)")


def _typography(value: Any, lang: Lang) -> Any:
    if isinstance(value, str):
        return _BEFORE_PUNCTUATION.sub(_NO_BREAK_SPACE + r"\1", value) if lang == "fr" else value
    if isinstance(value, dict):
        return {key: _typography(item, lang) for key, item in cast(dict[str, Any], value).items()}
    if isinstance(value, (list, tuple)):
        return [_typography(item, lang) for item in cast(list[Any], value)]
    return value


def _print(html: str) -> RenderedCv:
    width_mm = _PAGE_MM[0] - _MARGINS_MM[1] - _MARGINS_MM[3]
    height_mm = _PAGE_MM[1] - _MARGINS_MM[0] - _MARGINS_MM[2]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(html)
            page.emulate_media(media="print")
            # What the PDF's content box is: the width inside the margins.
            page.add_style_tag(content=f"html {{ width: {width_mm}mm; }}")
            text = page.inner_text("body")
            bottom_px: float = page.evaluate(
                "Math.max(...Array.from(document.body.children)"
                ".map(element => element.getBoundingClientRect().bottom))"
            )
            pdf = page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
        finally:
            browser.close()
    pages = len(re.findall(rb"/Type\s*/Page\b", pdf))
    # The layout is measured whole, not page by page: where it breaks over several pages is
    # not known, and a CV of several pages fails anyway.
    free_mm = max(0.0, height_mm - bottom_px / _PX_PER_MM) if pages == 1 else 0.0
    dashes = sum(text.count(dash) for dash in _DASHES)
    return RenderedCv(pdf, CvChecks(pages, dashes, round(free_mm, 1)))
