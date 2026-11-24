from pathlib import Path

import pii
import pytest
from pii import find_pii, forbidden_file, main

# Real-looking values are split so that this file itself passes the check.
REAL_EMAIL = "someone" + "@gmail.com"
REAL_PHONE = "06 12 34" + " 56 78"
REAL_PHONE_INTL = "+33 7 12" + " 34 56 78"
REAL_PHONE_COMPACT = "06123" + "45678"


@pytest.mark.parametrize("value", [REAL_EMAIL, REAL_PHONE, REAL_PHONE_INTL, REAL_PHONE_COMPACT])
def test_real_looking_contact_details_are_found(value: str):
    hits = find_pii(f"line one\ncontact: {value}\n")
    assert [h.line for h in hits] == [2]


@pytest.mark.parametrize(
    "value",
    [
        "jeanne@example.org",
        "bob@example.com",
        "ops@acme.test",
        "noreply@anthropic.com",
        "+33 6 00 00 00 00",
        "01 00 00 00 00",
    ],
)
def test_fictional_contact_details_are_allowed(value: str):
    assert find_pii(f"contact: {value}") == []


def test_digits_inside_a_hash_are_not_a_phone_number():
    wheel = "packages/c3/89/" + "03022" + "15373" + "c2f8b4408b0f/rpds_py.whl"
    assert find_pii(wheel) == []


def test_hit_names_the_kind_never_the_value():
    hits = find_pii(f"mail {REAL_EMAIL}")
    assert hits[0].kind == "e-mail address"
    assert "someone" not in str(hits[0])


@pytest.mark.parametrize("name", ["cv.pdf", "photo.JPG", "letter.docx", "applai.sqlite"])
def test_documents_and_images_are_forbidden(tmp_path: Path, name: str):
    path = tmp_path / name
    path.write_bytes(b"anything")
    assert forbidden_file(path, allowed=set())


def test_undecodable_file_is_forbidden(tmp_path: Path):
    path = tmp_path / "blob.bin"
    path.write_bytes(b"\xff\xfe\x00binary")
    assert forbidden_file(path, allowed=set())


def test_allowed_list_and_text_files_pass(tmp_path: Path):
    image = tmp_path / "demo.png"
    image.write_bytes(b"\x89PNG")
    text = tmp_path / "notes.md"
    text.write_text("plain text")
    assert not forbidden_file(image, allowed={str(image)})
    assert not forbidden_file(text, allowed=set())


def test_main_passes_an_allowed_binary_file_without_reading_it_as_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    image = tmp_path / "demo.jpg"
    image.write_bytes(b"\xff\xd8\xff")
    allowed = tmp_path / "allowed_files.txt"
    allowed.write_text(f"{image}\n")
    monkeypatch.setattr(pii, "ALLOWED_FILES_LIST", allowed)

    assert main([str(image)]) == 0


def test_main_reports_without_values(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    leaked = tmp_path / "notes.md"
    leaked.write_text(f"Recruiter: {REAL_EMAIL}\n")
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF")
    clean = tmp_path / "clean.md"
    clean.write_text("Candidate: jeanne@example.org\n")

    assert main([str(clean)]) == 0
    assert main([str(leaked), str(cv)]) == 1
    out = capsys.readouterr().out
    assert f"{leaked}:1: e-mail address" in out
    assert f"{cv}: binary or document file" in out
    assert "someone" not in out


@pytest.mark.parametrize(
    "sender",
    [
        "jobalerts-noreply" + "@linkedin.com",
        "alerts" + "@welcometothejungle.com",
        "alerte" + "@emails.hellowork.com",
        "no-reply" + "@notification.prod.jobteaser.net",
        "emploi" + "@alertes.lesjeudis.com",
    ],
)
def test_a_job_alert_sender_is_allowed_for_the_mail_fixtures(sender: str):
    assert find_pii(f"From: Alerts <{sender}>") == []


def test_another_address_at_a_job_site_is_still_found():
    assert [h.kind for h in find_pii("someone" + "@linkedin.com")] == ["e-mail address"]


def test_pii_script_starts_with_a_comment_naming_the_ci_job_that_runs_it():
    root = Path(__file__).parent.parent
    first_line = (root / "tools" / "pii.py").read_text().splitlines()[0]
    assert first_line.startswith("#")
    job = first_line.split("`")[1]
    ci_text = (root / ".github" / "workflows" / "ci.yml").read_text()
    assert f"\n  {job}:" in ci_text
    assert "python3 tools/pii.py" in ci_text.split(f"\n  {job}:")[1]
