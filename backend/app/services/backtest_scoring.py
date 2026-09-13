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
from app.services.levels import Levels, atr_and_support, levels_from_atr
from app.services.price_integrity import find_discontinuity
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
    # Carried for inverse-volatility position sizing (Phase 15). Annualised
    # realized vol, same series the risk sub-score percentile-ranks.
    volatility: float | None = None
    # Phase 17 recent-trend entry gate. True when the gate is off (the default)
    # so existing behaviour is unchanged. False means "eligible on rank, but its
    # recent trend contradicts that rank" — checked ONLY when opening a new
    # position, never when ranking or when deciding to hold.
    trend_ok: bool = True
    # Trailing max drawdown, the same series the risk sub-score percentile-ranks.
    # Carried for the Phase 21 quality gate, which is applied at SELECTION time
    # rather than during scoring — so every arm of that sweep produces an
    # identical snapshot and can share one indicator cache.
    max_drawdown: float | None = None


def _liquidity(price_df: pd.DataFrame) -> float | None:
    recent = price_df.tail(LIQUIDITY_WINDOW)
    traded_values = [
        float(c) * float(v) for c, v in zip(recent["close"], recent["volume"]) if pd.notna(c) and pd.notna(v)
    ]
    return float(np.mean(traded_values)) if traded_values else None


def compute_indicator_snapshot(
    bounded_frames: dict[int, pd.DataFrame], bounded_benchmark_df: pd.DataFrame
) -> dict[int, dict]:
    """Everything a point-in-time snapshot needs EXCEPT levels.

    Split out because this is the expensive half — a full indicator recompute
    for every stock — and it does not depend on any swept strategy parameter.
    The optimiser evaluates dozens of configurations over the same dates, so
    caching this makes each additional configuration nearly free. Levels, which
    DO depend on the parameters, are recomputed per configuration in
    compute_point_in_time_universe.
    """
    base: dict[int, dict] = {}
    for stock_id, price_df in bounded_frames.items():
        if len(price_df) < MIN_PRICE_ROWS_FOR_SCORING:
            continue

        indicator_series = compute_indicators(price_df, bounded_benchmark_df)
        latest = indicator_series.iloc[-1]
        prev = indicator_series.iloc[-2] if len(indicator_series) >= 2 else None

        latest_close = price_df["close"].astype(float).iloc[-1]
        if pd.isna(latest_close):
            continue

        base[stock_id] = {
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
        }
        # Cached with the indicators because neither depends on a swept
        # parameter — only how they are combined into a stop does.
        atr, support, entry = atr_and_support(price_df)
        base[stock_id]["_atr"] = atr
        base[stock_id]["_support"] = support
        base[stock_id]["_entry"] = entry

    return base


def compute_point_in_time_universe(
    bounded_frames: dict[int, pd.DataFrame],
    bounded_benchmark_df: pd.DataFrame,
    params=None,
    indicator_cache: dict | None = None,
    as_of=None,
) -> dict[int, PointInTimeSnapshot]:
    """bounded_frames: {stock_id: OHLCV DataFrame already filtered to
    date <= as_of_date}. bounded_benchmark_df: ^NSEI OHLC, same cutoff.

    `indicator_cache` is an optional caller-owned dict keyed by `as_of`. It
    holds the parameter-independent half so a parameter sweep over the same
    dates does not recompute indicators for every candidate.
    """
    cache_key = as_of
    if indicator_cache is not None and cache_key in indicator_cache:
        cached = indicator_cache[cache_key]
        # The entry holds whichever universe FIRST populated this date, and the
        # key is the date alone. Used as-is, a later arm with a different
        # membership could only ever score the stocks it shared with that first
        # arm: its own names were silently unscoreable. Phase 19 ran seven
        # memberships through one cache with `current_top500` first, so every
        # other arm — point-in-time, random, bottom — traded roughly its overlap
        # with current_top500. Its pit_top500 arm disagrees with Phase 20's
        # identically-configured live arm in 8 of 16 folds, by up to 11pp.
        #
        # compute_indicator_snapshot is strictly per-stock (each entry depends
        # only on that stock's frame and the benchmark), so computing the
        # missing stocks and merging them in gives exactly what a fresh
        # computation would. Restricting to the current universe keeps a wider
        # cached entry from leaking extra names into cross-sectional
        # percentiles. tests/test_indicator_cache.py pins both directions.
        missing = {sid: df for sid, df in bounded_frames.items() if sid not in cached}
        if missing:
            cached.update(compute_indicator_snapshot(missing, bounded_benchmark_df))
        base = {sid: cached[sid] for sid in bounded_frames if sid in cached}
    else:
        base = compute_indicator_snapshot(bounded_frames, bounded_benchmark_df)
        if indicator_cache is not None:
            indicator_cache[cache_key] = dict(base)

    if not base:
        return {}

    # Levels depend on the swept parameters, so they are always recomputed.
    # Levels are the only parameter-dependent part, and from a cached ATR they
    # are pure arithmetic rather than an O(n) Wilder recursion per candidate.
    raw = {}
    for stock_id, values in base.items():
        atr, support, entry = values.get("_atr"), values.get("_support"), values.get("_entry")
        levels = levels_from_atr(entry, atr, support, params) if (atr is not None and entry is not None) else None
        raw[stock_id] = {k: v for k, v in values.items() if not k.startswith("_")}
        raw[stock_id]["levels"] = levels

    # --- momentum ranking engine -------------------------------------
    # Replaces overall_score with a cross-sectional percentile of
    # (12-month return - 1-month return). The 1-month leg is subtracted
    # because the most recent month carries short-term reversal, which works
    # against momentum. Sub-scores are still computed below and reported, but
    # only overall_score drives selection.
    momentum_rank: dict[int, float] = {}
    if params is not None and getattr(params, "ranking_engine", "composite") == "momentum":
        raw_mom: dict[int, float] = {}
        for stock_id in raw:
            # Guard: a cached snapshot can contain stock_ids that are absent
            # from the current frames if universe membership changed between
            # runs sharing the cache. The `raw` construction below guards the
            # same way; this loop must too.
            if stock_id not in bounded_frames:
                continue
            closes = bounded_frames[stock_id]["close"].astype(float).dropna()
            lookback = getattr(params, "momentum_long_days", 252)
            skip = getattr(params, "momentum_skip_days", 21)
            if len(closes) < lookback + 1:
                continue
            # Same corporate-action gate as daily_signals._momentum_scores, and
            # it has to be here for the same reason that function's docstring
            # gives: "Identical construction ... so a signal published in
            # production matches what the backtest would have selected." Adding
            # the gate to only one of the two would break exactly the property
            # every phase of the research programme rests on.
            if find_discontinuity(closes.tolist()) is not None:
                continue
            last, long_ago = closes.iloc[-1], closes.iloc[-lookback]
            if long_ago <= 0:
                continue
            total = last / long_ago - 1
            if skip > 0:
                recent_base = closes.iloc[-skip]
                if recent_base <= 0:
                    continue
                total -= last / recent_base - 1
            raw_mom[stock_id] = float(total)
        if len(raw_mom) >= 2:
            ser = pd.Series(raw_mom).rank(pct=True) * 100
            momentum_rank = ser.to_dict()

    # --- recent-trend entry gate (Phase 17) ---------------------------
    # Computed from the same bounded_frames the ranking uses, so it is
    # point-in-time by construction and cannot see past `as_of`.
    trend_gate: dict[int, bool] = {}
    trend_days = getattr(params, "trend_confirm_days", 0) if params is not None else 0
    if trend_days > 0:
        min_ret = getattr(params, "trend_confirm_min_return", 0.0)
        for stock_id in raw:
            if stock_id not in bounded_frames:
                continue
            closes = bounded_frames[stock_id]["close"].astype(float).dropna()
            # Not enough history to judge the recent trend: leave the gate open
            # rather than silently excluding the stock, so a data gap never
            # masquerades as a trend signal.
            if len(closes) < trend_days + 1:
                continue
            base = closes.iloc[-(trend_days + 1)]
            if base <= 0:
                continue
            trend_gate[stock_id] = bool((closes.iloc[-1] / base - 1) >= min_ret)

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

        if momentum_rank:
            # Momentum engine: selection is driven entirely by the momentum
            # percentile. Sub-scores above are still computed and reported so
            # the two engines stay directly comparable in the output.
            overall = momentum_rank.get(stock_id)
        else:
            overall = _composite({"technical": technical_score, "risk": risk_score}, BACKTEST_COMPOSITE_WEIGHTS)

        results[stock_id] = PointInTimeSnapshot(
            trend_ok=trend_gate.get(stock_id, True),
            stock_id=stock_id,
            technical_score=technical_score,
            risk_score=risk_score,
            overall_score=overall,
            signal=_map_signal(overall),
            levels=row.levels,
            latest_close=row.close,
            volatility=None if pd.isna(row.volatility) else float(row.volatility),
            max_drawdown=None if pd.isna(row.max_drawdown) else float(row.max_drawdown),
        )

    return results
