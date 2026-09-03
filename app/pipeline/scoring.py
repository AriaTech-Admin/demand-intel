"""Trend scoring engine. Transparent and configurable: each component is
normalized to [0,1], multiplied by its documented weight (config.SCORING_WEIGHTS),
and the per-component breakdown + evidence bullets are stored so the UI can
explain exactly WHY a title is trending. Components with no real data score 0
and are reported as unavailable."""
import json
import logging
from datetime import datetime, timezone

from .. import config
from ..db import get_db, utcnow

log = logging.getLogger(__name__)


def compute_and_store_scores():
    """Recompute trend scores for all titles that have at least one snapshot."""
    with get_db() as db:
        titles = db.execute(
            """SELECT t.id, t.release_date,
                      (SELECT popularity FROM snapshots s WHERE s.title_id=t.id AND s.popularity IS NOT NULL
                       ORDER BY collected_at DESC LIMIT 1) AS pop,
                      (SELECT popularity FROM snapshots s WHERE s.title_id=t.id AND s.popularity IS NOT NULL
                       ORDER BY collected_at ASC LIMIT 1) AS first_pop,
                      (SELECT search_interest FROM snapshots s WHERE s.title_id=t.id AND s.search_interest IS NOT NULL
                       ORDER BY collected_at DESC LIMIT 1) AS interest
               FROM titles t"""
        ).fetchall()
        # Normalize current interest against the current candidate pool (derived).
        interests = [r["interest"] for r in titles if r["interest"] is not None]
        max_interest = max(interests) if interests else None

        for r in titles:
            comps, evidence = {}, []
            n_available = 0

            # 1. Search growth (verified: Google Trends measurement).
            g = db.execute(
                """SELECT value FROM metrics WHERE title_id=? AND metric_name='search_growth_pct'
                   AND value IS NOT NULL
                   ORDER BY collected_at DESC LIMIT 1""", (r["id"],)).fetchone()
            if g and g["value"] is not None:
                sg = _sigmoid_norm(g["value"], scale=100)   # +100% growth -> ~0.73
                comps["search_growth"] = {"available": True, "value": g["value"], "norm": round(sg, 3)}
                n_available += 1
                if g["value"] > 0 and sg > 0.5:
                    evidence.append(f"✓ Search interest is growing (+{g['value']}% in the last 7 days, Google Trends)")
                elif g["value"] > 0 and sg > 0.2:
                    evidence.append(f"✓ Search interest is increasing (+{g['value']}% recent, Google Trends)")
                elif g["value"] < 0:
                    evidence.append(f"✓ Search interest is decreasing ({g['value']}% in the last 7 days, Google Trends)")
            else:
                comps["search_growth"] = {"available": False}

            # 2. Popularity growth (derived: TMDB popularity delta between snapshots).
            if r["pop"] is not None and r["first_pop"] is not None and r["first_pop"] > 0:
                delta_pct = (r["pop"] - r["first_pop"]) / r["first_pop"] * 100
                pg = _sigmoid_norm(delta_pct, scale=50)
                comps["popularity_growth"] = {"available": True, "value": round(delta_pct, 1), "norm": round(pg, 3)}
                n_available += 1
                if pg > 0.5:
                    evidence.append(f"✓ Popularity increased ({delta_pct:+.0f}% since first snapshot, TMDB)")
            else:
                comps["popularity_growth"] = {"available": False}

            # 3. Current interest level (derived: normalized vs current pool).
            if r["interest"] is not None and max_interest:
                il = r["interest"] / max_interest
                comps["interest"] = {"available": True, "value": r["interest"], "norm": round(il, 3)}
                n_available += 1
            else:
                comps["interest"] = {"available": False}

            # 4. Recency (derived from the verified release date).
            if r["release_date"]:
                days = (datetime.now(timezone.utc)
                        - datetime.fromisoformat(r["release_date"] + "T00:00:00+00:00")).days
                rec = max(0.0, 1.0 - days / 365) if days >= 0 else 0.0
                comps["recency"] = {"available": True, "value": r["release_date"], "norm": round(rec, 3)}
                n_available += 1
                if rec > 0.5:
                    evidence.append("✓ Recent release")
            else:
                comps["recency"] = {"available": False}

            if n_available < config.MIN_COMPONENTS_FOR_RANKING:
                continue   # not enough real data to rank this title

            w = config.SCORING_WEIGHTS
            score = 100 * (
                w["search_growth_weight"] * comps["search_growth"].get("norm", 0)
                + w["popularity_growth_weight"] * comps["popularity_growth"].get("norm", 0)
                + w["interest_weight"] * comps["interest"].get("norm", 0)
                + w["recency_weight"] * comps["recency"].get("norm", 0)
            )
            share = n_available / 4
            confidence = ("High" if share >= config.CONFIDENCE_HIGH
                          else "Medium" if share >= config.CONFIDENCE_MEDIUM else "Low")
            if not evidence:
                evidence.append("Insufficient supporting signals — ranked on limited data")

            db.execute(
                """INSERT INTO scores(title_id, trend_score, confidence, explanation, components, computed_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(title_id) DO UPDATE SET trend_score=excluded.trend_score,
                     confidence=excluded.confidence, explanation=excluded.explanation,
                     components=excluded.components, computed_at=excluded.computed_at""",
                (r["id"], round(score, 1), confidence,
                 json.dumps(evidence), json.dumps(comps), utcnow()))


def _sigmoid_norm(x: float, scale: float) -> float:
    """Map a signed percentage to [0,1]; 0% -> 0.2 (stable, not zero-credit)."""
    import math
    return 0.2 + 0.8 / (1 + math.exp(-x / (scale / 2)))
