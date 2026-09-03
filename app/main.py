"""FastAPI application: JSON API + static dashboard."""
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .db import get_db, init_db, utcnow
from .pipeline.refresh import run_refresh
from .providers.gemini import generate_insight, get_cached_insight, set_cached_insight
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    from .scheduler import start_scheduler, stop_scheduler
    start_scheduler()
    yield
    stop_scheduler()

app = FastAPI(title="Content Demand Intelligence MVP", lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"
init_db()


# ---------------- helpers ----------------

def _title_row(db, r) -> dict:
    """Serialize a titles join row, exposing metrics with full provenance."""
    m = db.execute(
        """SELECT metric_name, value, source, region, period, collected_at, quality
           FROM metrics WHERE title_id=? AND metric_name IN
           ('search_interest','search_growth_pct','tmdb_popularity','imdb_rating','imdb_votes')
           ORDER BY collected_at DESC""", (r["id"],)).fetchall()
    latest = {}
    for x in m:                                   # keep most recent per metric
        if x["metric_name"] not in latest and x["quality"] != "unavailable":
            latest[x["metric_name"]] = dict(x)
    last_metric_at = m[0]["collected_at"] if m else r.get("collected_at")
    return {
        "id": r["id"],
        "title": r["title"],
        "type": r["type"],
        "genres": json.loads(r["genres"] or "[]"),
        "release_date": r["release_date"],
        "overview": r["overview"],
        "poster_url": r["poster_url"],
        "backdrop_url": r["backdrop_url"],
        "rating": r["rating"],
        "cast": json.loads(r["cast"] or "[]"),
        "directors": json.loads(r["directors"] or "[]"),
        "seasons": r["seasons"],
        "episodes": r["episodes"],
        "popularity": latest.get("tmdb_popularity", {}).get("value"),
        "search_interest": latest.get("search_interest", {}).get("value"),
        "search_growth_pct": latest.get("search_growth_pct", {}).get("value"),
        "imdb_rating": latest.get("imdb_rating", {}).get("value"),
        "imdb_votes": latest.get("imdb_votes", {}).get("value"),
        "provenance": {k: {kk: vv for kk, vv in v.items() if kk != "metric_name"}
                       for k, v in latest.items()},
        "last_updated": last_metric_at,
    }


# ---------------- API ----------------

@app.get("/api/trending")
def trending(type: str = Query("all", pattern="^(all|movie|series)$"), limit: int = 24):
    with get_db() as db:
        sql = """SELECT t.*, sc.trend_score, sc.confidence, sc.explanation, sc.computed_at
                 FROM titles t JOIN scores sc ON sc.title_id=t.id"""
        params: list = []
        if type != "all":
            sql += " WHERE t.type=?"
            params.append("series" if type == "series" else "movie")
        sql += " ORDER BY sc.trend_score DESC LIMIT ?"
        params.append(limit)
        rows = db.execute(sql, params).fetchall()
        return {"titles": [_title_row(db, r) | {
            "trend_score": r["trend_score"], "confidence": r["confidence"],
            "why_trending": json.loads(r["explanation"] or "[]")} for r in rows],
            "tmdb_configured": bool(config.TMDB_API_KEY)}


@app.get("/api/search-demand")
def search_demand(
    type: str = Query("all", pattern="^(all|movie|series)$"),
    genre: str = "All",
    region: str = "Global",
    period: str = Query("7d", pattern="^(24h|7d|30d|90d)$"),
    intensity: str = Query("all", pattern="^(all|high|medium)$"),
    limit: int = 24,
):
    with get_db() as db:
        sql = """SELECT t.*, sc.trend_score, sc.confidence, sc.explanation, m.value AS growth, m.source, m.region, m.period, m.collected_at
                 FROM titles t
                 LEFT JOIN metrics m ON m.title_id=t.id AND m.metric_name='search_growth_pct'
                 JOIN scores sc ON sc.title_id=t.id
                 WHERE 1=1"""
        params: list = []
        if type != "all":
            sql += " AND t.type=?"
            params.append("series" if type == "series" else "movie")
        if genre != "All":
            sql += " AND t.genres LIKE ?"
            params.append(f'%"{genre}"%')
        if region != "Global":
            sql += " AND m.region=?"
            params.append(region)
        if period != "7d":
            sql += " AND m.period=?"
            params.append(period)
        if intensity == "high":
            sql += " AND m.value >= 25"
        elif intensity == "medium":
            sql += " AND m.value >= 5 AND m.value < 25"
        sql += " ORDER BY CASE WHEN m.value IS NULL THEN 1 ELSE 0 END, m.value DESC LIMIT ?"
        params.append(limit)
        rows = db.execute(sql, params).fetchall()
        return {"titles": [_title_row(db, r) | {
            "search_growth_pct": r["growth"], "confidence": r["confidence"],
            "why_trending": json.loads(r["explanation"] or "[]")} for r in rows],
            "tmdb_configured": bool(config.TMDB_API_KEY)}


@app.get("/api/titles/{title_id}")
def title_detail(title_id: int):
    with get_db() as db:
        r = db.execute(
            """SELECT t.*, sc.trend_score, sc.confidence, sc.explanation, sc.components
               FROM titles t LEFT JOIN scores sc ON sc.title_id=t.id WHERE t.id=?""",
            (title_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Title not found")
        out = _title_row(db, r)
        out["trend_score"] = r["trend_score"]
        out["confidence"] = r["confidence"]
        out["why_trending"] = json.loads(r["explanation"] or "[]")
        out["components"] = json.loads(r["components"] or "{}")
        # Real time-series from snapshots only — never synthesized history.
        out["history"] = [dict(s) for s in db.execute(
            "SELECT popularity, search_interest, collected_at FROM snapshots "
            "WHERE title_id=? ORDER BY collected_at", (title_id,)).fetchall()]
        out["related_queries"] = []
        if out["search_interest"] is not None:
            from .providers.google_trends import GoogleTrendsProvider
            out["related_queries"] = GoogleTrendsProvider().get_related_queries(out["title"])
        return out


@app.get("/api/regions")
def regions():
    with get_db() as db:
        rows = db.execute('SELECT DISTINCT region FROM metrics WHERE metric_name=\'search_growth_pct\' AND value IS NOT NULL').fetchall()
        available = [x["region"] for x in rows]
        # Always at least Global is expected once data collected
        period_rows = db.execute('SELECT DISTINCT period FROM metrics WHERE metric_name=\'search_growth_pct\' AND value IS NOT NULL').fetchall()
        available_periods = [x["period"] for x in period_rows]
    return {"regions": config.REGIONS, "available_regions": available, "available_periods": available_periods, "configured_geos": config.get_trends_geos()}


@app.get("/api/titles/{title_id}/ai-insights")
def ai_insights(title_id: int):
    """Derived AI insight (Gemini) - cached 24h, never fabricates verified metrics."""
    with get_db() as db:
        r = db.execute("SELECT t.*, sc.trend_score FROM titles t LEFT JOIN scores sc ON sc.title_id=t.id WHERE t.id=?", (title_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Title not found")
        cached = db.execute("SELECT insight, model, generated_at FROM ai_insights WHERE title_id=?", (title_id,)).fetchone()
        if cached:
            from datetime import datetime, timezone
            try:
                gen = datetime.fromisoformat(cached["generated_at"].replace("Z","+00:00"))
                if (datetime.now(timezone.utc) - gen).total_seconds() < 24*3600:
                    return {"insight": json.loads(cached["insight"]), "model": cached["model"], "generated_at": cached["generated_at"], "cached": True}
            except: pass
        mem = get_cached_insight(title_id)
        if mem:
            return {"insight": mem, "model": mem.get("model"), "generated_at": None, "cached": True}
        title_row = _title_row(db, r)
        insight = generate_insight(title=title_row["title"], type_=title_row["type"], genres=title_row["genres"], overview=title_row["overview"], rating=title_row["rating"], search_interest=title_row["search_interest"], search_growth=title_row["search_growth_pct"], popularity=title_row["popularity"], trend_score=r["trend_score"])
        if not insight:
            # Fallback to OpenCode if Gemini unavailable
            try:
                from .providers.opencode import generate_opencode_insight
                insight = generate_opencode_insight(title=title_row["title"], type_=title_row["type"], genres=title_row["genres"], overview=title_row["overview"])
            except Exception:
                insight = None
        if not insight:
            raise HTTPException(503, "AI insights unavailable (Gemini/OpenCode not configured or failed)")
        set_cached_insight(title_id, insight)
        db.execute("INSERT INTO ai_insights(title_id, insight, model, generated_at) VALUES(?,?,?,?) ON CONFLICT(title_id) DO UPDATE SET insight=excluded.insight, model=excluded.model, generated_at=excluded.generated_at", (title_id, json.dumps(insight), insight.get("model","gemini-3.6-flash"), utcnow()))
        return {"insight": insight, "model": insight.get("model"), "generated_at": utcnow(), "cached": False}



@app.get("/api/genres")
def genres():
    with get_db() as db:
        rows = db.execute("SELECT genres FROM titles").fetchall()
        gs = sorted({g for r in rows for g in json.loads(r["genres"] or "[]") if g})
        return {"genres": gs}


@app.get("/api/alerts")
def alerts():
    """Alerts derived only from real measured growth (documented threshold)."""
    with get_db() as db:
        rows = db.execute(
            """SELECT t.title, t.type, t.id, m.value, m.source, m.region, m.period, m.collected_at
               FROM metrics m JOIN titles t ON t.id=m.title_id
               WHERE m.metric_name='search_growth_pct' AND m.value >= 50
               ORDER BY m.collected_at DESC LIMIT 20""").fetchall()
        return {"alerts": [dict(r) for r in rows]}


@app.post("/api/refresh")
def refresh():
    detail = run_refresh()
    if detail.get("error"):
        raise HTTPException(502, detail["error"])
    return detail


@app.get("/api/status")
def status():
    with get_db() as db:
        last = db.execute("SELECT * FROM refresh_log ORDER BY id DESC LIMIT 1").fetchone()
        counts = {
            "titles": db.execute("SELECT COUNT(*) c FROM titles").fetchone()["c"],
            "metrics": db.execute("SELECT COUNT(*) c FROM metrics").fetchone()["c"],
            "snapshots": db.execute("SELECT COUNT(*) c FROM snapshots").fetchone()["c"],
        }
    # AI insights count (derived, not verified)
    try:
        ai_count = db.execute("SELECT COUNT(*) c FROM ai_insights").fetchone()["c"]
    except:
        ai_count = 0
    return {"counts": {**counts, "ai_insights": ai_count}, "last_refresh": dict(last) if last else None,
            "tmdb_configured": bool(config.TMDB_API_KEY),
            "gemini_configured": bool(config.GEMINI_API_KEY),
            "opencode_configured": bool(config.OPENCODE_API_KEY),
            "scoring_weights": config.SCORING_WEIGHTS,
            "server_time_utc": utcnow()}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
