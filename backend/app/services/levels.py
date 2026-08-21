"""Entry zone / stop-loss / target computation from ATR (Average True Range).

ATR uses the same Wilder smoothing as RSI (app/services/indicators.py) —
seeded with a plain average of the first `period` true-range values, then
recursively smoothed — rather than a plain EMA, for the same reason: an EMA
seeded from the first observation gives different early values.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.core.portfolio_config import (
    ATR_PERIOD,
    ATR_STOP_MULTIPLIER,
    ENTRY_ZONE_ATR_MULTIPLIER,
    RISK_REWARD_RATIO,
    SUPPORT_LOOKBACK_DAYS,
)
from app.models.price_history import PriceHistory


@dataclass
class Levels:
    entry_low: float
    entry_high: float
    stop_loss: float
    target_price: float
    risk_reward: float
    stop_method: str  # "atr" or "support"
    # Carried explicitly rather than left to be inferred from the zone width.
    # The zone is now symmetrical: [close - 0.5*ATR, close + 0.5*ATR], so its 
    # width is a full ATR. Code that back-computes ATR from the zone width is 
    # fragile, so we carry it explicitly.
    atr: float


def _wilder_atr(true_range: pd.Series, period: int = ATR_PERIOD) -> pd.Series:
    """Wilder-smoothed ATR. A NaN true-range day (missing OHLC) carries the
    prior average forward unchanged instead of updating it — the recursive
    formula has no fixed window to "roll off" a bad day, so if a NaN were
    allowed through the arithmetic it would poison every value for the rest
    of the series, not just that one day.
    """
    n = len(true_range)
    atr = pd.Series(np.nan, index=true_range.index, dtype=float)
    if n < period:
        return atr

    atr.iloc[period - 1] = true_range.iloc[0:period].mean()
    for i in range(period, n):
        tr_i = true_range.iloc[i]
        if pd.isna(tr_i):
            atr.iloc[i] = atr.iloc[i - 1]
        else:
            atr.iloc[i] = (atr.iloc[i - 1] * (period - 1) + tr_i) / period
    return atr


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    tr.iloc[0] = high.iloc[0] - low.iloc[0]  # no prior close for the first row
    return tr


def compute_levels(price_df: pd.DataFrame, params: "StrategyParams | None" = None) -> Levels | None:
    """price_df: columns [date, open, high, low, close], sorted ascending.

    `params` injects the exit geometry so the optimiser can sweep it. Omitted,
    it falls back to the live defaults and behaves exactly as before.

    Returns None if there isn't enough history for ATR, if the latest close
    or ATR is missing, or if the computed stop would sit at/above entry (a
    genuinely invalid setup — not something to paper over with a fallback
    number).
    """
    from app.core.strategy_params import StrategyParams  # local: avoids an import cycle

    params = params or StrategyParams()
    df = price_df.sort_values("date").reset_index(drop=True)
    if len(df) < ATR_PERIOD:
        return None

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    if pd.isna(close.iloc[-1]):
        return None

    tr = _true_range(high, low, close)
    atr = _wilder_atr(tr, ATR_PERIOD)
    latest_atr = atr.iloc[-1]
    if pd.isna(latest_atr):
        return None

    entry = float(close.iloc[-1])
    latest_atr = float(latest_atr)
    entry_low = entry - params.entry_zone_atr_multiplier * latest_atr
    # Provide an upper buffer equal to the lower buffer so the zone is symmetrical
    entry_high = entry + params.entry_zone_atr_multiplier * latest_atr

    atr_stop = entry - params.atr_stop_multiplier * latest_atr

    # 20-day rolling low as the "nearest support" — only used once a full
    # window of history exists, same warm-up rule as everywhere else.
    support = None
    if params.use_support_stop and len(low) >= params.support_lookback_days:
        window_low = float(low.tail(params.support_lookback_days).min())
        if not pd.isna(window_low):
            support = window_low

    if support is not None and support > atr_stop:
        stop_loss = support
        stop_method = "support"
    else:
        stop_loss = atr_stop
        stop_method = "atr"

    if stop_loss >= entry:
        # A stop at/above the entry price is not a valid trade setup —
        # nulled rather than clamped to something arbitrary.
        return None

    risk_per_share = entry - stop_loss
    target_price = entry + params.risk_reward_ratio * risk_per_share

    return Levels(
        entry_low=round(entry_low, 2),
        entry_high=round(entry_high, 2),
        stop_loss=round(stop_loss, 2),
        target_price=round(target_price, 2),
        risk_reward=params.risk_reward_ratio,
        stop_method=stop_method,
        atr=round(latest_atr, 2),
    )


def levels_from_atr(entry: float, latest_atr: float, support_low: float | None, params=None) -> Levels | None:
    """Build levels from an already-computed ATR and 20-day low.

    Same arithmetic as compute_levels, without the Wilder recursion. Exists
    because ATR does not depend on any swept parameter: a stop-multiplier sweep
    recomputing it per candidate spends all its time re-deriving an identical
    number. Kept beside compute_levels so the two cannot drift apart.
    """
    from app.core.strategy_params import StrategyParams

    params = params or StrategyParams()
    entry_low = entry - params.entry_zone_atr_multiplier * latest_atr
    atr_stop = entry - params.atr_stop_multiplier * latest_atr

    if params.use_support_stop and support_low is not None and support_low > atr_stop:
        stop_loss, stop_method = support_low, "support"
    else:
        stop_loss, stop_method = atr_stop, "atr"

    if stop_loss >= entry:
        return None

    risk_per_share = entry - stop_loss
    entry_high = entry + params.entry_zone_atr_multiplier * latest_atr
    return Levels(
        entry_low=round(entry_low, 2),
        entry_high=round(entry_high, 2),
        stop_loss=round(stop_loss, 2),
        target_price=round(entry + params.risk_reward_ratio * risk_per_share, 2),
        risk_reward=params.risk_reward_ratio,
        stop_method=stop_method,
        atr=round(latest_atr, 2),
    )


def atr_and_support(price_df: pd.DataFrame, lookback: int = SUPPORT_LOOKBACK_DAYS):
    """(latest ATR, 20-day low, latest close) — the parameter-independent inputs."""
    df = price_df.sort_values("date").reset_index(drop=True)
    if len(df) < ATR_PERIOD:
        return None, None, None
    high, low, close = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    if pd.isna(close.iloc[-1]):
        return None, None, None
    latest_atr = _wilder_atr(_true_range(high, low, close), ATR_PERIOD).iloc[-1]
    if pd.isna(latest_atr):
        return None, None, None
    support = float(low.tail(lookback).min()) if len(low) >= lookback else None
    if support is not None and pd.isna(support):
        support = None
    return float(latest_atr), support, float(close.iloc[-1])


def load_ohlc_df(db: Session, stock_id: int) -> pd.DataFrame:
    rows = (
        db.query(PriceHistory.date, PriceHistory.open, PriceHistory.high, PriceHistory.low, PriceHistory.close)
        .filter(PriceHistory.stock_id == stock_id)
        .order_by(PriceHistory.date)
        .all()
    )
    return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close"])


def compute_levels_for_stock(db: Session, stock_id: int, params=None) -> Levels | None:
    ohlc = load_ohlc_df(db, stock_id)
    if ohlc.empty:
        return None
    return compute_levels(ohlc, params)
