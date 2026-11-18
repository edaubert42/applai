"""The candidate's profile, read from profile.yaml in the data directory.

A profile ApplAI cannot trust is refused as a whole: a missing field, an unknown
fact status or diffusion, two facts with the same id, a repeated YAML key, an
unknown field. The error names the file and the field or line, never the value,
which may be the real candidate's data (ADR 0001).
"""

import os
from collections.abc import Iterable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Self, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
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

    FREE = "free"
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


class BaseAnswer(_Section):
    """An answer most forms ask for, in both languages."""

    fr: str
    en: str


class Salary(_Section):
    minimum: int  # yearly, gross
    currency: str


class Targets(_Section):
    roles: tuple[str, ...]
    contracts: tuple[str, ...]  # in priority order
    locations: tuple[str, ...]
    salary: Salary


class Fact(_Section):
    id: Annotated[str, Field(pattern=r"^F[1-9][0-9]*$")]  # cited as #F12 (ADR 0008)
    text: str
    status: FactStatus
    diffusion: Diffusion


class Profile(_Section):
    identity: Identity
    base_answers: Mapping[str, BaseAnswer]
    targets: Targets
    facts: tuple[Fact, ...]

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
        return Profile.model_validate(content)
    except ValidationError as error:
        problems = [_describe(detail, content) for detail in error.errors()]
    # Raised outside the except clause, so the ValidationError, whose text holds the
    # refused values, is not even chained to the ProfileError.
    raise _refused(path, problems)


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
