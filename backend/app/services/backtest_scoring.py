"""Point-in-time scoring for the backtest engine.

Reuses the exact same metric functions as the live scoring engine
(app/services/scoring.py) and indicator/level computations (indicators.py,
levels.py) — the only difference is every input here comes from a price
frame already filtered to `date <= as_of_date` by the caller, so nothing
computed inside this module can see data from after the simulated date.

Fundamentals are deliberately excluded — see app/core/backtest_config.py's
module docstring for why. Only technical + risk sub-scores feed the
backtest composite.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.core.backtest_config import BACKTEST_COMPOSITE_WEIGHTS, MIN_PRICE_ROWS_FOR_SCORING
from app.core.scoring_config import RISK_WEIGHTS, TECHNICAL_WEIGHTS
from app.services.indicators import compute_indicators
from app.services.levels import Levels, compute_levels
from app.services.scoring import (
    _beta_score,
    _composite,
    _golden_cross_score,
    _macd_momentum_score,
    _map_signal,
    _price_vs_dma50_score,
    _rsi_band_score,
    _volume_confirmation_score,
    _weighted_subscore,
    percentile_score,
)

LIQUIDITY_WINDOW = 20


@dataclass
class PointInTimeSnapshot:
    stock_id: int
    technical_score: float | None
    risk_score: float | None
    overall_score: float | None
    signal: str | None
    levels: Levels | None
    latest_close: float | None


def _liquidity(price_df: pd.DataFrame) -> float | None:
    recent = price_df.tail(LIQUIDITY_WINDOW)
    traded_values = [
        float(c) * float(v) for c, v in zip(recent["close"], recent["volume"]) if pd.notna(c) and pd.notna(v)
    ]
    return float(np.mean(traded_values)) if traded_values else None


def compute_point_in_time_universe(
    bounded_frames: dict[int, pd.DataFrame], bounded_benchmark_df: pd.DataFrame
) -> dict[int, PointInTimeSnapshot]:
    """bounded_frames: {stock_id: OHLCV DataFrame already filtered to
    date <= as_of_date}. bounded_benchmark_df: ^NSEI OHLC, same cutoff."""

    raw: dict[int, dict] = {}
    for stock_id, price_df in bounded_frames.items():
        if len(price_df) < MIN_PRICE_ROWS_FOR_SCORING:
            continue

        indicator_series = compute_indicators(price_df, bounded_benchmark_df)
        latest = indicator_series.iloc[-1]
        prev = indicator_series.iloc[-2] if len(indicator_series) >= 2 else None

        latest_close = price_df["close"].astype(float).iloc[-1]
        if pd.isna(latest_close):
            continue

        raw[stock_id] = {
            "close": float(latest_close),
            "dma_50": latest.dma_50,
            "dma_200": latest.dma_200,
            "rsi_14": latest.rsi_14,
            "macd_hist": latest.macd_hist,
            "macd_hist_prev": prev.macd_hist if prev is not None else None,
            "volume_ratio": latest.volume_ratio,
            "volatility": latest.volatility,
            "beta": latest.beta,
            "max_drawdown": latest.max_drawdown,
            "liquidity": _liquidity(price_df),
            "levels": compute_levels(price_df),
        }

    if not raw:
        return {}

    universe_df = pd.DataFrame.from_dict(raw, orient="index")

    results: dict[int, PointInTimeSnapshot] = {}
    for stock_id, row in universe_df.iterrows():
        technical_metrics = {
            "price_vs_dma50": _price_vs_dma50_score(row.close, row.dma_50),
            "golden_cross": _golden_cross_score(row.dma_50, row.dma_200),
            "rsi_band": _rsi_band_score(row.rsi_14),
            "macd_momentum": _macd_momentum_score(row.macd_hist, row.macd_hist_prev),
            "volume_confirmation": _volume_confirmation_score(row.volume_ratio),
        }
        technical_score, _ = _weighted_subscore(technical_metrics, TECHNICAL_WEIGHTS)

        risk_metrics = {
            "volatility": percentile_score(row.volatility, universe_df["volatility"], False),
            "beta": _beta_score(row.beta),
            "max_drawdown": percentile_score(row.max_drawdown, universe_df["max_drawdown"], True),
            "liquidity": percentile_score(row.liquidity, universe_df["liquidity"], True),
        }
        risk_score, _ = _weighted_subscore(risk_metrics, RISK_WEIGHTS)

        overall = _composite({"technical": technical_score, "risk": risk_score}, BACKTEST_COMPOSITE_WEIGHTS)

        results[stock_id] = PointInTimeSnapshot(
            stock_id=stock_id,
            technical_score=technical_score,
            risk_score=risk_score,
            overall_score=overall,
            signal=_map_signal(overall),
            levels=row.levels,
            latest_close=row.close,
        )

    return results
