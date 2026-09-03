"""Provider contract. Any data source implements this interface so providers
can be added/replaced without touching the pipeline or UI."""
from dataclasses import dataclass, field


@dataclass
class TitleData:
    """Normalized metadata for one movie/series."""
    provider: str
    provider_id: str
    title: str
    type: str                      # 'movie' | 'series'
    imdb_id: str | None = None       # cross-provider linkage (from TMDB external_ids)
    genres: list = field(default_factory=list)
    release_date: str | None = None
    overview: str | None = None
    poster_url: str | None = None
    backdrop_url: str | None = None
    rating: float | None = None
    cast: list = field(default_factory=list)
    directors: list = field(default_factory=list)
    seasons: int | None = None
    episodes: int | None = None
    # Current-level metrics reported by this provider (never invented):
    popularity: float | None = None   # provider popularity level (source-defined scale)
    collected_at: str | None = None


@dataclass
class SearchSignal:
    """A search-demand measurement for one title."""
    metric_name: str      # e.g. 'search_interest' | 'search_growth_pct'
    value: float | None   # None => source could not provide; must NOT be faked
    source: str
    region: str
    period: str
    collected_at: str


class MetadataProvider:
    """Discovers titles and returns normalized metadata."""
    name = "abstract"

    def get_trending(self, media_type: str) -> list[TitleData]:
        raise NotImplementedError


class SearchDemandProvider:
    """Measures search interest / growth for a title name."""
    name = "abstract"

    def get_signals(self, title: str, geo: str = "", period: str = "now 7-d") -> list[SearchSignal]:
        raise NotImplementedError
