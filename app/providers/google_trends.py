"""Google Trends search-demand provider (via pytrends, unofficial API).
Returns real measurements only: if Trends cannot resolve a title or returns
no data, value=None ("Data unavailable") — never an invented number."""
import logging
import random
import re
import time

from .. import config
from .base import SearchDemandProvider, SearchSignal


def _title_variants(title: str) -> list[str]:
    """Generate fallback title variants for Trends (cleaned, without punctuation)."""
    variants = [title]
    # Remove punctuation, extra spaces
    cleaned = re.sub(r"[^\w\s]", " ", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned and cleaned.lower() != title.lower() and cleaned not in variants:
        variants.append(cleaned)
    # Remove year-like suffix if present (e.g., "Toy Story 5" keep, but "Movie 2026" remove)
    # Add quoted version for very generic terms
    if len(title.split()) == 1 and title.lower() not in ("lovesick",):
        # For single-word titles, try with quotes to improve specificity (optional)
        pass
    return variants[:3]

log = logging.getLogger(__name__)

try:
    from pytrends.request import TrendReq
    HAS_PYTRENDS = True
except Exception:
    HAS_PYTRENDS = False


class GoogleTrendsProvider(SearchDemandProvider):
    name = "Google Trends"

    def get_signals(self, title: str, geo: str = "", period: str = "now 7-d") -> list[SearchSignal]:
        period_label = _period_label(period)
        if not HAS_PYTRENDS:
            log.warning("pytrends not installed; search-demand data unavailable")
            return [SearchSignal("search_interest", None, self.name,
                                 _region_name(geo), period_label, _now())]
        collected = _now()
        df = None
        used_title = title
        last_error = None
        for variant in _title_variants(title):
            for attempt in range(3):
                try:
                    pytrends = TrendReq(hl="en-US", tz=0, timeout=(10, 30))
                    pytrends.build_payload([variant], timeframe=period, geo=geo)
                    df = pytrends.interest_over_time()
                    if df is not None and not df.empty and variant in df.columns:
                        used_title = variant
                        break
                    # Empty or missing column -> try next variant
                    last_error = f"empty or missing column for {variant!r}"
                    df = None
                    break
                except Exception as e:
                    msg = str(e)
                    is_429 = "429" in msg
                    if is_429 and attempt < 2:
                        wait = (2 ** attempt) * 2 + random.uniform(0, 1)
                        log.warning("Google Trends 429 for %r (variant %r) - retry %d/3 after %.1fs: %s", title, variant, attempt+1, wait, e)
                        time.sleep(wait)
                        continue
                    last_error = str(e)
                    log.warning("Google Trends query failed for %r (variant %r): %s", title, variant, e)
                    df = None
                    break
            if df is not None and not df.empty and used_title in df.columns:
                break
            # try next variant
            time.sleep(random.uniform(0.5, 1.0))
        if df is None or df.empty or used_title not in df.columns:
            log.info("Google Trends no data for %r variants %r last_error=%s", title, _title_variants(title), last_error)
            return [SearchSignal("search_interest", None, self.name,
                                 _region_name(geo), period_label, collected)]
        series = df[used_title].astype(float)
        # Relative interest 0-100 as reported by Google Trends (source-defined scale).
        interest = float(series.iloc[-1])
        # Growth: mean of most recent third vs mean of the preceding third of the window.
        n = len(series)
        if n >= 6:
            third = max(1, n // 3)
            recent = series.iloc[-third:].mean()
            prior = series.iloc[-2 * third:-third].mean()
            growth_pct = round((recent - prior) / prior * 100, 1) if prior > 0 else None
        else:
            growth_pct = None
        signals = [
            SearchSignal("search_interest", interest, self.name,
                         _region_name(geo), period_label, collected),
            SearchSignal("search_growth_pct", growth_pct, self.name,
                         _region_name(geo), period_label, collected),
        ]
        time.sleep(random.uniform(1.0, 2.0))   # be polite to the upstream service (increased to avoid 429)
        return signals

    _related_cache: dict[str, tuple[float, list[str]]] = {}
    _CACHE_TTL = 3600  # 1 hour - avoids hammering Trends on every detail open

    def get_related_queries(self, title: str, geo: str = "") -> list[str]:
        """Related/rising queries for context on the detail page (best effort, cached)."""
        if not HAS_PYTRENDS:
            return []
        import time as _time
        key = f"{title}|{geo}"
        cached = self._related_cache.get(key)
        if cached and _time.time() - cached[0] < self._CACHE_TTL:
            return cached[1]
        # polite delay to avoid 429
        time.sleep(random.uniform(1.0, 2.0))
        try:
            pytrends = TrendReq(hl="en-US", tz=0, timeout=(10, 30))
            pytrends.build_payload([title], timeframe="now 7-d", geo=geo)
            related = pytrends.related_queries()
            rising = (related.get(title) or {}).get("rising")
            if rising is not None and not rising.empty:
                result = rising["query"].head(5).tolist()
                self._related_cache[key] = (_time.time(), result)
                return result
        except Exception as e:
            log.warning("related_queries failed for %r: %s", title, e)
        self._related_cache[key] = (_time.time(), [])
        return []


def _period_label(timeframe: str) -> str:
    """Map a pytrends timeframe to the short period label persisted in metrics.

    e.g. "now 7-d" -> "7d", "now 1-d" -> "24h", "today 1-m" -> "30d",
    "today 3-m" -> "90d". Unknown timeframes fall back to "7d" only when they
    contain a 7-day marker, else to the raw timeframe stripped (never invent
    a 24h/30d/90d label that wasn't requested).
    """
    tf = (timeframe or "").strip().lower()
    mapping = {
        "now 1-h": "24h",
        "now 4-h": "24h",
        "now 1-d": "24h",
        "now 7-d": "7d",
        "today 1-m": "30d",
        "today 3-m": "90d",
    }
    if tf in mapping:
        return mapping[tf]
    if "3-m" in tf:
        return "90d"
    if "1-m" in tf or "30-d" in tf or "30d" in tf:
        return "30d"
    if "7-d" in tf or tf == "7d":
        return "7d"
    if "1-d" in tf or "1-h" in tf or "4-h" in tf or tf == "24h":
        return "24h"
    return tf or "7d"


def _region_name(geo: str) -> str:
    for name, code in config.REGIONS.items():
        if code == geo:
            return name
    return "Global"


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
