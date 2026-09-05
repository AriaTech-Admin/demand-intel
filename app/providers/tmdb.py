"""TMDB metadata provider (https://developer.themoviedb.org).
Popularity is TMDB's own metric, reported as-is; we never invent values.
Requires TMDB_API_KEY — without it the provider reports unavailable."""
import asyncio

import httpx

from .. import config
from .base import MetadataProvider, TitleData


class TMDBProvider(MetadataProvider):
    name = "TMDB"

    @property
    def available(self) -> bool:
        return bool(config.TMDB_API_KEY)

    async def get_trending(self, media_type: str) -> list[TitleData]:
        """media_type: 'movie' | 'tv'. Returns current TMDB trending list."""
        if not self.available:
            return []
        url = f"{config.TMDB_BASE_URL}/trending/{media_type}/{config.TMDB_TRENDING_WINDOW}"
        results = []
        async with httpx.AsyncClient(timeout=30) as client:
            page = 1
            while len(results) < config.TMDB_MAX_TITLES:
                params = {"api_key": config.TMDB_API_KEY, "page": page}
                resp = await _get_with_retry(client, url, params)
                if resp is None or resp.status_code != 200:
                    break
                batch = resp.json().get("results", [])
                if not batch:
                    break
                results.extend(batch)
                if len(batch) < 20:
                    break
                page += 1
            results = results[: config.TMDB_MAX_TITLES]
            titles = [_normalize(r, media_type) for r in results]
            # Fetch credits for top titles (cast/directors), tolerating failures.
            for t in titles:
                await _enrich(client, t)
            return titles


async def _get_with_retry(client, url, params, retries=3):
    for attempt in range(retries):
        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 429:            # rate limited: backoff
                await asyncio.sleep(2 ** attempt)
                continue
            return resp
        except httpx.HTTPError:
            await asyncio.sleep(1 + attempt)
    return None


def _normalize(r: dict, media_type: str) -> TitleData:
    is_tv = media_type == "tv"
    date = r.get("release_date") if not is_tv else r.get("first_air_date")
    return TitleData(
        provider="tmdb",
        provider_id=str(r.get("id")),
        title=r.get("title") or r.get("name") or "",
        type="series" if is_tv else "movie",
        genres=[],  # filled from genre map below
        release_date=date or None,
        overview=r.get("overview") or None,
        poster_url=(config.TMDB_IMAGE_BASE + r["poster_path"]) if r.get("poster_path") else None,
        backdrop_url=(config.TMDB_IMAGE_BASE + r["backdrop_path"]) if r.get("backdrop_path") else None,
        rating=r.get("vote_average") if r.get("vote_count", 0) > 0 else None,
        popularity=r.get("popularity"),
        collected_at=None,
    )


async def _enrich(client, t: TitleData):
    """Genre names, seasons/episodes, cast, directors - best effort."""
    kind = "tv" if t.type == "series" else "movie"
    params = {
        "api_key": config.TMDB_API_KEY,
        "append_to_response": "credits,external_ids",
    }
    resp = await _get_with_retry(client, f"{config.TMDB_BASE_URL}/{kind}/{t.provider_id}", params)
    if resp is None or resp.status_code != 200:
        return
    d = resp.json()
    t.genres = [g.get("name", "") for g in d.get("genres", [])]
    if t.type == "series":
        t.seasons = d.get("number_of_seasons")
        t.episodes = d.get("number_of_episodes")
    credits = d.get("credits") or {}
    t.cast = [c.get("name", "") for c in (credits.get("cast") or [])[:6]]
    crew = credits.get("crew") or []
    t.directors = sorted({c.get("name", "") for c in crew
                          if c.get("job") in ("Director", "Creator")})[:4]
    if not t.directors and d.get("created_by"):
        t.directors = [c.get("name", "") for c in d.get("created_by")[:3]]
    # Cross-provider linkage: official IMDb id, used by the IMDb datasets provider.
    ext_ids = d.get("external_ids") or {}
    if ext_ids.get("imdb_id"):
        t.imdb_id = ext_ids.get("imdb_id")
    # Region availability: subscription (flatrate) providers per country.
    wp = await _get_with_retry(client, f"{config.TMDB_BASE_URL}/{kind}/{t.provider_id}/watch/providers", params)
    if wp is not None and wp.status_code == 200:
        for code, entry in (wp.json().get("results") or {}).items():
            flat = entry.get("flatrate") or []
            names = sorted({p.get("provider_name", "") for p in flat if p.get("provider_name")})
            if names:
                t.watch_providers[code] = names
