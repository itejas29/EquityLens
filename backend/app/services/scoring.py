"""Scoring engine: four 0-100 sub-scores (technical, fundamental, valuation,
risk) combined into a weighted composite with a signal label.

NORMALIZATION CHOICE (documented once here, per-metric notes below):
- Peer-relative metrics (fundamentals, valuation, most of risk) use a
  percentile rank against the current active universe (or sector, for the
  two sector-relative valuation metrics) — there's no natural fixed scale
  for "is 22% revenue growth good", only "good relative to peers right now".
- Bounded/self-referential indicators (RSI, MACD sign/slope, price-vs-DMA,
  golden cross, volume ratio, beta) use a fixed-band mapping instead — RSI
  is already a 0-100 oscillator with textbook overbought/oversold zones,
  and the technical metrics all have a natural fixed reference point (DMA
  crossing itself, beta=1 as market-average risk) rather than needing peer
  comparison.

MISSING DATA: a metric that's NULL is dropped from its sub-score and the
remaining weights renormalize; if more than half a sub-score's inputs are
missing, the whole sub-score goes NULL and the composite renormalizes across
whatever sub-scores are available. Nothing is ever backfilled with a default.
"""

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.core.cache import get_scored_universe_cache, set_scored_universe_cache
from app.core.scoring_config import (
    COMPOSITE_WEIGHTS,
    FUNDAMENTAL_WEIGHTS,
    RISK_WEIGHTS,
    SIGNAL_THRESHOLDS,
    SUBSCORE_NULL_THRESHOLD,
    TECHNICAL_WEIGHTS,
    VALUATION_WEIGHTS,
)
from app.models.fundamentals import Fundamentals
from app.models.indicator import Indicator
from app.models.price_history import PriceHistory
from app.models.stock import Stock

LIQUIDITY_WINDOW = 20


@dataclass
class StockScore:
    stock_id: int
    symbol: str
    technical_score: float | None
    fundamental_score: float | None
    valuation_score: float | None
    risk_score: float | None
    overall_score: float | None
    signal: str | None
    missing_inputs: dict[str, list[str]] = field(default_factory=dict)


def _to_float(value) -> float | None:
    return None if value is None else float(value)


def percentile_score(value: float | None, population: pd.Series, higher_is_better: bool) -> float | None:
    """Percentile rank of `value` within `population`, scaled 0-100.

    A population of 0 or 1 non-null members means there's no real peer group
    to rank against, so this returns a neutral 50 rather than an
    artificially extreme 0/100.
    """
    if value is None or pd.isna(value):
        return None

    clean = population.dropna()
    if len(clean) <= 1:
        return 50.0

    ref = clean if higher_is_better else -clean
    v = value if higher_is_better else -value
    percentile = (ref <= v).sum() / len(ref) * 100
    return float(percentile)


def _weighted_subscore(metric_scores: dict[str, float | None], weights: dict[str, float]) -> tuple[float | None, list[str]]:
    missing = [name for name, score in metric_scores.items() if score is None]
    available = {name: score for name, score in metric_scores.items() if score is not None}

    if len(missing) > len(weights) * SUBSCORE_NULL_THRESHOLD or not available:
        return None, missing

    total_weight = sum(weights[name] for name in available)
    weighted = sum(weights[name] * score for name, score in available.items()) / total_weight
    return round(weighted, 2), missing


def _composite(sub_scores: dict[str, float | None], weights: dict[str, float] | None = None) -> float | None:
    weights = weights if weights is not None else COMPOSITE_WEIGHTS
    available = {name: score for name, score in sub_scores.items() if score is not None}
    if not available:
        return None
    total_weight = sum(weights[name] for name in available)
    weighted = sum(weights[name] * score for name, score in available.items()) / total_weight
    return round(weighted, 2)


def _map_signal(overall_score: float | None) -> str | None:
    if overall_score is None:
        return None
    for threshold, label in SIGNAL_THRESHOLDS:
        if overall_score >= threshold:
            return label
    return SIGNAL_THRESHOLDS[-1][1]


# --- Technical metrics (fixed-band / self-referential) ----------------------


def _missing(value) -> bool:
    """True for None or NaN. Values pulled out of a pandas row (rather than
    straight off an ORM object) come back as float('nan') for anything that
    was None going in — pandas coerces None to NaN in float64 columns — so
    every metric function here must treat the two as equivalent."""
    return value is None or (isinstance(value, float) and np.isnan(value))


def _price_vs_dma50_score(close: float | None, dma_50: float | None) -> float | None:
    """+/-10% from the 50-DMA maps linearly to the full 0-100 range."""
    if _missing(close) or _missing(dma_50) or dma_50 == 0:
        return None
    pct_diff = (close - dma_50) / dma_50
    return float(np.clip(50 + pct_diff * 500, 0, 100))


def _golden_cross_score(dma_50: float | None, dma_200: float | None) -> float | None:
    """Same +/-10% linear band, applied to the 50-DMA vs 200-DMA gap."""
    if _missing(dma_50) or _missing(dma_200) or dma_200 == 0:
        return None
    pct_diff = (dma_50 - dma_200) / dma_200
    return float(np.clip(50 + pct_diff * 500, 0, 100))


def _rsi_band_score(rsi: float | None) -> float | None:
    """Fixed control points: <30 or >70 penalized toward 0, 40-60 rewarded at 100."""
    if _missing(rsi):
        return None
    rsi_clamped = float(np.clip(rsi, 0, 100))
    control_x = [0, 30, 40, 60, 70, 100]
    control_y = [0, 40, 100, 100, 40, 0]
    return float(np.interp(rsi_clamped, control_x, control_y))


def _macd_momentum_score(macd_hist: float | None, macd_hist_prev: float | None) -> float | None:
    """Sign of the histogram sets the base (+/-25 from neutral 50); its
    day-over-day slope adds/subtracts another +/-12.5 for strengthening vs
    weakening momentum. Slope term is skipped (not zeroed) if there's no
    prior-day value to compare against."""
    if _missing(macd_hist):
        return None
    score = 50.0
    if macd_hist > 0:
        score += 25
    elif macd_hist < 0:
        score -= 25

    if not _missing(macd_hist_prev):
        slope = macd_hist - macd_hist_prev
        if slope > 0:
            score += 12.5
        elif slope < 0:
            score -= 12.5

    return float(np.clip(score, 0, 100))


def _volume_confirmation_score(volume_ratio: float | None) -> float | None:
    """volume_ratio=1 (average participation) is neutral 50; 2x average
    saturates at 100, near-zero volume saturates at 0."""
    if _missing(volume_ratio):
        return None
    return float(np.clip(50 + (volume_ratio - 1) * 50, 0, 100))


def _beta_score(beta: float | None) -> float | None:
    """Fixed anchor at beta=1 (market-average risk) = 80. Below 1 rewards up
    to 100 as beta approaches 0; above 1 penalizes steeply toward 0 by
    beta=3. Not a percentile because the reference point (market beta = 1)
    is fixed, not peer-relative."""
    if _missing(beta):
        return None
    if beta <= 1:
        score = 80 + (1 - beta) * 20
    else:
        score = 80 - (beta - 1) * 40
    return float(np.clip(score, 0, 100))


# --- Universe data loading ---------------------------------------------------


def _load_fundamentals_frame(db: Session, stocks: list[Stock]) -> pd.DataFrame:
    rows = []
    for stock in stocks:
        f = db.query(Fundamentals).filter(Fundamentals.stock_id == stock.id).order_by(Fundamentals.as_of_date.desc()).first()
        rows.append(
            {
                "stock_id": stock.id,
                "sector": stock.sector,
                "pe_ratio": _to_float(f.pe_ratio) if f else None,
                "pb_ratio": _to_float(f.pb_ratio) if f else None,
                "roe": _to_float(f.roe) if f else None,
                "roce": _to_float(f.roce) if f else None,
                "debt_to_equity": _to_float(f.debt_to_equity) if f else None,
                "revenue_growth": _to_float(f.revenue_growth) if f else None,
                "eps_growth": _to_float(f.eps_growth) if f else None,
                "profit_growth": _to_float(f.profit_growth) if f else None,
                "operating_margin": _to_float(f.operating_margin) if f else None,
            }
        )
    df = pd.DataFrame(rows).set_index("stock_id")

    # PEG-style growth-adjusted P/E; undefined (None) for non-positive PE or
    # non-positive growth rather than producing a meaningless ratio.
    def _peg(row) -> float | None:
        if pd.isna(row.pe_ratio) or pd.isna(row.eps_growth):
            return None
        if row.pe_ratio <= 0 or row.eps_growth <= 0:
            return None
        return row.pe_ratio / (row.eps_growth * 100)

    df["peg"] = df.apply(_peg, axis=1)
    return df


def _load_own_pe_history(db: Session, stock_id: int) -> pd.Series:
    rows = db.query(Fundamentals.pe_ratio).filter(Fundamentals.stock_id == stock_id).all()
    values = [_to_float(r[0]) for r in rows if r[0] is not None]
    return pd.Series(values, dtype=float)


def _load_indicators_frame(db: Session, stocks: list[Stock]) -> pd.DataFrame:
    rows = []
    for stock in stocks:
        last_two = (
            db.query(Indicator).filter(Indicator.stock_id == stock.id).order_by(Indicator.date.desc()).limit(2).all()
        )
        latest = last_two[0] if last_two else None
        prev = last_two[1] if len(last_two) > 1 else None

        latest_price = (
            db.query(PriceHistory).filter(PriceHistory.stock_id == stock.id).order_by(PriceHistory.date.desc()).first()
        )
        recent = (
            db.query(PriceHistory)
            .filter(PriceHistory.stock_id == stock.id)
            .order_by(PriceHistory.date.desc())
            .limit(LIQUIDITY_WINDOW)
            .all()
        )
        traded_values = [
            float(r.close) * float(r.volume) for r in recent if r.close is not None and r.volume is not None
        ]
        liquidity = float(np.mean(traded_values)) if traded_values else None

        rows.append(
            {
                "stock_id": stock.id,
                "close": _to_float(latest_price.close) if latest_price else None,
                "dma_50": _to_float(latest.dma_50) if latest else None,
                "dma_200": _to_float(latest.dma_200) if latest else None,
                "rsi_14": _to_float(latest.rsi_14) if latest else None,
                "macd_hist": _to_float(latest.macd_hist) if latest else None,
                "macd_hist_prev": _to_float(prev.macd_hist) if prev else None,
                "volume_ratio": _to_float(latest.volume_ratio) if latest else None,
                "volatility": _to_float(latest.volatility) if latest else None,
                "beta": _to_float(latest.beta) if latest else None,
                "max_drawdown": _to_float(latest.max_drawdown) if latest else None,
                "liquidity": liquidity,
            }
        )
    return pd.DataFrame(rows).set_index("stock_id")


# --- Orchestration ------------------------------------------------------------


def score_universe(db: Session) -> list[StockScore]:
    """Score every active stock in one pass so percentile ranks are computed
    against a single, consistent population — scoring one stock still needs
    the whole universe as context, so there's no cheaper single-stock path."""
    stocks = db.query(Stock).filter(Stock.is_active == True).order_by(Stock.symbol).all()  # noqa: E712
    if not stocks:
        return []

    fdf = _load_fundamentals_frame(db, stocks)
    idf = _load_indicators_frame(db, stocks)

    results: list[StockScore] = []
    for stock in stocks:
        frow = fdf.loc[stock.id]
        irow = idf.loc[stock.id]
        sector = frow["sector"]
        sector_peers = fdf[fdf["sector"] == sector] if sector else fdf.iloc[0:0]

        technical_metrics = {
            "price_vs_dma50": _price_vs_dma50_score(irow.close, irow.dma_50),
            "golden_cross": _golden_cross_score(irow.dma_50, irow.dma_200),
            "rsi_band": _rsi_band_score(irow.rsi_14),
            "macd_momentum": _macd_momentum_score(irow.macd_hist, irow.macd_hist_prev),
            "volume_confirmation": _volume_confirmation_score(irow.volume_ratio),
        }
        technical_score, technical_missing = _weighted_subscore(technical_metrics, TECHNICAL_WEIGHTS)

        fundamental_metrics = {
            "revenue_growth": percentile_score(frow.revenue_growth, fdf["revenue_growth"], True),
            "eps_growth": percentile_score(frow.eps_growth, fdf["eps_growth"], True),
            "profit_growth": percentile_score(frow.profit_growth, fdf["profit_growth"], True),
            "roe": percentile_score(frow.roe, fdf["roe"], True),
            "roce": percentile_score(frow.roce, fdf["roce"], True),
            "operating_margin": percentile_score(frow.operating_margin, fdf["operating_margin"], True),
            "debt_to_equity": percentile_score(frow.debt_to_equity, fdf["debt_to_equity"], False),
        }
        fundamental_score, fundamental_missing = _weighted_subscore(fundamental_metrics, FUNDAMENTAL_WEIGHTS)

        own_pe_history = _load_own_pe_history(db, stock.id)
        # Own trailing P/E range needs >= 2 historical fundamentals snapshots
        # for the stock; with only one refresh ever run, this stays missing
        # until a second snapshot exists (never approximated from one point).
        pe_vs_own_range = (
            percentile_score(frow.pe_ratio, own_pe_history, False) if len(own_pe_history) >= 2 else None
        )
        valuation_metrics = {
            "pe_vs_sector": percentile_score(frow.pe_ratio, sector_peers["pe_ratio"], False),
            "pe_vs_own_range": pe_vs_own_range,
            "pb_vs_sector": percentile_score(frow.pb_ratio, sector_peers["pb_ratio"], False),
            "growth_adjusted_pe": percentile_score(frow.peg, fdf["peg"], False),
        }
        valuation_score, valuation_missing = _weighted_subscore(valuation_metrics, VALUATION_WEIGHTS)

        risk_metrics = {
            "volatility": percentile_score(irow.volatility, idf["volatility"], False),
            "beta": _beta_score(irow.beta),
            "max_drawdown": percentile_score(irow.max_drawdown, idf["max_drawdown"], True),
            "liquidity": percentile_score(irow.liquidity, idf["liquidity"], True),
        }
        risk_score, risk_missing = _weighted_subscore(risk_metrics, RISK_WEIGHTS)

        sub_scores = {
            "technical": technical_score,
            "fundamental": fundamental_score,
            "valuation": valuation_score,
            "risk": risk_score,
        }
        overall_score = _composite(sub_scores)

        results.append(
            StockScore(
                stock_id=stock.id,
                symbol=stock.symbol,
                technical_score=technical_score,
                fundamental_score=fundamental_score,
                valuation_score=valuation_score,
                risk_score=risk_score,
                overall_score=overall_score,
                signal=_map_signal(overall_score),
                missing_inputs={
                    "technical": technical_missing,
                    "fundamental": fundamental_missing,
                    "valuation": valuation_missing,
                    "risk": risk_missing,
                },
            )
        )

    return results


def get_cached_scored_universe(db: Session) -> list[StockScore]:
    """Cached wrapper around score_universe (1h TTL) — for read paths that
    don't need up-to-the-second scores (single-stock scoring, portfolio
    analysis). POST /scoring/run-universe bypasses this and calls
    score_universe directly, then invalidates the cache so subsequent reads
    pick up the fresh run rather than serving a stale copy."""
    cached = get_scored_universe_cache()
    if cached is not None:
        return [StockScore(**row) for row in cached]

    scores = score_universe(db)
    set_scored_universe_cache([asdict(s) for s in scores])
    return scores
