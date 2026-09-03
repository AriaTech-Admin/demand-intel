"""Central configuration, including the documented, configurable trend-scoring weights."""
import os
from dotenv import load_dotenv

load_dotenv()

# --- External provider credentials -----------------------------------------
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
OPENCODE_API_KEY = os.getenv("OPENCODE_API_KEY", "")  # OpenCode services - never exposed via API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")  # Google Gemini - for AI features (never exposed via API)
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

# --- Scoring engine ----------------------------------------------------------
# Trend Score = weighted sum of normalized components in [0, 1], scaled to [0, 100].
# Every weight below is documented and can be tuned without code changes.
SCORING_WEIGHTS = {
    # Growth of search interest (Google Trends, latest window vs previous window).
    "search_growth_weight": 0.40,
    # Growth of TMDB popularity between collection snapshots (momentum, not level).
    "popularity_growth_weight": 0.30,
    # Current interest level normalized against the current candidate pool.
    "interest_weight": 0.20,
    # Recency of release (newer titles score higher; decays over ~365 days).
    "recency_weight": 0.10,
}
# A title needs at least this many scored components with real data to be ranked;
# components with unavailable data contribute 0 and are flagged in the explanation.
MIN_COMPONENTS_FOR_RANKING = 2

# Trend confidence thresholds (share of components backed by real data).
CONFIDENCE_HIGH = 0.75
CONFIDENCE_MEDIUM = 0.5

# --- Data collection ----------------------------------------------------------
REFRESH_INTERVAL_MINUTES = int(os.getenv("REFRESH_INTERVAL_MINUTES", "60"))
# Max titles pulled per trending list per type, per refresh.
TMDB_TRENDING_WINDOW = "week"          # day | week
TMDB_MAX_TITLES = 40
GOOGLE_TRENDS_GEO = ""                  # "" = worldwide (legacy, single region)
# Comma-separated list of geo codes to collect. Empty or "GLOBAL" means worldwide.
# Example: "GLOBAL,US,GB" collects worldwide + US + UK. Each extra region adds ~20 Trends calls per refresh.
GOOGLE_TRENDS_GEOS = [g.strip() for g in os.getenv("GOOGLE_TRENDS_GEOS", "GLOBAL").split(",") if g.strip()]
GOOGLE_TRENDS_PERIOD = "now 7-d"        # last 7 days

# Geographic regions offered as filters (Google Trends 'geo' codes; "" = worldwide).
REGIONS = {
    "Global": "",
    "United States": "US",
    "United Kingdom": "GB",
    "Canada": "CA",
    "Australia": "AU",
    "India": "IN",
    "Germany": "DE",
    "France": "FR",
    "Brazil": "BR",
    "Japan": "JP",
}

DB_PATH = os.getenv("DB_PATH", "demand_intel.db")


def get_trends_geos() -> list[str]:
    """Resolve GOOGLE_TRENDS_GEOS to pytrends geo codes ("" = worldwide)."""
    out = []
    for g in GOOGLE_TRENDS_GEOS:
        if g.upper() in ("", "GLOBAL", "WORLDWIDE"):
            if "" not in out:
                out.append("")
        elif g.upper() in REGIONS:
            code = REGIONS[g]
            if code not in out:
                out.append(code)
        elif g in REGIONS.values():
            if g not in out:
                out.append(g)
        else:
            # raw code like US
            if g not in out:
                out.append(g)
    return out or [GOOGLE_TRENDS_GEO]