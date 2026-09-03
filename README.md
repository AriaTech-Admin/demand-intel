# Content Demand Intelligence — MVP

Identifies **movies and TV series people are becoming interested in right now**, using only real,
verifiable external data. Every metric carries its **source, region, time period, and collection
timestamp**. Nothing is invented, estimated, or simulated — where a source does not provide a
metric, the UI shows **"Data unavailable"**.

## Quick start

```bash
python -m pip install -r requirements.txt
copy .env.example .env          # then add your free TMDB_API_KEY
python -m uvicorn app.main:app --reload
# open http://127.0.0.1:8000
```

Then either wait for the scheduled refresh (default: every 60 min) or trigger one:

```bash
curl -X POST http://127.0.0.1:8000/api/refresh
```

## Data sources (real, replaceable)

| Provider | What it provides | Module |
|---|---|---|
| TMDB | metadata (title, poster, genres, cast, dates, ratings) + its own popularity metric | `app/providers/tmdb.py` |
| Google Trends (pytrends) | relative search interest (0–100) and 7-day search growth | `app/providers/google_trends.py` |

Providers implement the interfaces in `app/providers/base.py`
(`MetadataProvider`, `SearchDemandProvider`). Adding a new source = adding one class + one line in
the refresh pipeline; nothing else changes.

**Without a TMDB key the app collects nothing and shows "Data unavailable" everywhere —
by design. It never fabricates values.**

## Data pipeline

```
External APIs → Data Collection → Validation → Normalization → Trend Detection
→ Scoring Engine → SQLite (with provenance) → Dashboard
```

- **Validation** (`app/pipeline/validate.py`): checks title/type/id, timestamp and numeric sanity,
  drops invalid records, de-duplicates. Invalid values become "unavailable", never guessed.
- **Storage** (`app/db.py`): every metric row stores `source`, `region`, `period`,
  `collected_at`, and a quality tier (`verified` / `derived`). Snapshots accumulate so real
  historical charts appear over time — no synthetic history.
- **Scheduling** (`app/scheduler.py`): APScheduler refreshes on an interval; retries + backoff on
  HTTP errors and 429 rate limits (`app/providers/tmdb.py`).

## Trend scoring (documented & configurable)

```
Trend Score = 0.40·search_growth + 0.30·popularity_growth
            + 0.20·current_interest + 0.10·recency          (scaled 0–100)
```

Weights live in `app/config.py` (`SCORING_WEIGHTS`) and are exposed via `GET /api/status`.
Each component is normalized to [0,1]; components without real data contribute 0 and are listed as
unavailable. A title is only ranked with ≥2 real components, and confidence
(High/Medium/Low) reflects what share of the score is backed by real data. Momentum
(growth) outweighs static popularity by construction.

The dashboard shows "Why is this trending?" evidence bullets and the per-component breakdown on
every detail page.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/trending?type=all\|movie\|series` | Trending Now (by trend score) |
| `GET /api/search-demand?genre=&type=&region=&period=&intensity=` | Rising search interest, filterable |
| `GET /api/titles/{id}` | Detail: score, why-trending, components, provenance, real snapshots, related queries |
| `GET /api/alerts` | Titles with measured search growth ≥ 50% (real data only) |
| `POST /api/refresh` | Trigger the pipeline now |
| `GET /api/status` | Counts, last refresh, scoring weights, TMDB key status |

## Design for expansion

Modular by intent: new providers (JustWatch, YouTube, social APIs), alternative scoring models,
regional pipelines, alerting channels, and acquisition-recommendation modules can plug into
`providers/`, `pipeline/scoring.py`, and `db.py` without rewriting the app.

## Known limits

- Google Trends gives **relative** interest (0–100 within a query window), not absolute volume —
  displayed and labeled as such.
- TMDB popularity is TMDB's own metric, not viewership; the app never claims "views".
- pytrends is an unofficial Google Trends client and may be rate-limited; failures surface as
  "Data unavailable", never as placeholder numbers.
