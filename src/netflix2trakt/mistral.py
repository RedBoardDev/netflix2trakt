"""Optional Mistral client used only as a fallback for episodes order-inference cannot place.

The model is constrained to choose an episode *from a supplied candidate list* (or answer
"none"); the calling code validates the choice. The LLM only contributes cross-language
semantic judgement -- it never invents identifiers.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from .tmdb import Episode

MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
DEFAULT_MISTRAL_MODEL = "mistral-small-latest"

_SYSTEM_PROMPT = (
    "Tu identifies l'épisode d'une série correspondant à un titre Netflix français. "
    "Les traductions FR de Netflix et TMDB diffèrent souvent : raisonne par le SENS, "
    "pas par les mots. Tu réponds STRICTEMENT en JSON."
)


class MistralClient:
    """Cached client over the Mistral chat-completions endpoint."""

    def __init__(self, cache_path: str, *, api_key: str | None, model: str | None = None,
                 url: str | None = None):
        self.cache_path = cache_path
        self.cache: dict[str, dict] = {}
        if os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as handle:
                    self.cache = json.load(handle)
            except (OSError, ValueError):
                self.cache = {}
        self.api_key = api_key
        self.model = model or os.environ.get("MISTRAL_MODEL", DEFAULT_MISTRAL_MODEL)
        self.url = url or os.environ.get("MISTRAL_URL", MISTRAL_API_URL)
        self.enabled = bool(api_key)
        self.calls = 0

    def save(self) -> None:
        with open(self.cache_path, "w", encoding="utf-8") as handle:
            json.dump(self.cache, handle)

    def pick_episode(
        self,
        cache_key: str,
        show_name: str,
        netflix_title: str,
        season_hint: int | None,
        candidates: list[Episode],
    ) -> dict:
        """Ask the model which candidate matches; return ``{season, episode, confidence}``."""
        if cache_key in self.cache:
            return self.cache[cache_key]

        listing = [
            {
                "s": episode.season_number,
                "e": episode.episode_number,
                "fr": episode.names[0] if episode.names else None,
                "en": episode.names[1] if len(episode.names) > 1 else None,
                "date": episode.air_date,
            }
            for episode in candidates
        ]
        user_prompt = (
            f"Série : {show_name}\n"
            f'Titre d\'épisode Netflix (FR) : "{netflix_title}"\n'
            + (f"Saison probable : {season_hint}\n" if season_hint is not None else "")
            + "Épisodes TMDB candidats (choisis EXACTEMENT l'un d'eux) :\n"
            + json.dumps(listing, ensure_ascii=False)
            + '\n\nRéponds en JSON : {"season": <int|null>, "episode": <int|null>, '
            '"confidence": <0..1>}. N\'invente rien : choisis un couple (season,episode) '
            "présent dans la liste, ou episode=null si aucun ne correspond."
        )
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "max_tokens": 120,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            }
        ).encode("utf-8")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        for attempt in range(5):
            try:
                request = urllib.request.Request(self.url, data=body, headers=headers)
                with urllib.request.urlopen(request, timeout=60) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                answer = json.loads(payload["choices"][0]["message"]["content"])
                result = {
                    "season": answer.get("season"),
                    "episode": answer.get("episode"),
                    "confidence": float(answer.get("confidence") or 0),
                }
                self.calls += 1
                self.cache[cache_key] = result
                if self.calls % 20 == 0:
                    self.save()
                time.sleep(0.6)  # stay within free-tier rate limits
                return result
            except urllib.error.HTTPError as error:
                if error.code == 429:
                    time.sleep(2 + 2 * attempt)
                    continue
                if attempt == 4:
                    raise
                time.sleep(1 + attempt)
            except (OSError, ValueError, KeyError):
                if attempt == 4:
                    self.cache[cache_key] = {"season": None, "episode": None, "confidence": 0}
                    return self.cache[cache_key]
                time.sleep(1 + attempt)
        return {"season": None, "episode": None, "confidence": 0}
