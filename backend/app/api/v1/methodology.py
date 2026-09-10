"""Due-diligence surface: what this platform measures, and how it avoids
flattering itself.

Everything here is either computed live from the production database or
transcribed from a committed experiment with its source directory attached.
Nothing is a projection or a target. Where the strategy under test failed, this
endpoint reports the failure — that is the point of it.

Cached, because it is a public read path and one of its inputs walks the whole
price history. See core/cache.py for what a Redis outage degrades to.
"""

from datetime import date as date_type

from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.cache import get_methodology_cache, set_methodology_cache
from app.core.database import get_db
from app.core.daily_signals_config import MAX_SIGNALS
from app.core.research_record import LIVE_ARM, PHASES, PROTOCOL
from app.core.v1_strategy import V1, V1_DESCRIPTION, V1_VERSION
from app.ml.predict import MIN_SERVABLE_ROC_AUC
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.services.price_integrity import (
    CORPORATE_ACTION_RATIOS,
    MIN_MOVE_PCT,
    RATIO_TOLERANCE,
)

router = APIRouter(prefix="/methodology", tags=["methodology"])

CACHE_KEY = "v1"


def _universe(db: Session) -> dict:
    active = db.query(func.count(Stock.id)).filter(Stock.is_active == True).scalar() or 0  # noqa: E712
    inactive = db.query(func.count(Stock.id)).filter(Stock.is_active == False).scalar() or 0  # noqa: E712
    span = db.execute(text(
        "SELECT min(date) AS a, max(date) AS b, count(*) AS n FROM price_history"
    )).one()
    return {
        "active": active,
        # Retained deliberately: excluding them from a backtest is survivorship
        # bias, and this is the number that quantifies the exposure.
        "inactive_retained": inactive,
        "bars": span.n,
        "first_bar": str(span.a) if span.a else None,
        "last_bar": str(span.b) if span.b else None,
    }


def _integrity(db: Session) -> dict:
    """Corporate-action discontinuities still present in the stored series.

    One window-function pass, not a per-stock loop — this is a public endpoint.
    """
    rows = db.execute(text("""
        WITH ranked AS (
          SELECT ph.stock_id, s.symbol, ph.date, ph.close,
                 LAG(ph.close) OVER (PARTITION BY ph.stock_id ORDER BY ph.date) AS prev_close
          FROM price_history ph JOIN stocks s ON s.id = ph.stock_id
          WHERE s.is_active = true AND ph.close IS NOT NULL AND ph.close > 0
        )
        SELECT symbol, date, prev_close, close
        FROM ranked
        WHERE prev_close IS NOT NULL
          AND (close / prev_close < :lo OR close / prev_close > :hi)
    """), {"lo": 1 - MIN_MOVE_PCT / 100, "hi": 1 + MIN_MOVE_PCT / 100}).fetchall()

    flagged = []
    for r in rows:
        ratio = float(r.prev_close) / float(r.close)
        for nominal in CORPORATE_ACTION_RATIOS:
            for candidate in (ratio, 1 / ratio):
                if abs(candidate - nominal) / nominal <= RATIO_TOLERANCE:
                    flagged.append({
                        "symbol": r.symbol, "date": str(r.date),
                        "ratio": round(ratio, 4), "matched": nominal,
                    })
                    break
            else:
                continue
            break

    return {
        "large_moves_scanned": len(rows),
        "corporate_action_shaped": len(flagged),
        # Named, because a stock the strategy cannot see is otherwise
        # indistinguishable from one that ranked badly.
        "detail": sorted(flagged, key=lambda f: f["date"], reverse=True)[:12],
    }


def _ml_gate() -> dict:
    """The gate is the point: a model that cannot beat noise is not served."""
    import json
    from pathlib import Path

    meta_path = Path(__file__).resolve().parents[3] / "app" / "ml" / "artifacts" / "latest.json"
    measured, selected, trained = None, None, None
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        selected = meta.get("selected_model")
        trained = meta.get("trained_at")
        measured = ((meta.get(selected) or {}).get("test") or {}).get("roc_auc")
    return {
        "serving_threshold_roc_auc": MIN_SERVABLE_ROC_AUC,
        "selected_model": selected,
        "measured_test_roc_auc": measured,
        "trained_at": trained,
        "is_serving": bool(measured is not None and measured >= MIN_SERVABLE_ROC_AUC),
    }


@router.get("")
def methodology(db: Session = Depends(get_db)) -> dict:
    cached = get_methodology_cache(CACHE_KEY)
    if cached is not None:
        return cached

    from app.services.forward_testing import compute_track_record

    payload = {
        "generated_at": date_type.today().isoformat(),
        "strategy": {
            "version": V1_VERSION,
            "description": V1_DESCRIPTION,
            "frozen": True,
            "max_concurrent_positions": MAX_SIGNALS,
            "params": {
                "ranking": V1.ranking_engine,
                "momentum_long_days": V1.momentum_long_days,
                "momentum_skip_days": V1.momentum_skip_days,
                "sizing": V1.sizing_method,
                "atr_stop_multiplier": V1.atr_stop_multiplier,
                "use_support_stop": V1.use_support_stop,
                "regime_ma_days": V1.regime_ma_days,
                "bull_exposure": V1.bull_exposure,
                "bear_exposure": V1.bear_exposure,
                "rebalance_frequency": V1.rebalance_frequency,
                "horizon_days": V1.horizon_days,
                "trend_confirm_days": V1.trend_confirm_days,
            },
        },
        "protocol": PROTOCOL,
        "universe": _universe(db),
        "integrity": _integrity(db),
        "ml_gate": _ml_gate(),
        "live_arm": LIVE_ARM,
        "phases": [
            {"phase": p.phase, "question": p.question, "verdict": p.verdict,
             "detail": p.detail, "source": p.source}
            for p in PHASES
        ],
        "track_record": compute_track_record(db),
    }
    set_methodology_cache(CACHE_KEY, payload)
    return payload
