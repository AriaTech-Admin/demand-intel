"""SQLite storage. Every metric row carries full provenance:
source, metric name, region, time period, collection timestamp, and a
verified/derived flag so the UI can distinguish data quality tiers."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS titles (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,             -- e.g. 'tmdb'
    provider_id TEXT NOT NULL,
    title TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('movie', 'series')),
    genres TEXT DEFAULT '[]',
    release_date TEXT,
    overview TEXT,
    poster_url TEXT,
    backdrop_url TEXT,
    rating REAL,
    cast TEXT DEFAULT '[]',
    directors TEXT DEFAULT '[]',
    seasons INTEGER,
    episodes INTEGER,
    imdb_id TEXT,
    UNIQUE(provider, provider_id, type)
);
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title_id INTEGER NOT NULL REFERENCES titles(id),
    metric_name TEXT NOT NULL,          -- e.g. 'search_interest'
    value REAL,                         -- NULL means: source says unavailable
    source TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT 'Global',
    period TEXT,                        -- e.g. '7d'
    collected_at TEXT NOT NULL,
    quality TEXT NOT NULL DEFAULT 'verified' CHECK(quality IN ('verified', 'derived', 'unavailable'))
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title_id INTEGER NOT NULL REFERENCES titles(id),
    popularity REAL,
    search_interest REAL,
    collected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scores (
    title_id INTEGER PRIMARY KEY REFERENCES titles(id),
    trend_score REAL NOT NULL,
    confidence TEXT NOT NULL,
    explanation TEXT NOT NULL,          -- JSON list of evidence bullets
    components TEXT NOT NULL,           -- JSON of per-component values/availability
    computed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refresh_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS ai_insights (
    title_id INTEGER PRIMARY KEY REFERENCES titles(id),
    insight TEXT NOT NULL,
    model TEXT NOT NULL,
    generated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS availability (
    title_id INTEGER NOT NULL REFERENCES titles(id),
    region TEXT NOT NULL,               -- TMDB watch_region country code, e.g. 'US'
    providers TEXT NOT NULL,            -- JSON list of subscription provider names
    collected_at TEXT NOT NULL,
    PRIMARY KEY(title_id, region)
);
CREATE INDEX IF NOT EXISTS idx_metrics_title ON metrics(title_id, metric_name, region, period);
CREATE INDEX IF NOT EXISTS idx_snapshots_title ON snapshots(title_id, collected_at);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def get_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as db:
        db.executescript(SCHEMA)
        # lightweight migrations for pre-existing databases
        cols = [r[1] for r in db.execute("PRAGMA table_info(titles)")]
        if "imdb_id" not in cols:
            db.execute("ALTER TABLE titles ADD COLUMN imdb_id TEXT")
