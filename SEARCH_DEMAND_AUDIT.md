# Search Demand - Unavailable Data Audit
Date: 2026-09-03 | App: demand-intel | DB: 40 titles, 286 metrics, 98 snapshots (3 refreshes)

## Executive Summary
GET /api/search-demand returns only titles with verified search_growth_pct (18/40 = 45%). The other 22 titles (55%) show Data unavailable because Google Trends did not return a verifiable measurement in any refresh. By design no fabricated data (README, app/pipeline/validate.py).

Live test:
- search-demand?region=Global&period=7d&intensity=all -> 18 titles
- trending -> 40 titles -> 22 missing (Lovesick id37, The Dog Stars, etc) provenance only tmdb_popularity
- Detail id37 Lovesick: search_growth false, interest false, popularity_growth true 0.0, recency true, history 2
- intensity high >=25% -> 1 (The Runner 73.2), medium 5-25% -> 0 (most growth -2 to -44)

## Test Matrix Details
- region=United States -> 0 (pipeline collects only Global, app/config.py get_trends_geos -> [""])
  metrics.region never US. app/pipeline/refresh.py:108 loops geos from config.
- period=24h/30d/90d -> 0 (only 7d stored, period now 7-d)
- genre=Thriller -> 4 correct (The Runner, Obsession, Mutiny, Whisper Man)
- GET /api/regions now returns available_regions [Global] and available_periods [7d]

## DB Breakdown
tmdb_popularity verified 80
search_interest verified 18 / unavailable 22
search_growth_pct verified 18
imdb_rating/votes verified 74 (missing for new titles like The Runner tt34564059, expected)

Snapshots: popularity 40 distinct titles, search_interest 18 titles. Scores confidence High 18 / Medium 22.

## Root Causes
P1 Under-Sampling:
- app/pipeline/refresh.py:111 measured[:20] top 20 by TMDB popularity only. Other 20 never queried that refresh.
- app/providers/google_trends.py:20 on 429 or df.empty returns SearchSignal(None, unavailable) without retry/backoff. server.log shows 2x Trends 429 (Toy Story 5, Minions & Monsters) + 9x related_queries 429.

P1 Growth null:
- provider growth_pct = None when n<6 or prior==0 (google_trends.py:33). Stored NULL -> excluded by search-demand AND m.value IS NOT NULL.

P2 Popularity growth:
- app/pipeline/scoring.py:52 requires 2 snapshots. 3 snapshots exist but TMDB popularity flat across ~1h -> delta 0 -> still available for some (Lovesick true 0.0) but false for others due to snapshot timing.

P2 Filters promise unavailable data: UI offered all regions/periods but DB only Global/7d. Fixed via app/static/app.js emptyState explaining GOOGLE_TRENDS_GEOS.

P3 IMDb missing expected for unreleased.

P3 Intensity thresholds high >=25 / medium 5-25 mismatch current negative distribution.

## Recommendations
A Short-term (done):
- Keep Data unavailable, emptyState with explanation (app/static/app.js:108, app/main.py:149 available_regions).

B Medium-term (increase coverage):
1. Stagger round-robin: sort measured by last search_interest collected_at not popularity, cycle 40 titles in 2 refreshes.
2. 429 backoff: google_trends.py add exponential retry 2^attempt *2s, keep 1-2s polite sleep, cache unavailable 6h.
3. Persist unavailable rows for all attempts to distinguish never-attempted.

C Longer-term:
4. Multi-period opt-in (GOOGLE_TRENDS_PERIODS)
5. Fix popularity_growth availability for flat values
6. Supplemental providers via base.py SearchDemandProvider
7. Retune intensity thresholds symmetric for declining interest.

## Files to Touch
- app/pipeline/refresh.py:108-121
- app/providers/google_trends.py:20-42
- app/pipeline/scoring.py:33-55
- app/static/app.js:108
- app/config.py:28

Verification: re-run diag_sd.py after B.1+B.2, expect search_interest verified >30.
