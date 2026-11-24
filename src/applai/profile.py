"""The candidate's profile, read from profile.yaml in the data directory.

A profile ApplAI cannot trust is refused as a whole: a missing field, an unknown
fact status or diffusion, two facts with the same id, a repeated YAML key, an
unknown field. The error names the file and the field or line, never the value,
which may be the real candidate's data (ADR 0001).
"""

import os
from collections.abc import Iterable, Iterator, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self, cast

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    ValidationError,
    WrapSerializer,
    model_validator,
)
from pydantic_core import ErrorDetails, PydanticCustomError

DATA_DIR_VARIABLE = "APPLAI_DATA_DIR"
PROFILE_FILE = "profile.yaml"


class ProfileError(Exception):
    """A profile ApplAI refuses to read. The message never holds a value from it."""


class FactStatus(StrEnum):
    VALIDATED = "validated"
    TO_CONFIRM = "to_confirm"
    FORBIDDEN = "forbidden"


class Diffusion(StrEnum):
    """Where a fact may appear."""

    ANYWHERE = "anywhere"
    APPLICATIONS_ONLY = "applications_only"
    NEVER = "never"


class _Section(BaseModel):
    # An unknown field is refused: it is a typo or something ApplAI would silently ignore.
    model_config = ConfigDict(frozen=True, extra="forbid")


class Identity(_Section):
    first_name: str
    last_name: str
    email: str
    phone: str
    city: str
    grammatical_gender: Literal["masculine", "feminine"] | None = None  # of the CV's title line


class BaseAnswer(_Section):
    """An answer most forms ask for, in both languages."""

    fr: str
    en: str


class _ReadOnlyMapping[K, V](Mapping[K, V]):
    """A mapping nobody can change after reading, and that hashes like a frozen model."""

    __slots__ = ("_items",)

    def __init__(self, items: Mapping[K, V]) -> None:
        self._items = dict(items)

    def __getitem__(self, key: K) -> V:
        return self._items[key]

    def __iter__(self) -> Iterator[K]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __hash__(self) -> int:
        return hash(frozenset(self._items.items()))

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._items!r})"


def _dumped_as_dict(value: Mapping[Any, Any], handler: SerializerFunctionWrapHandler) -> Any:
    return handler(dict(value))


class Salary(_Section):
    minimum: int  # yearly, gross
    currency: str


class Targets(_Section):
    roles: tuple[str, ...]
    contracts: tuple[str, ...]  # in priority order
    locations: tuple[str, ...]
    salary: Salary
    # With fixed_term in contracts: a shorter CDD is screened out after Triage.
    fixed_term_min_months: Annotated[int, Field(ge=1)] | None = None


class CvSources(_Section):
    """The CV sources Filling picks from for an offer: files inside the data directory."""

    fr: str | None = None
    en: str | None = None

    @model_validator(mode="after")
    def _has_one(self) -> Self:
        if self.fr is None and self.en is None:
            raise PydanticCustomError("no_cv_source", "needs a fr or an en source")
        return self

    def paths(self) -> dict[str, str]:
        return {lang: path for lang, path in (("fr", self.fr), ("en", self.en)) if path}


class TriageRules(_Section):
    """What the first screening, by code, sets aside (applai.screening)."""

    exclude_title_words: tuple[str, ...]  # a word or a phrase of the title, whole
    max_years_required: int
    stretch_locations: tuple[str, ...]  # pass, and Triage marks them down
    trusted_boards: tuple[str, ...] = ()  # boards ApplAI may read a Google offer from


class Fact(_Section):
    id: Annotated[str, Field(pattern=r"^F[1-9][0-9]*$")]  # cited as #F12 (ADR 0008)
    text: str
    status: FactStatus
    diffusion: Diffusion


class Profile(_Section):
    identity: Identity
    # pydantic reads a mapping as a plain dict, which would leave the profile mutable;
    # it is dumped as a dict again, or pydantic warns, quoting the answers.
    base_answers: Annotated[
        Mapping[str, BaseAnswer],
        AfterValidator(_ReadOnlyMapping),
        WrapSerializer(_dumped_as_dict),
    ]
    targets: Targets
    triage: TriageRules
    facts: tuple[Fact, ...]
    cv: str | None = None  # the CV Filling attaches: a file's path inside the data directory
    # Instead of cv: Filling renders the CV of the offer's language and contract from these.
    cv_sources: CvSources | None = None
    cv_photo_rule: str | None = None  # when the CV has a photo, in the candidate's words

    @model_validator(mode="after")
    def _fact_ids_are_unique(self) -> Self:
        first: dict[str, int] = {}
        for index, fact in enumerate(self.facts):
            if fact.id in first:
                raise PydanticCustomError(
                    "repeated_fact_id",
                    "facts[{index}].id repeats the id of facts[{first}]",
                    {"index": index, "first": first[fact.id]},
                )
            first[fact.id] = index
        return self


def load_profile(data_dir: Path | str | None = None) -> Profile:
    """Read the profile in data_dir, by default the directory APPLAI_DATA_DIR names."""
    if data_dir is None:
        data_dir = os.environ.get(DATA_DIR_VARIABLE) or _no_data_dir()
    path = Path(data_dir) / PROFILE_FILE
    content = _read_yaml(path)
    try:
        profile = Profile.model_validate(content)
    except ValidationError as error:
        problems = [_describe(detail, content) for detail in error.errors()]
    else:
        problems = [
            f"{key}: not a file inside the data directory"
            for key, path in _cv_paths(profile)
            if _inside(data_dir, path) is None
        ]
        if not problems:
            return profile
    # Raised outside the except clause, so the ValidationError, whose text holds the
    # refused values, is not even chained to the ProfileError.
    raise _refused(path, problems)


def _cv_paths(profile: Profile) -> list[tuple[str, str]]:
    """The profile's CV files, each with the key that names it."""
    found = [("cv", profile.cv)] if profile.cv is not None else []
    sources = profile.cv_sources.paths() if profile.cv_sources else {}
    return found + [(f"cv_sources.{lang}", path) for lang, path in sources.items()]


def _inside(data_dir: Path | str, relative: str) -> Path | None:
    """The file `relative` names, or None when it is not a file inside data_dir.

    Resolved first: neither "..", an absolute path nor a link leads out of data_dir.
    """
    root = Path(data_dir).resolve()
    try:
        path = (root / relative).resolve()
        return path if path.is_relative_to(root) and path.is_file() else None
    except (OSError, ValueError):
        return None


def cv_file(data_dir: Path | str, profile: Profile) -> Path | None:
    """The profile's fixed CV, or None when it has none or it is not a file inside data_dir."""
    return None if profile.cv is None else _inside(data_dir, profile.cv)


def cv_source_file(data_dir: Path | str, profile: Profile, lang: str) -> Path | None:
    """The profile's CV source in lang, or None when it has none or it is not inside data_dir."""
    path = profile.cv_sources.paths().get(lang) if profile.cv_sources else None
    return None if path is None else _inside(data_dir, path)


def _no_data_dir() -> str:
    raise ProfileError(
        f"{DATA_DIR_VARIABLE} is not set: set it to the candidate's data directory,"
        " or pass that directory to load_profile()."
    )


def _refused(path: Path, problems: Iterable[str]) -> ProfileError:
    return ProfileError("\n  ".join([f"{path}: profile refused", *problems]))


_UNKNOWN_FIELD = "<unknown field>"


def _base_answer_names(content: object) -> list[object]:
    if not isinstance(content, dict):
        return []
    answers = cast(dict[str, Any], content).get("base_answers")
    return list(cast(dict[object, Any], answers)) if isinstance(answers, dict) else []


def _describe(detail: ErrorDetails, content: object) -> str:
    """Where a problem is and what it is, without any key or value taken from the file."""
    loc = detail["loc"]
    if detail["type"] == "extra_forbidden":
        # The unknown field's name comes from the file: say where it is, not what it is.
        loc = (*loc[:-1], _UNKNOWN_FIELD)
    if loc[:1] == ("base_answers",) and len(loc) > 1:
        # The name of an answer comes from the file too: say its position.
        names = _base_answer_names(content)
        position = names.index(loc[1]) if loc[1] in names else _UNKNOWN_FIELD
        loc = ("base_answers", position, *loc[2:])
    field = ""
    for part in loc:
        field += f"[{part}]" if isinstance(part, int) else f".{part}" if field else part
    return f"{field}: {detail['msg']}" if field else detail["msg"]


class _RepeatedKeyError(Exception):
    def __init__(self, line: int) -> None:
        super().__init__(line)
        self.line = line


class _Loader(yaml.SafeLoader):
    """SafeLoader that refuses a repeated key, where PyYAML would keep the last one."""


_MERGE_TAG = "tag:yaml.org,2002:merge"


def _mapping_without_repeated_keys(loader: _Loader, node: yaml.MappingNode) -> dict[Any, Any]:
    seen: set[tuple[str, str]] = set()
    for key_node, _ in node.value:
        if not isinstance(key_node, yaml.ScalarNode) or key_node.tag == _MERGE_TAG:
            continue  # merge keys and complex keys are left to PyYAML
        key = (key_node.tag, key_node.value)
        if key in seen:
            raise _RepeatedKeyError(key_node.start_mark.line + 1)
        seen.add(key)
    return loader.construct_mapping(node)


_Loader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping_without_repeated_keys
)


def _read_yaml(path: Path) -> object:
    # PyYAML's own messages can quote the file (a tag, a line), so only the position is kept.
    try:
        stream = path.open("rb")
    except OSError as error:
        raise _refused(path, [f"cannot be read ({error.strerror})"]) from None
    with stream:
        try:
            return yaml.load(stream, Loader=_Loader)
        except _RepeatedKeyError as error:
            problem = f"line {error.line}: repeats a key of the same mapping"
        except yaml.MarkedYAMLError as error:
            mark = error.problem_mark or error.context_mark
            problem = f"line {mark.line + 1}: not valid YAML" if mark else "not valid YAML"
        except yaml.YAMLError:
            problem = "not valid YAML"
    raise _refused(path, [problem])
