"""Refresh orchestrator — the data pipeline:
collect -> validate -> normalize -> store snapshots -> score -> alert.
Runs on a schedule; also triggered manually via POST /api/refresh."""
import asyncio
import json
import logging

from .. import config
from ..db import get_db, utcnow
from ..providers.base import TitleData
from ..providers.google_trends import GoogleTrendsProvider
from ..providers.imdb import IMDbDatasetsProvider
from ..providers.tmdb import TMDBProvider
from .scoring import compute_and_store_scores
from .validate import deduplicate, validate_title

log = logging.getLogger(__name__)

ALERT_GROWTH_THRESHOLD = 50.0   # % search growth that triggers an alert (documented)


async def _collect_tmdb(tmdb) -> list[TitleData]:
    titles = []
    for media_type in ("movie", "tv"):
        titles.extend(await tmdb.get_trending(media_type))
    return titles


def _upsert_title(db, t: TitleData) -> int:
    db.execute(
        """INSERT INTO titles(provider, provider_id, title, type, genres, release_date,
                               overview, poster_url, backdrop_url, rating, cast, directors,
                               seasons, episodes, imdb_id)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(provider, provider_id, type) DO UPDATE SET
             title=excluded.title, genres=excluded.genres, release_date=excluded.release_date,
             overview=excluded.overview, poster_url=excluded.poster_url,
             backdrop_url=excluded.backdrop_url, rating=excluded.rating,
             cast=excluded.cast, directors=excluded.directors,
             seasons=excluded.seasons, episodes=excluded.episodes,
             imdb_id=excluded.imdb_id""",
        (t.provider, t.provider_id, t.title, t.type, json.dumps(t.genres), t.release_date,
         t.overview, t.poster_url, t.backdrop_url, t.rating, json.dumps(t.cast),
         json.dumps(t.directors), t.seasons, t.episodes, t.imdb_id))
    row = db.execute("SELECT id FROM titles WHERE provider=? AND provider_id=? AND type=?",
                     (t.provider, t.provider_id, t.type)).fetchone()
    return row["id"]


def run_refresh() -> dict:
    started = utcnow()
    with get_db() as db:
        cur = db.execute("INSERT INTO refresh_log(started_at, status) VALUES(?, 'running')",
                         (started,))
        refresh_id = cur.lastrowid

    detail = {"collected": 0, "dropped": 0, "trends_signals": 0, "alerts": 0, "error": None}
    try:
        detail.update(_collect_and_score())
    except Exception as e:
        log.exception("Refresh failed")
        detail["error"] = str(e)
        with get_db() as db:
            db.execute("UPDATE refresh_log SET finished_at=?, status='failed', detail=? WHERE id=?",
                       (utcnow(), json.dumps(detail), refresh_id))
        return detail

    with get_db() as db:
        db.execute("UPDATE refresh_log SET finished_at=?, status='ok', detail=? WHERE id=?",
                   (utcnow(), json.dumps(detail), refresh_id))
    return detail


def _collect_and_score() -> dict:
    detail = {"collected": 0, "dropped": 0, "trends_signals": 0, "alerts": 0}
    tmdb, trends = TMDBProvider(), GoogleTrendsProvider()

    if not tmdb.available:
        log.warning("TMDB_API_KEY not set — metadata collection skipped. "
                    "All metrics will show as unavailable until a key is configured.")

    validated: list[TitleData] = []
    for t in asyncio.run(_collect_tmdb(tmdb)):
        if validate_title(t):
            validated.append(t)
        else:
            detail["dropped"] += 1
    validated = deduplicate(validated)

    with get_db() as db:
        imdb = IMDbDatasetsProvider()
        for t in validated:
            t.collected_at = utcnow()
            title_id = _upsert_title(db, t)
            detail["collected"] += 1

            # Popularity snapshot + metric provenance (verified, source=TMDB).
            if t.popularity is not None:
                db.execute("INSERT INTO snapshots(title_id, popularity, collected_at) VALUES(?,?,?)",
                           (title_id, t.popularity, t.collected_at))
                db.execute(
                    """INSERT INTO metrics(title_id, metric_name, value, source, region, period, collected_at, quality)
                       VALUES(?,?,?,?,?,?,?, 'verified')""",
                    (title_id, "tmdb_popularity", t.popularity, "TMDB", "Global", "current", t.collected_at))

            # IMDb rating/votes from the official IMDb datasets (verified, real).
            if t.imdb_id:
                db.execute("UPDATE titles SET imdb_id=? WHERE id=?", (t.imdb_id, title_id))
                r = imdb.get_rating(t.imdb_id)
                if r:
                    db.execute(
                        """INSERT INTO metrics(title_id, metric_name, value, source, region, period, collected_at, quality)
                           VALUES(?,?,?,?,?,?,?, 'verified')""",
                        (title_id, "imdb_rating", r[0], imdb.name, "Global", "current", t.collected_at))
                    db.execute(
                        """INSERT INTO metrics(title_id, metric_name, value, source, region, period, collected_at, quality)
                           VALUES(?,?,?,?,?,?,?, 'verified')""",
                        (title_id, "imdb_votes", r[1], imdb.name, "Global", "current", t.collected_at))

        # Search-demand measurements (round-robin by oldest Trends collection to cycle all titles in 2 refreshes)
        measured = [t for t in validated if t.popularity is not None]
        # Build map of last search_interest collection per title_id
        last_map = {}
        for row in db.execute("SELECT title_id, MAX(collected_at) as last_at FROM metrics WHERE metric_name='search_interest' GROUP BY title_id"):
            last_map[row["title_id"]] = row["last_at"]
        # Map TitleData -> title_id for sorting
        id_map = {}
        for t in measured:
            r = db.execute("SELECT id FROM titles WHERE provider=? AND provider_id=? AND type=?", (t.provider, t.provider_id, t.type)).fetchone()
            if r:
                id_map[(t.provider, t.provider_id, t.type)] = r["id"]
        def sort_key(td):
            tid = id_map.get((td.provider, td.provider_id, td.type))
            last = last_map.get(tid, "")  # "" sorts first -> never-attempted first
            # oldest last_at first, then higher popularity as tie-breaker
            return (last, -td.popularity if td.popularity else 0)
        measured.sort(key=sort_key)
        geos = config.get_trends_geos()
        # Limit to avoid 429: max 20 titles * len(geos) signals; sleep happens inside provider
        max_titles_per_geo = 20 if len(geos) == 1 else max(5, 20 // len(geos))
        for t in measured[:max_titles_per_geo]:
            row = db.execute("SELECT id FROM titles WHERE provider=? AND provider_id=? AND type=?",
                             (t.provider, t.provider_id, t.type)).fetchone()
            title_id = row["id"]
            title_name = db.execute("SELECT title FROM titles WHERE id=?", (title_id,)).fetchone()["title"]
            for geo in geos:
                for sig in trends.get_signals(title_name, geo=geo,
                                              period=config.GOOGLE_TRENDS_PERIOD):
                    quality = "verified" if sig.value is not None else "unavailable"
                db.execute(
                    """INSERT INTO metrics(title_id, metric_name, value, source, region, period, collected_at, quality)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (title_id, sig.metric_name, sig.value, sig.source, sig.region, sig.period,
                     sig.collected_at, quality))
                if sig.metric_name == "search_interest" and sig.value is not None:
                    db.execute("INSERT INTO snapshots(title_id, search_interest, collected_at) VALUES(?,?,?)",
                               (title_id, sig.value, sig.collected_at))
                detail["trends_signals"] += 1
                if sig.metric_name == "search_growth_pct" and sig.value is not None \
                        and sig.value >= ALERT_GROWTH_THRESHOLD:
                    detail["alerts"] += 1   # surfaced by GET /api/alerts from real metrics

    compute_and_store_scores()
    return detail
