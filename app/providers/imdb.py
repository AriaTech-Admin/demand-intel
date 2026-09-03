"""IMDb ratings provider — official IMDb datasets (https://datasets.imdbws.com).
These are the free, publicly-licensed datasets IMDb publishes; ratings and vote
counts are real measured values, refreshed daily upstream. We download and cache
the ratings file locally (re-downloaded at most once per day) and look titles up
by their official IMDb id (obtained from TMDB external_ids)."""
import gzip
import logging
import os
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

RATINGS_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
CACHE_TTL_SECONDS = 24 * 3600
CACHE_PATH = Path(os.getenv("DB_PATH", "demand_intel.db")).parent / "_imdb_ratings.tsv.gz"


class IMDbDatasetsProvider:
    name = "IMDb Official Datasets"
    attribution = "Data courtesy of IMDb (https://www.imdb.com), via datasets.imdbws.com"

    def __init__(self):
        self._ratings = None

    def _ensure_loaded(self) -> bool:
        """Download/cache title.ratings.tsv.gz; returns False if unavailable."""
        if self._ratings is not None:
            return True
        fresh = CACHE_PATH.exists() and time.time() - CACHE_PATH.stat().st_mtime < CACHE_TTL_SECONDS
        if not fresh:
            try:
                log.info("Downloading IMDb ratings dataset (%s)", RATINGS_URL)
                with httpx.Client(timeout=120, follow_redirects=True) as client:
                    resp = client.get(RATINGS_URL)
                    resp.raise_for_status()
                    CACHE_PATH.write_bytes(resp.content)
            except Exception as e:
                log.warning("IMDb dataset download failed: %s", e)
                if not CACHE_PATH.exists():
                    return False   # no data -> metrics stay unavailable, nothing fabricated

        import pandas as pd
        with gzip.open(CACHE_PATH, "rt", encoding="utf-8") as f:
            df = pd.read_csv(f, sep="\t", usecols=["tconst", "averageRating", "numVotes"],
                             dtype={"tconst": str, "averageRating": "float64", "numVotes": "int64"})
        self._ratings = df.set_index("tconst")
        return True

    def get_rating(self, imdb_id: str):
        """Returns (averageRating, numVotes) or None if the id is unknown."""
        if not imdb_id or not self._ensure_loaded():
            return None
        try:
            row = self._ratings.loc[imdb_id]
            if isinstance(row, __import__("pandas").DataFrame):   # duplicate index edge case
                row = row.iloc[0]
            return float(row["averageRating"]), int(row["numVotes"])
        except (KeyError, ValueError):
            return None
