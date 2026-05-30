"""Accent-insensitive text normalization and fuzzy title scoring (standard library only)."""

from __future__ import annotations

import difflib
import re
import unicodedata

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def strip_accents(text: str) -> str:
    """Remove diacritics, e.g. ``"Éternaute"`` -> ``"Eternaute"``."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize(text: str) -> str:
    """Lowercase, drop accents and collapse non-alphanumeric runs to single spaces."""
    return _NON_ALNUM.sub(" ", strip_accents(text or "").lower()).strip()


def title_score(a: str, b: str) -> float:
    """Return a fuzzy similarity in ``[0, 1]``.

    Combines token (Jaccard) overlap, character sequence ratio and substring
    containment so that re-orderings, minor edits and "X" vs "X: subtitle" all score
    highly, while unrelated titles score low.
    """
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    tokens_a, tokens_b = set(na.split()), set(nb.split())
    union = tokens_a | tokens_b
    jaccard = len(tokens_a & tokens_b) / len(union) if union else 0.0
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    containment = 0.95 if (na in nb or nb in na) else 0.0
    return max(ratio, jaccard, containment, 0.5 * ratio + 0.5 * jaccard)
