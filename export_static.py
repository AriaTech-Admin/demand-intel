"""Export a static snapshot of the current database for GitHub Pages.

The live app needs a Python backend, which GitHub Pages cannot run. This script
exports the real, collected data (with full provenance) into site/ as a
self-contained static page. No values are invented: unavailable metrics are
rendered as "Data unavailable" exactly like the live app.

Usage:  python export_static.py     (then commit site/ and enable Pages)
"""
import json
import sqlite3
from datetime import datetime, timezone

DB_PATH = "demand_intel.db"
OUT = "site"

SNAPSHOT = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row

def latest_metrics(title_id):
    rows = db.execute(
        """SELECT metric_name, value, source, region, period, collected_at, quality
           FROM metrics WHERE title_id=? ORDER BY collected_at DESC""", (title_id,)).fetchall()
    latest = {}
    for x in rows:
        if x["metric_name"] not in latest and x["quality"] != "unavailable":
            latest[x["metric_name"]] = dict(x)
    return latest, rows[0]["collected_at"] if rows else None

titles = []
for r in db.execute(
        """SELECT t.*, sc.trend_score, sc.confidence, sc.explanation
           FROM titles t JOIN scores sc ON sc.title_id=t.id
           ORDER BY sc.trend_score DESC"""):
    latest, last_at = latest_metrics(r["id"])
    hist = db.execute(
        "SELECT search_interest, popularity, collected_at FROM snapshots "
        "WHERE title_id=? ORDER BY collected_at", (r["id"],)).fetchall()
    titles.append({
        "id": r["id"], "title": r["title"], "type": r["type"],
        "genres": json.loads(r["genres"] or "[]"),
        "release_date": r["release_date"], "overview": r["overview"],
        "poster_url": r["poster_url"], "rating": r["rating"],
        "imdb_rating": latest.get("imdb_rating", {}).get("value"),
        "imdb_votes": latest.get("imdb_votes", {}).get("value"),
        "popularity": latest.get("tmdb_popularity", {}).get("value"),
        "search_interest": latest.get("search_interest", {}).get("value"),
        "search_growth_pct": latest.get("search_growth_pct", {}).get("value"),
        "provenance": {k: {kk: vv for kk, vv in v.items() if kk != "metric_name"}
                       for k, v in latest.items()},
        "trend_score": r["trend_score"], "confidence": r["confidence"],
        "why_trending": json.loads(r["explanation"] or "[]"),
        "history": [dict(h) for h in hist],
        "last_updated": last_at,
    })

SNAPSHOT["titles"] = titles
SNAPSHOT["sources"] = [
    {"name": "TMDB", "provides": "metadata, rating, popularity", "url": "https://www.themoviedb.org"},
    {"name": "IMDb Official Datasets", "provides": "IMDb rating & vote counts", "url": "https://datasets.imdbws.com"},
    {"name": "Google Trends", "provides": "relative search interest & 7-day growth", "url": "https://trends.google.com"},
]

import os
os.makedirs(OUT, exist_ok=True)
with open(f"{OUT}/data.json", "w", encoding="utf-8") as f:
    json.dump(SNAPSHOT, f, ensure_ascii=False)
print(f"Exported {len(titles)} titles -> {OUT}/data.json (generated {SNAPSHOT['generated_at']})")
