"""The Île-de-France communes ship with the package, for the screening to find a commune by name."""

import csv
from importlib.resources import files


def _communes() -> list[dict[str, str]]:
    text = files("applai").joinpath("data/idf_communes.csv").read_text(encoding="utf-8")
    return list(csv.DictReader(text.splitlines()))


def test_every_commune_of_the_eight_departments_is_bundled():
    rows = _communes()
    assert len(rows) == 1266
    assert {r["department"] for r in rows} == {"75", "77", "78", "91", "92", "93", "94", "95"}


def test_a_commune_is_found_by_name_with_its_postcodes():
    by_name = {r["commune"]: r for r in _communes()}
    assert by_name["Levallois-Perret"]["department"] == "92"
    assert by_name["Levallois-Perret"]["postcodes"] == "92300"
    assert len(by_name["Paris"]["postcodes"].split()) == 21  # 75001-75020 and 75116
