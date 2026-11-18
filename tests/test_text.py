from applai.text import slugify


def test_accents_are_stripped() -> None:
    assert slugify("Ingénieur Données") == "ingenieur-donnees"


def test_runs_of_spaces_and_punctuation_become_one_hyphen_none_at_the_ends() -> None:
    assert slugify("  Lead / Dev -- Backend!  ") == "lead-dev-backend"


def test_empty_or_punctuation_only_title_gives_an_empty_slug() -> None:
    assert slugify("") == ""
    assert slugify("!!!") == ""
