"""The candidate's settings, read from settings.yaml in the data directory.

The file is optional, and so is every key in it: a missing one has its default. Settings
are read where they are used, so a change applies without restarting ApplAI.
"""

import os
from pathlib import Path
from typing import Any, cast

import yaml

from applai.profile import DATA_DIR_VARIABLE

SETTINGS_FILE = "settings.yaml"
DEFAULT_REVIEW_MIN_SCORE = 40  # the lowest Triage score the To review screen proposes


class SettingsError(Exception):
    """A settings file ApplAI cannot use. The message never quotes the file."""


def review_min_score(data_dir: Path | str | None = None) -> int:
    """The lowest score of an offer proposed to the candidate, from 0 to 100."""
    value = _read(data_dir).get("review_min_score", DEFAULT_REVIEW_MIN_SCORE)
    if type(value) is not int or not 0 <= value <= 100:
        raise SettingsError(f"{SETTINGS_FILE}: review_min_score must be a whole number, 0 to 100")
    return value


def _read(data_dir: Path | str | None) -> dict[str, Any]:
    if data_dir is None:
        data_dir = os.environ.get(DATA_DIR_VARIABLE)
        if not data_dir:
            raise SettingsError(f"{DATA_DIR_VARIABLE} is not set")
    try:
        content = yaml.safe_load((Path(data_dir) / SETTINGS_FILE).read_bytes())
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise SettingsError(f"{SETTINGS_FILE}: cannot be read ({error.strerror})") from None
    except yaml.YAMLError:
        raise SettingsError(f"{SETTINGS_FILE}: not valid YAML") from None
    if content is None:
        return {}
    if not isinstance(content, dict):
        raise SettingsError(f"{SETTINGS_FILE}: must be a mapping of settings")
    return cast(dict[str, Any], content)
