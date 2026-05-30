# netflix2trakt

Convert your **Netflix viewing history** into a CSV you can import into **[Trakt.tv](https://trakt.tv)** — movies *and* individual TV episodes.

![Python](https://img.shields.io/badge/python-3.9%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)

Netflix only exports a localized **title** and a **date** per row, but Trakt's importer needs a real **ID**. This tool resolves every row to its [TMDB](https://www.themoviedb.org) id and writes a ready-to-import CSV. It is pure Python (standard library only) and caches every API call, so re-runs are fast and reproducible.

## How it works

For each row it applies the most certain strategy that fits, and **abstains rather than guesses** when none is safe:

1. **Show resolution** — disambiguates same-named remakes (e.g. *Dynasty* 1981 vs the 2017 reboot) by which candidate's episodes match what was watched.
2. **Title / number match** — matches an episode by its localized title (in French *and* English) or by episode number.
3. **Watch-order inference** — when titles diverge, fills a *fully watched* season by viewing order, but only when confident anchors prove the ordering is correct.
4. **Optional LLM fallback** (`--llm`) — a constrained Mistral prompt picks from the real candidate list; the code validates the choice. Off by default.

Unresolved rows go to `needs_review.csv` instead of being mis-mapped. Multiple CSVs (e.g. several profiles) are merged and de-duplicated.

## Requirements

- Python 3.9+
- A free **TMDB API key**: <https://www.themoviedb.org/settings/api> (the v4 "API Read Access Token" is recommended).
- *Optional:* a **Mistral API key** for `--llm` (<https://console.mistral.ai>).

## Install

```bash
git clone https://github.com/OWNER/netflix2trakt.git
cd netflix2trakt
pip install .
```

## Configure

```bash
cp env.example .env      # then add your key(s); .env is git-ignored
```

```ini
TMDB_BEARER=<your-v4-read-access-token>   # or use TMDB_API_KEY for a v3 key
# MISTRAL_API_KEY=...       # only for --llm
```

Get your history from Netflix → **Account → Viewing activity → Download all**.

## Usage

```bash
netflix2trakt --file NetflixViewingHistory.csv
netflix2trakt --file profile1.csv profile2.csv --out trakt_import.csv
netflix2trakt --file NetflixViewingHistory.csv --llm
```

| Option | Description |
| --- | --- |
| `--file CSV [CSV ...]` | **Required.** One or more Netflix CSVs (merged). |
| `--out CSV` | Output file (default: `trakt_import.csv`). |
| `--llm` | Enable the optional Mistral fallback. |
| `--limit N` | Process only the first N rows. |

## Output

| File | Contents |
| --- | --- |
| `trakt_import.csv` | The import (`tmdb_id,type,watched_at`). Upload it at **Trakt → Settings → [Import](https://trakt.tv/settings/data)**. |
| `needs_review.csv` | Rows with no id (blank rows, items absent from TMDB, divergent titles). |
| `inferred_details.csv` | Audit of episodes chosen by order/LLM, to spot-check. |

Confident matches are very reliable; order-inferred rows assume in-order viewing (and are listed in `inferred_details.csv`); the `--llm` fallback raises coverage but is a judgement, so review it if used. Netflix gives a date only, stamped at 12:00 UTC.

## Development

```bash
pip install -e ".[dev]"
pytest          # unit tests, no network
ruff check .    # lint
```

Modules under `src/netflix2trakt/`: `parsing`, `text`, `tmdb`, `mistral`, `matching`, `pipeline`, `cli`.

## License

[MIT](LICENSE) © Thomas Ott. Not affiliated with Netflix, Trakt or TMDB; uses the TMDB API but is not endorsed by TMDB.
