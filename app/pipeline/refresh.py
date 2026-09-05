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
from ..providers.wikipedia import WikipediaPageviewsProvider
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

            # Region availability (verified, source=TMDB watch providers):
            # "watchable in country X" — never interpreted as popularity there.
            for code, names in (t.watch_providers or {}).items():
                db.execute(
                    """INSERT INTO availability(title_id, region, providers, collected_at)
                       VALUES(?,?,?,?)
                       ON CONFLICT(title_id, region) DO UPDATE SET
                         providers=excluded.providers, collected_at=excluded.collected_at""",
                    (title_id, code, json.dumps(names), t.collected_at))

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

        # Search-demand measurements, batched: up to 5 titles per single Trends
        # call, so one refresh covers the top ~20 titles across ALL configured
        # geos with only ~24 calls (single-title mode needed ~120 for the same).
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
        # Only configured timeframes are collected — never auto-invent 24h/30d/90d.
        periods = config.get_trends_periods()
        MAX_TITLES_PER_REFRESH = 20     # 4 batched calls of 5 per geo
        batch = measured[:MAX_TITLES_PER_REFRESH]
        # resolve title names once
        name_of = {}
        tid_of = {}
        for t in batch:
            row = db.execute("SELECT id, title FROM titles WHERE provider=? AND provider_id=? AND type=?",
                             (t.provider, t.provider_id, t.type)).fetchone()
            if row:
                name_of[t.title] = row["title"]
                tid_of[t.title] = row["id"]
        names = list(name_of.values())
        import time as _time
        throttled = False
        for i in range(0, len(names), 5):
            chunk = names[i:i + 5]
            for gi, geo in enumerate(geos):
                if gi:
                    _time.sleep(8)             # space out geo calls to dodge 429s
                for period_tf in periods:
                    batch_sigs = trends.get_signals_batch(chunk, geo=geo, period=period_tf)
                    if batch_sigs is None:
                        # sustained 429 — Google is throttling this IP; stop the
                        # Trends part of this refresh entirely to let the
                        # throttle window reset instead of burning more quota.
                        log.warning("Trends throttled (geo=%s) — skipping remaining Trends work this refresh", geo)
                        throttled = True
                        break
                    for t_name, sigs in batch_sigs.items():
                        title_id = tid_of[t_name]
                        for sig in sigs:
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
            if throttled:
                break

    compute_and_store_scores()

    # Wikipedia pageviews (official Wikimedia API, absolute numbers) — collected
    # AFTER scoring so Trends rate limits never block this independent signal.
    detail.update(_collect_wikipedia(validated, id_map))
    return detail


def _collect_wikipedia(validated: list[TitleData], id_map: dict) -> dict:
    detail = {"wiki_signals": 0}
    wiki = WikipediaPageviewsProvider()
    try:
        with get_db() as db:
            for t in validated[:20]:
                title_id = id_map.get((t.provider, t.provider_id, t.type))
                if not title_id:
                    continue
                row = db.execute("SELECT article FROM wiki_articles WHERE title_id=?",
                                 (title_id,)).fetchone()
                article = row["article"] if row else None
                if not article:
                    article = wiki.resolve_article(t.title, t.type)
                    if not article:
                        continue                    # no article — metric stays unavailable
                    db.execute("INSERT INTO wiki_articles(title_id, article, resolved_at) VALUES(?,?,?) "
                               "ON CONFLICT(title_id) DO UPDATE SET article=excluded.article, "
                               "resolved_at=excluded.resolved_at",
                               (title_id, article, utcnow()))
                v = wiki.get_weekly_views(article)
                if not v:
                    continue
                if v["total_7d"] < 50:
                    # Almost certainly the wrong article (a real trending title's
                    # page gets more). Don't store misleading numbers; drop the
                    # cached mapping so a future refresh retries resolution.
                    db.execute("DELETE FROM wiki_articles WHERE title_id=?", (title_id,))
                    continue
                for metric, value in (("wiki_views_7d", v["total_7d"]),
                                      ("wiki_views_growth_pct", v["growth_pct"])):
                    if value is None:
                        continue
                    db.execute(
                        """INSERT INTO metrics(title_id, metric_name, value, source, region, period, collected_at, quality)
                           VALUES(?,?,?,?,?,?,?, 'verified')""",
                        (title_id, metric, value, wiki.name, "Global", "7d", utcnow()))
                    detail["wiki_signals"] += 1
    finally:
        wiki.close()
    return detail
