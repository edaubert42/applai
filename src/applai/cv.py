"""The candidate's CV, rendered from cv/cv_{lang}.yaml in the data directory to a one-page A4 PDF.

The source is refused as a whole when a required key is missing: the error names the key,
never its value, which may be the real candidate's data (ADR 0001). The HTML comes from
templates/cv.html.j2 and Chromium (Playwright) prints it. The checks that go with the PDF are
the page count, em or en dashes, and the free space under the last line. No agent: code picks
the language and the contract of an offer (offer_language, offer_contract), this module renders.
"""

import base64
import copy
import json
import re
from collections.abc import Collection, Iterator, Mapping, Sequence
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


def _contracts_named(title: str, contract: str | None) -> Iterator[str]:
    """The contract the offer's own text names, then the one its title names, if any."""
    for text in (contract or "", title):
        folded = f" {fold(text)} "
        found = next((word for key, word in _CONTRACTS.items() if f" {key} " in folded), None)
        if found is not None:
            yield found


def offer_contract_type(title: str, contract: str | None) -> str | None:
    """The offer's contract in the profile's words (permanent, fixed_term, internship,
    apprenticeship): the offer's own text, then its title's. None when neither names one."""
    return next(_contracts_named(title, contract), None)


def offer_contract(
    title: str, contract: str | None, headlines: Collection[str], targets: Sequence[str]
) -> str:
    """The offer's contract in the profile's words: the offer's own, then its title's.

    Unknown or without a headline: the first of targets that has one. CvError when none does.
    """
    for found in _contracts_named(title, contract):
        if found in headlines:
            return found
    for target in targets:
        if target in headlines:
            return target
    raise CvError("the CV source has no headline for any of the profile's target contracts")


def source_headlines(data_dir: Path | str, source: Path | str) -> list[str]:
    """The contracts the CV source (a path inside data_dir) has a headline for."""
    return list(_load_source(Path(data_dir) / source).headlines)


def source_dump(data_dir: Path | str, source: Path | str) -> dict[str, Any]:
    """The CV source (a path inside data_dir) as plain data, where change paths point."""
    return _load_source(Path(data_dir) / source).model_dump(mode="json")


@dataclass(frozen=True)
class Change:
    """A change to a copy of a source: reorder a list, reword or drop one of its bullets.

    old is the bullet's text, or a reorder's items as a JSON list; new is the new text, or the
    items in their new order; none for a drop. Applied only where the source still has old.
    """

    op: Literal["reorder", "reword", "drop"]
    path: str  # skills, experience[0].bullets, experience[1].roles[0].bullets[2]…
    old: str
    new: str | None


_PATH = re.compile(r"[a-z]+(\[\d+\])*(\.[a-z]+(\[\d+\])*)*")


def _parts(path: str) -> list[str | int]:
    if not _PATH.fullmatch(path):
        raise CvError(f"{path}: not a path")
    return [int(i) if i else key for key, i in re.findall(r"([a-z]+)|\[(\d+)\]", path)]


def _at(data: Any, parts: Sequence[str | int]) -> Any:
    for part in parts:
        if isinstance(part, int) and isinstance(data, list) and part < len(cast(list[Any], data)):
            data = cast(list[Any], data)[part]
        elif isinstance(part, str) and isinstance(data, dict) and part in data:
            data = cast(dict[str, Any], data)[part]
        else:
            return None
    return data


def listed(data: dict[str, Any], path: str) -> list[str] | None:
    """The texts of the list a reorder may name: a list of bullets, or skills (its lines)."""
    parts = _parts(path)
    found = _at(data, parts)
    if path == "skills" and isinstance(found, list):
        return [f"{skill['label']}: {skill['text']}" for skill in cast(list[dict[str, str]], found)]
    if parts[-1] == "bullets" and isinstance(found, list):
        return list(cast(list[str], found))
    return None


def bullet(data: dict[str, Any], path: str) -> str | None:
    """The bullet a reword or a drop may name, or None."""
    parts = _parts(path)
    found = _at(data, parts)
    return found if parts[-2:-1] == ["bullets"] and isinstance(found, str) else None


def apply_changes(data: dict[str, Any], changes: Sequence[Change]) -> dict[str, Any]:
    """A copy of the source's data with the changes; CvError where it no longer has their old."""
    edits: dict[str, dict[int, str | None]] = {}
    orders: dict[str, list[str]] = {}
    for change in changes:
        if change.op == "reorder":
            if listed(data, change.path) != json.loads(change.old):
                raise CvError(f"{change.path}: the CV source changed since it was tailored")
            orders[change.path] = json.loads(change.new or "[]")
        else:
            if bullet(data, change.path) != change.old:
                raise CvError(f"{change.path}: the CV source changed since it was tailored")
            list_path, _, index = change.path.rpartition("[")
            edits.setdefault(list_path, {})[int(index[:-1])] = change.new
    data = copy.deepcopy(data)
    for path in edits.keys() | orders.keys():
        parts = _parts(path)
        items: list[Any] = _at(data, parts)
        texts, new_order = listed(data, path) or [], orders.get(path)
        order = range(len(items)) if new_order is None else [texts.index(t) for t in new_order]
        changed = edits.get(path, {})
        kept = [changed.get(i, items[i]) for i in order]
        _at(data, parts[:-1])[parts[-1]] = [item for item in kept if item is not None]
    return data


def render_cv(
    data_dir: Path | str, lang: Lang, contract: str, photo: bool = False,
    source_file: Path | str | None = None, headline: str | None = None,
    changes: Sequence[Change] = (),
) -> RenderedCv:  # fmt: skip
    """The PDF of the CV in lang for contract, with a photo or not, and its checks.

    The source is cv/cv_{lang}.yaml, or source_file (a path inside data_dir) when given. A
    tailored CV is a copy of it with the changes and the headline in place of the contract's.
    """
    directory = Path(data_dir) / CV_DIR
    path = Path(data_dir) / source_file if source_file else directory / f"cv_{lang}.yaml"
    source = _load_source(path)
    if contract not in source.headlines:
        raise CvError(f"{path}: headlines.{contract}: no headline")
    if changes:
        source = CvSource.model_validate(apply_changes(source.model_dump(mode="json"), changes))
    if headline:
        source = source.model_copy(update={"headlines": {**source.headlines, contract: headline}})
    photo_uri = None
    if photo:
        photo_file = directory / PHOTO_FILE
        if not photo_file.is_file():
            raise CvError(f"{photo_file}: the photo is not a file")
        encoded = base64.b64encode(photo_file.read_bytes()).decode("ascii")
        photo_uri = f"data:image/jpeg;base64,{encoded}"
    html = _html(source, lang, contract, photo_uri)
    return print_pdf(html)


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


def print_pdf(html: str, margins_mm: tuple[int, int, int, int] = _MARGINS_MM) -> RenderedCv:
    """The A4 PDF of html, whose @page has these margins (top, right, bottom, left), with checks."""
    width_mm = _PAGE_MM[0] - margins_mm[1] - margins_mm[3]
    height_mm = _PAGE_MM[1] - margins_mm[0] - margins_mm[2]
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
