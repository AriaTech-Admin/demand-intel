"""Gemini AI enrichment provider (generativelanguage.googleapis.com).
Uses GEMINI_API_KEY (gemini-3.6-flash) to generate derived insights where
verified metrics are unavailable. Never fabricates verified metrics - insights
are stored as derived with provenance source=Gemini and clearly labeled in UI."""
import json
import logging
import time

import httpx

from .. import config

log = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-3.6-flash"
CACHE_TTL = 24 * 3600  # 24h
# In-memory cache to avoid re-calling during same process; persisted via DB ai_insights table
_mem_cache: dict[int, tuple[float, dict]] = {}

def _available() -> bool:
    return bool(config.GEMINI_API_KEY)

def generate_insight(title: str, type_: str, genres: list, overview: str | None, rating: float | None,
                     search_interest: float | None, search_growth: float | None,
                     popularity: float | None, trend_score: float | None) -> dict | None:
    """Call Gemini to generate enriched insight. Returns dict or None on failure."""
    if not _available():
        log.warning("GEMINI_API_KEY not configured - AI insights unavailable")
        return None

    prompt = f"""You are a content-demand analyst for movies/TV. Given verified data, generate a concise JSON insight.
Return STRICT JSON only, no markdown, with keys: summary (1 sentence, max 25 words), why_trending_ai (1 sentence explaining momentum), demand_proxy (0-100 integer estimating viewer interest if search data missing, else null), tags (2-4 keywords), recommendation (1 sentence for acquisition).

Verified data:
Title: {title}
Type: {type_}
Genres: {', '.join(genres) if genres else 'unknown'}
Overview: {overview or "N/A"}
TMDB rating: {rating if rating is not None else "N/A"}/10
Search interest (0-100 Google Trends): {search_interest if search_interest is not None else "Data unavailable"}
Search growth % (7d): {search_growth if search_growth is not None else "Data unavailable"}
TMDB popularity: {popularity if popularity is not None else "N/A"}
Trend score: {trend_score if trend_score is not None else "N/A"}

Rules: Do NOT invent verified metrics. If search data is unavailable, set demand_proxy based on popularity/recency/rating but label as derived. Keep tone factual.
"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1024}}
    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(url, json=body)
            r.raise_for_status()
            j = r.json()
            text = j["candidates"][0]["content"]["parts"][0]["text"]
            # Strip markdown fences if present
            text = text.strip()
            if text.startswith("```"):
                text = text.strip("`").strip()
                if text.lstrip().startswith("json"):
                    text = text.lstrip()[4:].strip()
            # If truncated, try to close JSON
            if not text.endswith("}"):
                # Try to extract up to last complete field
                last_brace = text.rfind("}")
                if last_brace != -1:
                    text = text[:last_brace+1]
            try:
                data = json.loads(text)
            except Exception as je:
                # Try to extract JSON object with regex
                import re
                m = re.search(r"\{.*\}", text, re.S)
                if m:
                    try:
                        data = json.loads(m.group(0))
                    except:
                        log.warning("Gemini JSON parse failed for %r: %s raw=%r", title, je, text[:500])
                        # Fallback derived insight without AI
                        return {
                            "summary": f"{title} is a {type_} with {', '.join(genres) if genres else 'unknown'} appeal.",
                            "why_trending_ai": "Trending based on verified TMDB popularity and recency where search data is unavailable.",
                            "demand_proxy": int(min(100, max(0, (popularity or 0)/10 + (rating or 0)*5))) if popularity else None,
                            "tags": genres[:3] if genres else [],
                            "recommendation": "Monitor search demand as it becomes available.",
                            "model": GEMINI_MODEL,
                            "source": "Gemini-fallback",
                            "quality": "derived",
                        }
                else:
                    log.warning("Gemini JSON parse failed for %r: %s raw=%r", title, je, text[:500])
                    return {
                        "summary": f"{title} is a {type_} with {', '.join(genres) if genres else 'unknown'} appeal.",
                        "why_trending_ai": "Trending based on verified TMDB popularity and recency where search data is unavailable.",
                        "demand_proxy": int(min(100, max(0, (popularity or 0)/10 + (rating or 0)*5))) if popularity else None,
                        "tags": genres[:3] if genres else [],
                        "recommendation": "Monitor search demand as it becomes available.",
                        "model": GEMINI_MODEL,
                        "source": "Gemini-fallback",
                        "quality": "derived",
                    }
            out = {
                "summary": str(data.get("summary",""))[:300],
                "why_trending_ai": str(data.get("why_trending_ai",""))[:300],
                "demand_proxy": data.get("demand_proxy"),
                "tags": [str(x) for x in (data.get("tags") or [])][:4],
                "recommendation": str(data.get("recommendation",""))[:300],
                "model": GEMINI_MODEL,
                "source": "Gemini",
                "quality": "derived",
            }
            return out
    except Exception as e:
        log.warning("Gemini generate failed for %r: %s", title, e)
        # Fallback derived without AI
        try:
            return {
                "summary": f"{title} is a {type_} with {', '.join(genres) if genres else 'unknown'} appeal.",
                "why_trending_ai": "Trending based on verified metrics.",
                "demand_proxy": int(min(100, max(0, (popularity or 0)/10 + (rating or 0)*5))) if popularity else None,
                "tags": genres[:3] if genres else [],
                "recommendation": "Monitor search demand as it becomes available.",
                "model": GEMINI_MODEL,
                "source": "Gemini-fallback",
                "quality": "derived",
            }
        except:
            return None

def get_cached_insight(title_id: int) -> dict | None:
    """Check in-memory cache TTL."""
    entry = _mem_cache.get(title_id)
    if entry and time.time() - entry[0] < CACHE_TTL:
        return entry[1]
    return None

def set_cached_insight(title_id: int, data: dict):
    _mem_cache[title_id] = (time.time(), data)
