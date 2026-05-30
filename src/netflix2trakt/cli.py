"""Command-line interface: read Netflix CSV(s), run the pipeline, write Trakt CSV(s)."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter

from . import __version__
from .mistral import MistralClient
from .parsing import parse_title, parse_watched_at
from .pipeline import HistoryRow, run
from .tmdb import TmdbAuthError, TmdbClient

TMDB_CACHE_FILE = "tmdb_cache.json"
LLM_CACHE_FILE = "llm_cache.json"
REVIEW_FILE = "needs_review.csv"
DETAILS_FILE = "inferred_details.csv"


def load_dotenv(path: str = ".env") -> None:
    """Load ``KEY=value`` pairs from a .env file. Existing environment variables win."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def read_history(paths: list[str]) -> tuple[list[HistoryRow], list[tuple[str, int]]]:
    """Read and merge the given Netflix CSVs into ordered :class:`HistoryRow` objects."""
    rows: list[HistoryRow] = []
    per_file: list[tuple[str, int]] = []
    index = 0
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            records = list(csv.reader(handle))[1:]  # skip the "Title,Date" header
        per_file.append((path, len(records)))
        for record in records:
            if len(record) < 2:
                continue
            raw_title, date = record[0], record[1]
            rows.append(
                HistoryRow(
                    raw_title=raw_title,
                    watched_at=parse_watched_at(date) or "unknown",
                    parsed=parse_title(raw_title),
                    index=index,
                )
            )
            index += 1
    return rows, per_file


def write_csv(path: str, header: list[str], records) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(records)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="netflix2trakt",
        description="Convert Netflix viewing-history CSV(s) into a Trakt.tv import CSV.",
    )
    parser.add_argument(
        "--file", required=True, nargs="+", metavar="CSV",
        help="one or more Netflix viewing-history CSV files (merged into one import)",
    )
    parser.add_argument(
        "--out", default="trakt_import.csv", metavar="CSV",
        help="output Trakt import file (default: trakt_import.csv)",
    )
    parser.add_argument(
        "--llm", action="store_true",
        help="enable the optional Mistral fallback for titles order-inference cannot place",
    )
    parser.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="process only the first N rows (for quick tests)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str | None] = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv()

    for path in args.file:
        if not os.path.exists(path):
            print(f"error: file not found: {path}", file=sys.stderr)
            return 2

    out_dir = os.path.dirname(os.path.abspath(args.out)) or "."
    os.makedirs(out_dir, exist_ok=True)

    rows, per_file = read_history(args.file)
    if len(args.file) > 1:
        print("Input files (merged):")
        for path, count in per_file:
            print(f"  {count:6} rows  {path}")
        print(f"  raw total: {len(rows)} rows")
    if args.limit:
        rows = rows[: args.limit]

    try:
        tmdb = TmdbClient(
            os.path.join(out_dir, TMDB_CACHE_FILE),
            bearer=os.environ.get("TMDB_BEARER") or os.environ.get("TMDB_TOKEN"),
            api_key=os.environ.get("TMDB_API_KEY"),
        )
    except TmdbAuthError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    mistral = MistralClient(
        os.path.join(out_dir, LLM_CACHE_FILE), api_key=os.environ.get("MISTRAL_API_KEY")
    )
    if args.llm and not mistral.enabled:
        print("warning: --llm given but MISTRAL_API_KEY is unset; skipping LLM fallback.", file=sys.stderr)

    result = run(rows, tmdb, mistral, use_llm=args.llm, log=print)

    # Merge confident + inferred entries and drop exact-duplicate plays
    # (same id + type + date, e.g. the same title watched on two accounts the same day).
    merged: list[tuple] = []
    seen: set[tuple] = set()
    for entry in result.confident + result.inferred:
        if entry not in seen:
            seen.add(entry)
            merged.append(entry)
    dropped = len(result.confident) + len(result.inferred) - len(merged)

    write_csv(args.out, ["tmdb_id", "type", "watched_at"], merged)
    write_csv(
        os.path.join(out_dir, REVIEW_FILE),
        ["raw_title", "date", "kind", "best_guess", "reason", "score"],
        [(r.raw_title, r.watched_at, r.kind, r.best_guess, r.reason, r.score) for r in result.review],
    )
    write_csv(
        os.path.join(out_dir, DETAILS_FILE),
        ["raw_title", "date", "show", "episode", "tmdb_episode_name", "tmdb_id", "source"],
        [(d.raw_title, d.watched_at, d.show, d.episode_code, d.episode_name, d.tmdb_id, d.source)
         for d in result.infer_details],
    )

    total = result.total_rows
    n_confident, n_inferred, n_review = len(result.confident), len(result.inferred), len(result.review)
    coverage = 100 * (n_confident + n_inferred) / total if total else 0.0
    extra = f", {dropped} duplicates removed" if dropped else ""
    print("\n==== done ====")
    print(f"rows (excluding blank): {total}")
    print(f"  {args.out}: {len(merged)} entries  [{n_confident} confident + {n_inferred} inferred{extra}]")
    print(f"  {os.path.join(out_dir, REVIEW_FILE)}: {n_review} rows without an id (not importable)")
    print(f"  coverage: {coverage:.1f}%")
    print(f"  review reasons: {dict(Counter(r.reason for r in result.review))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
