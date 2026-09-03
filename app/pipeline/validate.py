"""Data validation. Invalid or incomplete data is either repaired with real
values, flagged as non-verified, or dropped — never displayed as verified."""
import logging
from datetime import datetime

from ..providers.base import TitleData

log = logging.getLogger(__name__)


def validate_title(t: TitleData) -> TitleData | None:
    """Return a validated TitleData, or None if it must be dropped."""
    problems = []

    if not t.title or not t.title.strip():
        problems.append("missing title")
    if t.type not in ("movie", "series"):
        problems.append(f"invalid type {t.type!r}")
    if not t.provider_id:
        problems.append("missing provider id")
    if t.release_date:
        try:
            datetime.strptime(t.release_date, "%Y-%m-%d")
        except ValueError:
            log.warning("Invalid release_date %r for %s", t.release_date, t.title)
            t.release_date = None          # invalid -> unavailable, not fabricated
    if t.rating is not None and not (0 <= t.rating <= 10):
        log.warning("Out-of-range rating %s for %s", t.rating, t.title)
        t.rating = None
    if t.popularity is not None and t.popularity < 0:
        t.popularity = None

    if problems:
        log.warning("Dropping invalid title (%s): %s", ", ".join(problems), t)
        return None
    return t


def deduplicate(titles: list[TitleData]) -> list[TitleData]:
    """Duplicate detection: keep the first occurrence per (provider, id, type)."""
    seen, out = set(), []
    for t in titles:
        key = (t.provider, t.provider_id, t.type)
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out
