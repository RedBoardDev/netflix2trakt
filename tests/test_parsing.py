from netflix2trakt.parsing import parse_title, parse_watched_at


def test_movie():
    parsed = parse_title("The Electric State")
    assert parsed.kind == "movie"
    assert parsed.title == "The Electric State"


def test_french_typography_is_not_a_separator():
    # " : " (space-colon-space) belongs to the title; this is a movie, not a show.
    assert parse_title("Astérix & Obélix : L'empire du Milieu").kind == "movie"


def test_episode_with_season():
    parsed = parse_title("Lucifer: Saison 6: Partenaires à jamais")
    assert parsed.kind == "episode"
    assert parsed.show == "Lucifer"
    assert parsed.season == 6
    assert parsed.episode_title == "Partenaires à jamais"


def test_ambiguous_when_show_name_contains_colon():
    parsed = parse_title("Ghosts : Fantômes à la maison: Pilote")
    assert parsed.kind == "ambiguous"
    assert parsed.show == "Ghosts : Fantômes à la maison"
    assert parsed.episode_title == "Pilote"


def test_mini_series_is_season_one():
    parsed = parse_title("Adolescence: Mini-série: Épisode 4")
    assert parsed.kind == "episode"
    assert parsed.season == 1


def test_non_breaking_space_in_season_label():
    parsed = parse_title("Designated Survivor: Saison\xa02: Les retombées")
    assert parsed.kind == "episode"
    assert parsed.season == 2


def test_blank_rows():
    assert parse_title("").kind == "blank"
    assert parse_title("   ").kind == "blank"


def test_parse_watched_at():
    assert parse_watched_at("6/2/25") == "2025-06-02T12:00:00Z"
    assert parse_watched_at("12/31/21") == "2021-12-31T12:00:00Z"
    assert parse_watched_at("not-a-date") is None
