"""Resolve shows and episodes against TMDB, with safe watch-order inference.

Three independent strategies, from most to least certain:

* :func:`resolve_show` -- pick the right show, disambiguating remakes (e.g. *Dynasty*
  1981 vs the 2017 reboot) by which candidate's episodes actually match what was watched.
* :func:`match_episode` -- match an episode by localized title or by episode number.
* :func:`order_infer` -- when titles diverge, fill a *fully watched* season by viewing
  order, but only when confident title anchors prove the ordering is correct.
"""

from __future__ import annotations

from dataclasses import dataclass

from .parsing import EPISODE_NUMBER
from .text import title_score
from .tmdb import Episode, TmdbClient

# Title-similarity score at or above which a match is treated as confident.
ACCEPT_SCORE = 0.90


@dataclass
class MatchRow:
    """A history row paired with its best episode match (used by Phase 2 reconciliation)."""

    index: int  # position in the original input, for watch-order inference
    episode: Episode | None
    score: float
    data: object = None  # opaque payload owned by the caller

    @property
    def is_confident(self) -> bool:
        return self.episode is not None and self.score >= ACCEPT_SCORE


def match_episode(
    tmdb: TmdbClient, tv_id: int, season: int | None, episode_title: str
) -> tuple[Episode | None, float, int | None]:
    """Return ``(episode, score, season)`` for the best match, searching one or all seasons."""
    seasons = [season] if season is not None else tmdb.tv_seasons(tv_id)
    number_hint = EPISODE_NUMBER.match(episode_title or "")
    wanted_number = int(number_hint.group(1)) if number_hint else None

    best: Episode | None = None
    best_score = 0.0
    best_season: int | None = None
    for season_number in seasons:
        for episode in tmdb.season_episodes(tv_id, season_number):
            # A generic "Épisode N" label matches by number within a known season.
            if wanted_number is not None and season is not None and episode.episode_number == wanted_number:
                return episode, 0.97, season_number
            score = max((title_score(episode_title, name) for name in episode.names), default=0.0)
            if score > best_score:
                best, best_score, best_season = episode, score, season_number

    # Fall back to a number match across seasons when no title matched well.
    if best_score < ACCEPT_SCORE and wanted_number is not None:
        for season_number in seasons:
            for episode in tmdb.season_episodes(tv_id, season_number):
                if episode.episode_number == wanted_number:
                    return episode, 0.80, season_number
    return best, best_score, best_season


def resolve_show(
    tmdb: TmdbClient, name: str, sample_episodes: list[tuple[int | None, str | None]]
) -> dict | None:
    """Pick the show for ``name``, disambiguating same-named remakes by their episodes."""
    candidates = [(show, score) for show, score in tmdb.search_tv_candidates(name) if score >= 0.55]
    if not candidates:
        return None
    if len(candidates) == 1 or (candidates[0][1] - candidates[1][1]) > 0.15:
        return candidates[0][0]

    # Same-named candidates (remakes): choose the one whose episodes best explain the
    # watched titles. Sample a few episodes spread across the watched seasons.
    by_season: dict[int, list[str]] = {}
    for season, title in sample_episodes:
        if season is not None and title:
            by_season.setdefault(season, []).append(title)
    samples = [(season, title) for season in sorted(by_season) for title in by_season[season][:3]][:12]
    if not samples:
        return candidates[0][0]

    best: dict | None = None
    best_key = (-1.0, -1.0)
    for show, score in candidates[:3]:
        hits = total = 0
        for season, title in samples:
            episodes = tmdb.season_episodes(show["id"], season)
            if not episodes:
                continue
            best_match = max(
                (max(title_score(title, name) for name in episode.names) for episode in episodes),
                default=0.0,
            )
            total += 1
            hits += best_match >= ACCEPT_SCORE
        rate = hits / total if total else 0.0
        if (rate, score) > best_key:
            best, best_key = show, (rate, score)
    return best


def order_infer(
    tmdb: TmdbClient, tv_id: int, season: int | None, rows: list[MatchRow]
) -> list[tuple[MatchRow, Episode]]:
    """Fill the un-matched rows of a season by watch order, only when provably safe.

    Requires the whole season to have been watched exactly once, episodes numbered
    ``1..N``, and at least two confident title anchors that each sit at their own episode
    position. Otherwise returns ``[]`` (abstains rather than guess). Netflix lists newest
    first, so a higher input index means the row was watched earlier.
    """
    if season is None:
        return []
    episodes = tmdb.season_episodes(tv_id, season)
    if not episodes:
        return []
    by_number = {episode.episode_number: episode for episode in episodes}
    count = len(episodes)
    if sorted(by_number) != list(range(1, count + 1)) or len(rows) != count:
        return []

    ordered = sorted(rows, key=lambda row: -row.index)
    anchors = [(position, row) for position, row in enumerate(ordered) if row.is_confident]
    if len(anchors) < 2:
        return []
    if any(row.episode.episode_number != position + 1 for position, row in anchors):
        return []
    return [
        (row, by_number[position + 1])
        for position, row in enumerate(ordered)
        if not row.is_confident
    ]
