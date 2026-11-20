from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from applai.app import create_app
from applai.database import open_database
from applai.models import Offer


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """The real app on a database in tmp_path, started as applai serve starts it."""
    with TestClient(create_app(tmp_path, frontend_dir=tmp_path / "no-frontend")) as client:
        yield client


def _head() -> str | None:
    config = Config()
    config.set_main_option("script_location", "applai:migrations")
    return ScriptDirectory.from_config(config).get_current_head()


def _offer(slug: str, created_at: datetime, user_id: int = 1, state: str = "new") -> Offer:
    return Offer(
        user_id=user_id,
        url=f"https://jobs.example.org/brindille-logistique/{slug}",
        title=slug.replace("-", " ").capitalize(),
        company="Brindille Logistique",
        state=state,
        created_at=created_at,
    )


def _summary(offer: Offer) -> dict[str, object]:
    return {
        "id": offer.id,
        "url": offer.url,
        "title": offer.title,
        "company": offer.company,
        "state": offer.state,
    }


def test_health_is_ok_and_gives_the_database_revision(client: TestClient):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database_revision": _head()}


def test_the_offers_in_a_state_are_listed_newest_first(tmp_path: Path, client: TestClient):
    older = _offer("data-engineer", datetime(2026, 11, 2, 8, 30))
    newer = _offer("analytics-engineer", datetime(2026, 11, 3, 8, 30))
    triaged = _offer("platform-engineer", datetime(2026, 11, 4, 8, 30), state="triaged")
    other_user = _offer("data-engineer", datetime(2026, 11, 5, 8, 30), user_id=2)
    engine = open_database(tmp_path)
    with Session(engine) as session:
        session.add_all([older, newer, triaged, other_user])
        session.commit()
        expected = [_summary(newer), _summary(older)]
    engine.dispose()

    response = client.get("/api/offers", params={"state": "new"})

    assert response.status_code == 200
    assert response.json() == expected


def test_an_unknown_offer_state_is_refused(client: TestClient):
    # "sent" is a state of an application, not of an offer.
    response = client.get("/api/offers", params={"state": "sent"})

    assert response.status_code == 422


def test_the_built_frontend_is_served_only_when_it_exists(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>ApplAI</title>")

    with TestClient(create_app(tmp_path, frontend_dir=dist)) as client:
        assert "<title>ApplAI</title>" in client.get("/").text
        assert client.get("/api/health").status_code == 200
    with TestClient(create_app(tmp_path, frontend_dir=tmp_path / "missing")) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/health").status_code == 200


def test_a_logo_of_the_data_directory_is_served_with_its_image_type(
    tmp_path: Path, client: TestClient
):
    (tmp_path / "logos").mkdir()
    (tmp_path / "logos" / "pivoine-analytics.png").write_bytes(b"\x89PNG\r\n\x1a\nfictional")
    (tmp_path / "logos" / "brindille-logistique.ico").write_bytes(b"\x00\x00\x01\x00fictional")

    png = client.get("/api/logos/pivoine-analytics.png")
    ico = client.get("/api/logos/brindille-logistique.ico")

    assert (png.status_code, png.headers["content-type"]) == (200, "image/png")
    assert png.content == b"\x89PNG\r\n\x1a\nfictional"
    assert (ico.status_code, ico.headers["content-type"]) == (200, "image/x-icon")


@pytest.mark.parametrize(
    "name",
    [
        "..",
        ".",
        "..%2Fsecret.png",
        "%2E%2E%2Fsecret.png",
        "sub%2Fnested.png",
        "unknown.png",
        "a%00.png",
    ],
)
def test_a_logo_name_that_is_not_a_file_of_logos_is_a_404(
    tmp_path: Path, client: TestClient, name: str
):
    (tmp_path / "logos" / "sub").mkdir(parents=True)
    (tmp_path / "logos" / "sub" / "nested.png").write_bytes(b"\x89PNG")
    (tmp_path / "secret.png").write_bytes(b"\x89PNG")

    assert client.get(f"/api/logos/{name}").status_code == 404


def test_a_logo_that_is_a_link_out_of_logos_or_not_an_image_is_a_404(
    tmp_path: Path, client: TestClient
):
    (tmp_path / "logos").mkdir()
    (tmp_path / "secret.png").write_bytes(b"\x89PNG")
    (tmp_path / "logos" / "link.png").symlink_to(tmp_path / "secret.png")
    (tmp_path / "logos" / "script.svg").write_text("<svg onload='alert(1)'/>")

    assert client.get("/api/logos/link.png").status_code == 404
    assert client.get("/api/logos/script.svg").status_code == 404
