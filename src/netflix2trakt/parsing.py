"""Parse Netflix "Viewing History" rows into structured movie / episode candidates.

Netflix exports rows shaped like ``Title,Date`` where ``Title`` is a localized string
that mixes movies and TV episodes, e.g.::

    "Lucifer: Saison 6: Partenaires à jamais"   -> episode (show, season, episode)
    "Ghosts : Fantômes à la maison: Pilote"     -> episode (show name contains " : ")
    "Astérix & Obélix : L'empire du Milieu"     -> movie (French typography, not a split)
    "Lockwood & Co.: Sous emprise"              -> ambiguous (single-season show or movie)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Netflix exports a date only; we stamp a fixed time so timezone conversions never
# shift an entry to the previous/next day.
WATCHED_TIME = "12:00:00"

# Netflix's structural separator is a colon NOT preceded by a space ("Show: Episode").
# A French-typography " : " (space-colon-space) belongs to the title and must not split.
_SEPARATOR = re.compile(r"(?<! ):\s+")

# A middle segment that denotes a season. "Chapitre"/"Épisode" are episode labels, not
# seasons, so they are intentionally excluded here.
_SEASON = re.compile(
    r"^(?:saison|season)\s*(\d+)"
    r"|^(?:partie|part|volume)\s*(\d+)"
    r"|^(?:mini[\- ]?s[ée]rie|mini[\- ]?series|s[ée]rie\s+limit[ée]e|limited\s+series)\b",
    re.IGNORECASE,
)

# Generic episode labels we can match by NUMBER when proper titles are unavailable.
EPISODE_NUMBER = re.compile(
    r"^(?:[ée]pisode|episode|chapitre|chapter|partie|part)\s*(\d+)\s*$", re.IGNORECASE
)

# Netflix dates are US-formatted "M/D/YY".
_DATE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2})\s*$")


@dataclass
class ParsedTitle:
    """Structured view of a Netflix title string."""

    kind: str  # "movie" | "episode" | "ambiguous" | "blank"
    title: str | None = None  # movie title
    show: str | None = None  # series name
    season: int | None = None  # season number (None when unknown)
    episode_title: str | None = None  # episode label
    full: str | None = None  # original full string (kept for the ambiguous case)


def parse_watched_at(date: str) -> str | None:
    """Convert a Netflix ``M/D/YY`` date to an ISO-8601 UTC timestamp, or ``None``."""
    match = _DATE.match(date or "")
    if not match:
        return None
    month, day, year = (int(part) for part in match.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{2000 + year:04d}-{month:02d}-{day:02d}T{WATCHED_TIME}Z"


def parse_title(raw: str) -> ParsedTitle:
    """Classify a Netflix title as a movie, episode, ambiguous row, or blank."""
    text = (raw or "").strip()
    if not text:
        return ParsedTitle(kind="blank")

    parts = [part.strip() for part in _SEPARATOR.split(text)]
    if len(parts) == 1:
        return ParsedTitle(kind="movie", title=parts[0])

    season_index: int | None = None
    season_number: int | None = None
    for index in range(1, len(parts)):
        match = _SEASON.match(parts[index])
        if match:
            season_index = index
            number = next((group for group in match.groups() if group), None)
            season_number = int(number) if number else 1  # mini-series -> season 1
            break

    if season_index is not None:
        return ParsedTitle(
            kind="episode",
            show=": ".join(parts[:season_index]).strip(),
            season=season_number,
            episode_title=": ".join(parts[season_index + 1 :]).strip() or None,
        )

    # A colon with no season keyword: usually "Show: Episode" of a single-season series,
    # but possibly a colon-titled movie. The resolver decides between the two.
    return ParsedTitle(
        kind="ambiguous",
        show=parts[0],
        episode_title=": ".join(parts[1:]).strip(),
        full=text,
    )
