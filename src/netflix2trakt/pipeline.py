"""Orchestration: turn parsed Netflix rows into Trakt import entries + a review log.

The pipeline is pure with respect to the filesystem (its only side effects are the TMDB
and Mistral response caches, owned by the injected clients), which keeps it easy to test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .matching import ACCEPT_SCORE, MatchRow, match_episode, order_infer, resolve_show
from .mistral import MistralClient
from .parsing import ParsedTitle
from .text import normalize
from .tmdb import Episode, TmdbClient

# Minimum self-reported confidence required to accept an (optional) LLM episode pick.
LLM_MIN_CONFIDENCE = 0.70

# A trailing parenthetical such as "(version longue)" hurts movie search; strip and retry.
_PARENTHETICAL = re.compile(r"\s*[\(\[].*?[\)\]]\s*$")

#: One Trakt import row: ``(tmdb_id, media_type, watched_at)``.
ImportEntry = tuple[int, str, str]


@dataclass
class HistoryRow:
    """A single Netflix history entry with its parsed form and input position."""

    raw_title: str
    watched_at: str  # ISO-8601 timestamp or "unknown"
    parsed: ParsedTitle
    index: int


@dataclass
class ReviewEntry:
    """A row that could not be turned into an import entry (kept for transparency)."""

    raw_title: str
    watched_at: str
    kind: str
    best_guess: str
    reason: str
    score: object  # float or "" (kept loose for CSV output)


@dataclass
class InferDetail:
    """An audit record describing how an inferred episode was chosen."""

    raw_title: str
    watched_at: str
    show: str
    episode_code: str  # e.g. "S1E7"
    episode_name: str
    tmdb_id: int
    source: str  # "order" or "llm:<confidence>"


@dataclass
class PipelineResult:
    confident: list[ImportEntry] = field(default_factory=list)
    inferred: list[ImportEntry] = field(default_factory=list)
    review: list[ReviewEntry] = field(default_factory=list)
    infer_details: list[InferDetail] = field(default_factory=list)
    total_rows: int = 0  # excludes blank Netflix rows


def run(
    rows: list[HistoryRow],
    tmdb: TmdbClient,
    mistral: MistralClient | None = None,
    *,
    use_llm: bool = False,
    log: Callable[[str], None] = lambda message: None,
) -> PipelineResult:
    """Resolve every row to a TMDB id, or record why it could not be resolved."""
    result = PipelineResult()
    claimed: dict[tuple, set[str]] = {}

    def claim(tv_id: int, episode: Episode, source_title: str) -> bool:
        """``True`` to keep the play; ``False`` if a different title already took it."""
        key = (tv_id, episode.season_number, episode.episode_number)
        token = normalize(source_title)
        seen = claimed.get(key)
        if seen is None:
            claimed[key] = {token}
            return True
        if token in seen:  # same label again = a legit extra play (rewatch / other account)
            return True
        return False

    # Resolve each unique show once (disambiguating remakes).
    show_rows: dict[str | None, list[HistoryRow]] = {}
    for row in rows:
        if row.parsed.kind in ("episode", "ambiguous"):
            show_rows.setdefault(row.parsed.show, []).append(row)
    log(f"Resolving {len(show_rows)} unique shows...")
    show_to_tv: dict[str | None, dict | None] = {}
    for name, group in show_rows.items():
        if not name:
            show_to_tv[name] = None
            continue
        samples = [(member.parsed.season, member.parsed.episode_title) for member in group]
        show_to_tv[name] = resolve_show(tmdb, name, samples)
    tmdb.save()

    # Phase 1: per-row resolution. Group episode rows for per-season reconciliation.
    log("Matching...")
    groups: dict[tuple, list[MatchRow]] = {}
    for row in rows:
        parsed = row.parsed
        if parsed.kind == "blank":
            result.review.append(
                ReviewEntry(row.raw_title, row.watched_at, "blank", "", "blank Netflix row", "")
            )
            continue
        if parsed.kind == "movie":
            movie, score = tmdb.search_movie(parsed.title)
            if not (movie and score >= ACCEPT_SCORE):
                stripped = _PARENTHETICAL.sub("", parsed.title).strip()
                if stripped != parsed.title:
                    movie, score = tmdb.search_movie(stripped)
            if movie and score >= ACCEPT_SCORE:
                result.confident.append((movie["id"], "movie", row.watched_at))
            else:
                result.review.append(ReviewEntry(row.raw_title, row.watched_at, "movie",
                                                  movie.get("title") if movie else "", "movie not found",
                                                  round(score, 2)))
            continue

        tv = show_to_tv.get(parsed.show)
        episode: Episode | None = None
        score = 0.0
        season_found: int | None = None
        if tv:
            episode, score, season_found = match_episode(
                tmdb, tv["id"], parsed.season, parsed.episode_title or ""
            )
        # A colon-titled movie whose prefix looks like a series (e.g. "Terminator: Dark Fate"):
        # if there is no strong episode match, try the full title as a movie.
        if parsed.kind == "ambiguous" and not (episode and score >= ACCEPT_SCORE):
            movie, movie_score = tmdb.search_movie(parsed.full)
            if movie and movie_score >= ACCEPT_SCORE:
                result.confident.append((movie["id"], "movie", row.watched_at))
                continue
        if not tv:
            result.review.append(
                ReviewEntry(row.raw_title, row.watched_at, parsed.kind, "", "show not found", "")
            )
            continue
        season_key = parsed.season if parsed.season is not None else season_found
        groups.setdefault((tv["id"], season_key, tv["name"]), []).append(
            MatchRow(index=row.index, episode=episode, score=score, data=row)
        )
    tmdb.save()

    # Phase 2: per (show, season) -> emit confident anchors, then safe order-inference.
    leftovers: list[tuple[MatchRow, int, str, int | None]] = []
    for (tv_id, season, tv_name), match_rows in groups.items():
        for match in match_rows:
            if match.is_confident:
                source = match.data.parsed.episode_title or ""
                if claim(tv_id, match.episode, source):
                    result.confident.append((match.episode.id, "episode", match.data.watched_at))
                else:
                    result.review.append(ReviewEntry(match.data.raw_title, match.data.watched_at,
                                                      match.data.parsed.kind, tv_name,
                                                      "collision (different title, same episode)",
                                                      round(match.score, 2)))
        fills = order_infer(tmdb, tv_id, season, match_rows)
        filled = set()
        for match, episode in fills:
            filled.add(match.index)
            source = match.data.parsed.episode_title or ""
            if claim(tv_id, episode, source):
                result.inferred.append((episode.id, "episode", match.data.watched_at))
                result.infer_details.append(InferDetail(
                    match.data.raw_title, match.data.watched_at, tv_name,
                    f"S{episode.season_number}E{episode.episode_number}",
                    episode.names[0] if episode.names else "", episode.id, "order"))
            else:
                result.review.append(ReviewEntry(match.data.raw_title, match.data.watched_at,
                                                  match.data.parsed.kind, tv_name, "collision (order)", ""))
        for match in match_rows:
            if not match.is_confident and match.index not in filled:
                leftovers.append((match, tv_id, tv_name, season))

    # Optional LLM fallback on whatever order-inference could not place.
    if use_llm and mistral and mistral.enabled:
        log(f"LLM fallback: {len(leftovers)} rows via {mistral.model}...")
        for match, tv_id, tv_name, season in leftovers:
            row, parsed = match.data, match.data.parsed
            seasons = [season] if season is not None else tmdb.tv_seasons(tv_id)
            candidates = [episode for sn in seasons for episode in tmdb.season_episodes(tv_id, sn)]
            if not candidates:
                result.review.append(ReviewEntry(row.raw_title, row.watched_at, parsed.kind, tv_name,
                                                  "no TMDB episodes", ""))
                continue
            key = f"{tv_id}|{season}|{normalize(parsed.episode_title or '')}"
            answer = mistral.pick_episode(key, tv_name, parsed.episode_title or "", season, candidates)
            chosen = next((episode for episode in candidates
                           if episode.season_number == answer.get("season")
                           and episode.episode_number == answer.get("episode")), None)
            confidence = answer.get("confidence", 0)
            accepted = (
                chosen is not None
                and confidence >= LLM_MIN_CONFIDENCE
                and claim(tv_id, chosen, parsed.episode_title or "")
            )
            if accepted:
                result.inferred.append((chosen.id, "episode", row.watched_at))
                result.infer_details.append(InferDetail(
                    row.raw_title, row.watched_at, tv_name,
                    f"S{chosen.season_number}E{chosen.episode_number}",
                    chosen.names[0] if chosen.names else "", chosen.id, f"llm:{confidence}"))
            else:
                result.review.append(ReviewEntry(row.raw_title, row.watched_at, parsed.kind, tv_name,
                                                  "LLM: no reliable match", confidence))
        mistral.save()
    else:
        for match, _tv_id, tv_name, _season in leftovers:
            row = match.data
            result.review.append(ReviewEntry(row.raw_title, row.watched_at, row.parsed.kind, tv_name,
                                              "divergent title (unresolved)", ""))

    result.total_rows = sum(1 for row in rows if row.parsed.kind != "blank")
    return result
