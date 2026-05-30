from netflix2trakt.matching import MatchRow, match_episode, order_infer, resolve_show
from netflix2trakt.tmdb import Episode


def ep(season: int, number: int, *names: str) -> Episode:
    return Episode(id=season * 100 + number, season_number=season, episode_number=number, names=list(names))


SEASON_1 = [
    ep(1, 1, "Pilote", "Pilot"),
    ep(1, 2, "Lâche-moi !", "Let Go of Me"),
    ep(1, 3, "Doubt Thou The Stars"),
    ep(1, 4, "Sweet Dreams"),
]


class FakeTmdb:
    """In-memory stand-in for TmdbClient (no network), enough for the matching logic."""

    def __init__(self, seasons=None, candidates=None):
        self._seasons = seasons or {}      # {(tv_id, season): [Episode, ...]}
        self._candidates = candidates or {}  # {name: [(show_dict, score), ...]}

    def season_episodes(self, tv_id, season):
        return self._seasons.get((tv_id, season), [])

    def tv_seasons(self, tv_id):
        return sorted({season for (tid, season) in self._seasons if tid == tv_id})

    def search_tv_candidates(self, name, limit=6):
        return self._candidates.get(name, [])


def test_match_episode_by_title():
    tmdb = FakeTmdb({(10, 1): SEASON_1})
    episode, score, season = match_episode(tmdb, 10, 1, "Sweet Dreams")
    assert episode.episode_number == 4
    assert score == 1.0
    assert season == 1


def test_match_episode_by_number_label():
    tmdb = FakeTmdb({(10, 1): SEASON_1})
    episode, score, _ = match_episode(tmdb, 10, 1, "Épisode 2")
    assert episode.episode_number == 2
    assert score >= 0.9


def _rows_in_watch_order(*pairs):
    """Build MatchRows. Netflix lists newest-first, so we assign descending indices to
    successive *watch* positions (the first watched gets the highest index)."""
    n = len(pairs)
    return [MatchRow(index=n - 1 - i, episode=epi, score=sc) for i, (epi, sc) in enumerate(pairs)]


def test_order_infer_fills_divergent_titles():
    tmdb = FakeTmdb({(10, 1): SEASON_1})
    # Watched E1..E4 in order; E1 and E4 matched by title (anchors), E2/E3 diverged.
    rows = _rows_in_watch_order(
        (SEASON_1[0], 1.0),  # E1 anchor
        (None, 0.0),         # -> E2
        (None, 0.0),         # -> E3
        (SEASON_1[3], 1.0),  # E4 anchor
    )
    fills = order_infer(tmdb, 10, 1, rows)
    assert sorted(episode.episode_number for _, episode in fills) == [2, 3]


def test_order_infer_abstains_on_partial_season():
    tmdb = FakeTmdb({(10, 1): SEASON_1})  # 4 episodes, but only 2 rows watched
    rows = _rows_in_watch_order((SEASON_1[0], 1.0), (None, 0.0))
    assert order_infer(tmdb, 10, 1, rows) == []


def test_order_infer_abstains_with_too_few_anchors():
    tmdb = FakeTmdb({(10, 1): SEASON_1})
    rows = _rows_in_watch_order((SEASON_1[0], 1.0), (None, 0.0), (None, 0.0), (None, 0.0))
    assert order_infer(tmdb, 10, 1, rows) == []


def test_order_infer_abstains_when_anchors_are_out_of_order():
    tmdb = FakeTmdb({(10, 1): SEASON_1})
    # First watched is E4, last watched is E1 -> anchors contradict episode order.
    rows = _rows_in_watch_order(
        (SEASON_1[3], 1.0),  # E4 in position 1
        (None, 0.0),
        (None, 0.0),
        (SEASON_1[0], 1.0),  # E1 in position 4
    )
    assert order_infer(tmdb, 10, 1, rows) == []


def test_resolve_show_disambiguates_remake_by_episodes():
    # Two same-named candidates; only id 200 has the episodes that were watched.
    candidates = {"Dynastie": [({"id": 100, "name": "Dynastie"}, 1.0),
                               ({"id": 200, "name": "Dynastie"}, 1.0)]}
    seasons = {
        (100, 1): [ep(1, 1, "The Original Pilot"), ep(1, 2, "Old Episode")],
        (200, 1): [ep(1, 1, "Chacune ses affaires"), ep(1, 2, "Une vraie actrice")],
    }
    tmdb = FakeTmdb(seasons=seasons, candidates=candidates)
    samples = [(1, "Chacune ses affaires"), (1, "Une vraie actrice")]
    show = resolve_show(tmdb, "Dynastie", samples)
    assert show["id"] == 200
