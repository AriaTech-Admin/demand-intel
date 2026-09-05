"""Wikipedia pageviews provider — official Wikimedia REST API (free, CC0).
Gives ABSOLUTE daily view counts for a title's Wikipedia article: a third,
independent interest signal (TMDB popularity + Google Trends + this).
Article resolution: try exact name, common disambiguators, then MediaWiki
search; the resolved article is cached so each title is resolved only once.
Views are real measured numbers — never invented."""
import logging
import re
import time

import httpx

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "ArianaTools/1.0 (content demand intelligence MVP; contact: admin@arianatools)"}
REST = "https://wikimedia.org/api/rest_v1"
SEARCH = "https://en.wikipedia.org/w/api.php"


class WikipediaPageviewsProvider:
    name = "Wikipedia Pageviews"

    def __init__(self):
        self._client = httpx.Client(timeout=20, headers=HEADERS)

    # ---- article resolution -------------------------------------------------

    def _exists(self, article: str) -> bool:
        """REST pageviews returns 404 for articles that never had views — but
        existence is better checked via the API. Returns False on 404."""
        resp = self._client.get(
            f"{REST}/metrics/pageviews/per-article/en.wikipedia.org/all-access/user/"
            f"{article}/daily/20260101/20260102")
        return resp.status_code == 200

    def resolve_article(self, title: str, type_: str = "") -> str | None:
        """Best-effort mapping of a movie/series title to its English Wikipedia
        article. Disambiguated forms are tried FIRST ('Wednesday' the weekday
        must not shadow 'Wednesday (TV series)'), then exact, then search."""
        candidates = []
        if type_ == "series":
            candidates += [f"{title}_(TV_series)".replace(" ", "_")]
        else:
            candidates += [f"{title}_(film)".replace(" ", "_"),
                           f"{title}_(2025_film)".replace(" ", "_"),
                           f"{title}_(2026_film)".replace(" ", "_")]
        candidates.append(title.replace(" ", "_"))
        cleaned = re.sub(r"[^\w\s()]", "", title).strip()
        if cleaned and cleaned != title:
            candidates.append(cleaned.replace(" ", "_"))
        for c in candidates:
            try:
                if self._exists(c):
                    return c
            except Exception as e:
                log.warning("Wikipedia existence check failed for %r: %s", c, e)
                return None
        # fallback: MediaWiki search, take the first hit
        try:
            resp = self._client.get(SEARCH, params={
                "action": "opensearch", "search": title, "limit": 1, "format": "json"})
            if resp.status_code == 200:
                results = resp.json()[1]
                if results:
                    return results[0].replace(" ", "_")
        except Exception as e:
            log.warning("Wikipedia search failed for %r: %s", title, e)
        return None

    # ---- measurements -------------------------------------------------------

    def get_weekly_views(self, article: str) -> dict | None:
        """Last-7-day absolute views + growth (recent 3 days vs prior 3 days,
        same shape as the Trends growth so scoring stays comparable)."""
        from datetime import date, timedelta
        end = date.today() - timedelta(days=1)      # today is often incomplete
        start = end - timedelta(days=7)
        url = (f"{REST}/metrics/pageviews/per-article/en.wikipedia.org/all-access/user/"
               f"{article}/daily/{start.strftime('%Y%m%d')}/{end.strftime('%Y%m%d')}")
        try:
            resp = self._client.get(url)
            if resp.status_code == 404:
                return None                          # article exists but no views yet
            resp.raise_for_status()
            items = resp.json().get("items", [])
        except Exception as e:
            log.warning("Wikipedia pageviews failed for %r: %s", article, e)
            return None
        if not items:
            return None
        views = [x["views"] for x in items]
        total = sum(views)
        if len(views) >= 6:
            third = max(1, len(views) // 3)
            recent = sum(views[-third:]) / third
            prior = sum(views[-2 * third:-third]) / third
            growth = round((recent - prior) / prior * 100, 1) if prior > 0 else None
        else:
            growth = None
        return {"total_7d": total, "growth_pct": growth, "days": len(views)}

    def close(self):
        self._client.close()
