"""Every call in frontend/src/api.ts names a method and a path the real app serves.

The frontend's tests run on fake-api.ts, so a route api.ts calls and the backend lacks
passes them and answers 404 in the app.
"""

import re
from pathlib import Path

from applai.app import create_app

API_TS = Path(__file__).parent.parent / "frontend" / "src" / "api.ts"
CALL = re.compile(
    r"""\b(?:request|requestEmpty)\s*(?:<[^>]*>)?\(\s*"([A-Z]+)"\s*,\s*([`"])(.*?)\2"""
)


def _shape(path: str) -> str:
    """A path with every parameter, `{id}` or `${id}`, written `{}` and no query string."""
    return re.sub(r"\$?\{[^}]*\}", "{}", path.split("?")[0])


def api_calls(source: str) -> set[tuple[str, str]]:
    """The (method, path) of each call in api.ts, parameters written {}."""
    return {(method, _shape(path)) for method, _, path in CALL.findall(source)}


def missing_routes(source: str, tmp_path: Path) -> list[str]:
    """The calls in source that the real app does not serve, as `METHOD /path`."""
    app = create_app(tmp_path, frontend_dir=tmp_path / "no-frontend")
    # The schema lists every route, whatever router it came from.
    served = {
        (method.upper(), _shape(path))
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }
    return sorted(f"{method} {path}" for method, path in api_calls(source) - served)


def test_a_call_to_a_route_the_app_does_not_serve_fails_and_names_it(tmp_path: Path):
    source = API_TS.read_text() + (
        '\nexport const broken = () => request<unknown>("POST", `/api/offers/${id}/archive`);\n'
    )

    assert missing_routes(source, tmp_path) == ["POST /api/offers/{}/archive"]


def test_a_wrong_method_on_a_served_path_fails(tmp_path: Path):
    source = 'request<unknown>("DELETE", "/api/scan")'

    assert missing_routes(source, tmp_path) == ["DELETE /api/scan"]


def test_every_route_api_ts_calls_is_served_by_the_app(tmp_path: Path):
    calls = api_calls(API_TS.read_text())

    assert len(calls) >= 20, "the calls of api.ts were not read"
    missing = missing_routes(API_TS.read_text(), tmp_path)
    assert not missing, "api.ts calls routes the app does not serve: " + ", ".join(missing)
