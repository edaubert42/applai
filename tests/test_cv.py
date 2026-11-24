import io
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from pypdf import PdfReader

from applai.cli import main
from applai.cv import CvError, render_cv

DEMO_DIR = Path(__file__).parents[1] / "demo" / "candidate"
NO_BREAK_SPACE = chr(0xA0)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the demo candidate's data directory, named by APPLAI_DATA_DIR."""
    directory = tmp_path / "data"
    shutil.copytree(DEMO_DIR, directory)
    monkeypatch.setenv("APPLAI_DATA_DIR", str(directory))
    return directory


def _edit(data_dir: Path, change: Callable[[dict[str, Any]], None], lang: str = "fr") -> None:
    """Rewrite the CV source of lang after change(source)."""
    path = data_dir / "cv" / f"cv_{lang}.yaml"
    source = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(source)
    path.write_text(yaml.safe_dump(source, allow_unicode=True), encoding="utf-8")


def _text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


def _images(pdf: bytes) -> int:
    return len(re.findall(rb"/Subtype\s*/Image\b", pdf))


def _build(out: Path, *argv: str) -> None:
    main(["cv", "build", "--lang", "fr", "--contract", "permanent", *argv, "--out", str(out)])


def test_the_demo_french_cv_is_one_valid_page_with_the_headline_and_french_typography(
    data_dir: Path,
) -> None:
    rendered = render_cv(data_dir, "fr", "permanent")

    assert rendered.pdf.startswith(b"%PDF-")
    assert rendered.checks.pages == 1
    assert rendered.checks.problems == []
    assert rendered.checks.free_mm > 0
    text = _text(rendered.pdf)
    assert "Jeanne Dupont" in text
    assert "Ingénieure data, pipelines de données fiables" in text
    assert f"{NO_BREAK_SPACE}:" in text


def test_a_source_with_an_em_dash_is_reported_and_the_command_fails(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _edit(data_dir, lambda source: source["skills"][0].update(text="Python — SQL"))

    checks = render_cv(data_dir, "fr", "permanent").checks
    out = tmp_path / "cv.pdf"
    with pytest.raises(SystemExit) as exit:
        _build(out)

    assert checks.dashes == 1
    assert any("U+2014" in problem for problem in checks.problems)
    assert exit.value.code == 1
    assert "FAIL" in capsys.readouterr().out


def test_a_source_made_too_long_is_two_pages_and_the_command_fails(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Many small entries, so the length does not hang on where one long entry breaks.
    _edit(data_dir, lambda source: source.update(projects=source["projects"] * 10))

    checks = render_cv(data_dir, "fr", "permanent").checks
    with pytest.raises(SystemExit) as exit:
        _build(tmp_path / "cv.pdf")

    assert checks.pages == 2
    assert any("2 pages" in problem for problem in checks.problems)
    assert exit.value.code == 1
    assert "FAIL" in capsys.readouterr().out


def test_the_command_prints_pages_and_free_space_then_ok(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out" / "cv.pdf"

    _build(out)

    printed = capsys.readouterr().out
    assert re.search(r"pages: 1\b", printed)
    assert re.search(r"free space: \d+(\.\d+)? mm", printed)
    assert printed.strip().endswith("OK")
    assert out.read_bytes().startswith(b"%PDF-")


def test_with_a_photo_the_pdf_holds_one_image(data_dir: Path) -> None:
    without = render_cv(data_dir, "fr", "permanent")
    with_photo = render_cv(data_dir, "fr", "permanent", photo=True)

    assert _images(without.pdf) == 0
    assert _images(with_photo.pdf) == 1
    assert with_photo.checks.pages == 1


def test_a_photo_without_photo_jpg_is_refused_naming_the_file(
    data_dir: Path, tmp_path: Path
) -> None:
    (data_dir / "cv" / "photo.jpg").unlink()
    out = tmp_path / "cv.pdf"

    with pytest.raises(CvError, match=r"photo\.jpg"):
        render_cv(data_dir, "fr", "permanent", photo=True)
    with pytest.raises(SystemExit, match=r"photo\.jpg"):
        _build(out, "--photo")

    assert not out.exists()


@pytest.mark.parametrize(
    ("where", "key"),
    [
        (("name",), "name"),
        (("contact", "phone"), "contact.phone"),
        (("summary",), "summary"),
        (("titles", "skills"), "titles.skills"),
        (("experience", 0, "dates"), "experience[0].dates"),
        (("education", 0, "school"), "education[0].school"),
    ],
)
def test_a_source_missing_a_key_is_refused_with_its_name_and_no_pdf(
    data_dir: Path, tmp_path: Path, where: tuple[str | int, ...], key: str
) -> None:
    def remove(source: dict[str, Any]) -> None:
        node: Any = source
        for part in where[:-1]:
            node = node[part]
        del node[where[-1]]

    _edit(data_dir, remove)
    out = tmp_path / "cv.pdf"

    with pytest.raises(CvError, match=re.escape(key)):
        render_cv(data_dir, "fr", "permanent")
    with pytest.raises(SystemExit, match=re.escape(key)):
        _build(out)

    assert not out.exists()


def test_a_contract_with_no_headline_is_refused_with_the_key_and_no_pdf(
    data_dir: Path, tmp_path: Path
) -> None:
    out = tmp_path / "cv.pdf"

    with pytest.raises(CvError, match=r"headlines\.apprenticeship"):
        render_cv(data_dir, "fr", "apprenticeship")
    with pytest.raises(SystemExit, match=r"headlines\.apprenticeship"):
        main(["cv", "build", "--lang", "fr", "--contract", "apprenticeship", "--out", str(out)])

    assert not out.exists()


def test_the_summary_of_a_contract_replaces_the_default_one(data_dir: Path) -> None:
    text = _text(render_cv(data_dir, "fr", "freelance").pdf).replace(NO_BREAK_SPACE, " ")

    assert "Disponible sous un mois" in text
    assert "Je cherche un poste" not in text


def test_the_english_demo_cv_is_one_page_too(data_dir: Path) -> None:
    rendered = render_cv(data_dir, "en", "permanent")

    assert rendered.checks.pages == 1
    assert rendered.checks.problems == []
    assert "Data engineer, reliable data pipelines" in _text(rendered.pdf)
