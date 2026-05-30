"""Minimal, cached TMDB API client (standard library only).

Every request is memoized to a JSON file on disk, so re-runs are fast and deterministic
and never hit the network twice for the same resource.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .text import title_score

# TMDB REST endpoint and the languages episode titles are matched against. Netflix and
# TMDB often ship different French translations, so we also compare against the original.
TMDB_API_ROOT = "https://api.themoviedb.org/3"
LANGUAGES = ("fr-FR", "en-US")


@dataclass
class Episode:
    """A single TMDB episode, with names merged across the configured languages."""

    id: int
    season_number: int
    episode_number: int
    names: list[str] = field(default_factory=list)
    air_date: str | None = None


class TmdbAuthError(RuntimeError):
    """Raised when neither a TMDB v4 token nor a v3 key is configured."""


class TmdbClient:
    """Thin wrapper over the TMDB REST API with on-disk response caching."""

    def __init__(self, cache_path: str, *, bearer: str | None = None, api_key: str | None = None):
        self.cache_path = cache_path
        self.cache: dict[str, object] = {}
        if os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as handle:
                    self.cache = json.load(handle)
            except (OSError, ValueError):
                self.cache = {}

        self.bearer = bearer
        self.api_key = api_key
        # A v4 "Read Access Token" is a long JWT; accept one passed via the v3 slot too.
        if not self.bearer and api_key and "." in api_key and len(api_key) > 40:
            self.bearer, self.api_key = api_key, None
        if not (self.bearer or self.api_key):
            raise TmdbAuthError("Set TMDB_BEARER (v4 token) or TMDB_API_KEY (v3 key).")

        self.calls = 0

    # -- low level ---------------------------------------------------------------
    def save(self) -> None:
        with open(self.cache_path, "w", encoding="utf-8") as handle:
            json.dump(self.cache, handle)

    def _get(self, path: str, **params: str) -> dict | None:
        if self.api_key:
            params["api_key"] = self.api_key
        url = f"{TMDB_API_ROOT}{path}?{urllib.parse.urlencode(params)}"
        if url in self.cache:
            return self.cache[url]  # type: ignore[return-value]

        headers = {"Accept": "application/json"}
        if self.bearer:
            headers["Authorization"] = f"Bearer {self.bearer}"

        for attempt in range(5):
            try:
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = json.loads(response.read().decode("utf-8"))
                self.calls += 1
                self.cache[url] = data
                if self.calls % 50 == 0:
                    self.save()
                time.sleep(0.03)
                return data
            except urllib.error.HTTPError as error:
                if error.code == 429:  # rate limited
                    time.sleep(1 + attempt)
                    continue
                if error.code == 404:
                    self.cache[url] = None
                    return None
                if attempt == 4:
                    raise
                time.sleep(1 + attempt)
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(1 + attempt)
        return None

    # -- typed helpers -----------------------------------------------------------
    def search_movie(self, title: str) -> tuple[dict | None, float]:
        """Return the best movie result for ``title`` and its title-similarity score."""
        data = self._get("/search/movie", query=title, language="fr-FR", include_adult="false") or {}
        best, best_score = None, 0.0
        for result in (data.get("results") or [])[:8]:
            score = max(
                title_score(title, result.get("title", "")),
                title_score(title, result.get("original_title", "")),
            )
            if score > best_score:
                best, best_score = result, score
        return best, best_score

    def search_tv_candidates(self, name: str, limit: int = 6) -> list[tuple[dict, float]]:
        """Return up to ``limit`` show candidates, sorted by title similarity (desc)."""
        data = self._get("/search/tv", query=name, language="fr-FR", include_adult="false") or {}
        candidates = []
        for result in (data.get("results") or [])[:limit]:
            score = max(
                title_score(name, result.get("name", "")),
                title_score(name, result.get("original_name", "")),
            )
            candidates.append((result, score))
        candidates.sort(key=lambda item: -item[1])
        return candidates

    def tv_seasons(self, tv_id: int) -> list[int]:
        """Return the season numbers available for a show."""
        data = self._get(f"/tv/{tv_id}", language="fr-FR") or {}
        return [
            season["season_number"]
            for season in (data.get("seasons") or [])
            if season.get("season_number") is not None
        ]

    def season_episodes(self, tv_id: int, season_number: int) -> list[Episode]:
        """Return a season's episodes, with names merged across :data:`LANGUAGES`."""
        merged: dict[int, Episode] = {}
        for language in LANGUAGES:
            data = self._get(f"/tv/{tv_id}/season/{season_number}", language=language) or {}
            for raw in data.get("episodes") or []:
                episode_id = raw.get("id")
                if episode_id is None:
                    continue
                episode = merged.get(episode_id)
                if episode is None:
                    episode = Episode(
                        id=episode_id,
                        season_number=raw.get("season_number", season_number),
                        episode_number=raw.get("episode_number"),
                        air_date=raw.get("air_date"),
                    )
                    merged[episode_id] = episode
                if raw.get("name"):
                    episode.names.append(raw["name"])
        return sorted(merged.values(), key=lambda episode: episode.episode_number or 0)
