"""The fictional candidate's profile as the tests that are not about CV tailoring want it."""

from pathlib import Path

import yaml

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"


def profile_without_cv_sources() -> str:
    """The demo profile as YAML text, without its CV sources and photo rule: no CV tailoring."""
    profile = yaml.safe_load((DEMO_DIR / "profile.yaml").read_text(encoding="utf-8"))
    del profile["cv_sources"], profile["cv_photo_rule"]
    return yaml.safe_dump(profile, allow_unicode=True, sort_keys=False)


def write_profile_without_cv_sources(directory: Path) -> None:
    """Put the demo profile without CV sources in `directory`, where the profile is loaded."""
    (directory / "profile.yaml").write_text(profile_without_cv_sources(), encoding="utf-8")
