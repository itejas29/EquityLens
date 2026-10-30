"""NSE-style normalized momentum (ranking_engine="momentum_normalized").

The Phase 23 challenger to V1's raw 12m-1m ranking: returns divided by
volatility, so how a stock got its return matters, not only how much.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.core.strategy_params import StrategyParams
from app.services.backtest_scoring import compute_point_in_time_universe, normalized_momentum_scores

DAYS = 300


def _closes(daily_returns) -> pd.Series:
    return pd.Series(100.0 * np.cumprod(1 + np.asarray(daily_returns)))


def _swinging(total, n, swing):
    """`total` return over n days, reached through alternating daily swings of
    `swing` — so volatility is set by `swing` alone, independent of return.
    (A perfectly smooth series would have zero volatility, and dividing by it
    would rank on rounding noise.)"""
    base = (1 + total) ** (1 / n) - 1
    up, down = (1 + base) * (1 + swing) - 1, (1 + base) / (1 + swing) - 1
    return _closes([up if i % 2 == 0 else down for i in range(n)])


def _steady(total=0.30, n=DAYS):
    return _swinging(total, n, 0.005)


def _jumpy(total=0.30, n=DAYS):
    return _swinging(total, n, 0.03)


def test_a_steady_riser_outranks_a_jumpy_one_with_the_same_return():
    steady, jumpy = _steady(), _jumpy()
    assert steady.iloc[-1] == pytest.approx(jumpy.iloc[-1], rel=1e-6)
    scores = normalized_momentum_scores({1: steady, 2: jumpy, 3: _steady(total=0.05)})
    assert scores[1] > scores[2], "volatility should cost the jumpy stock rank"
    assert scores[1] > scores[3], "more return at the same smoothness should rank higher"


def test_the_scores_are_z_scores_averaged_across_both_windows():
    scores = normalized_momentum_scores({1: _steady(0.40), 2: _steady(0.10), 3: _jumpy(0.25)})
    # Each window's z-scores have mean 0, so their average does too.
    assert sum(scores.values()) == pytest.approx(0.0, abs=1e-9)


def test_a_short_history_is_excluded_not_scored_on_a_shorter_window():
    scores = normalized_momentum_scores({1: _steady(n=DAYS), 2: _steady(n=200), 3: _jumpy()})
    assert 2 not in scores and set(scores) == {1, 3}


def test_fewer_than_two_scoreable_stocks_ranks_nothing():
    assert normalized_momentum_scores({1: _steady()}) == {}


def _frame(closes: pd.Series, start=date(2024, 1, 1)) -> pd.DataFrame:
    days, d = [], start
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    c = closes.to_numpy()
    return pd.DataFrame({"date": days, "open": c, "high": c * 1.01, "low": c * 0.99,
                         "close": c, "volume": np.full(len(c), 1_000_000)})


def test_backtest_scoring_ranks_by_it_and_caching_does_not_change_it():
    """Look-ahead is covered where it can actually happen — run_backtest
    bounding the frames — by test_backtest_execution's cutoff test, which runs
    for this engine too."""
    params = StrategyParams(ranking_engine="momentum_normalized")
    frames = {1: _frame(_steady(0.40)), 2: _frame(_jumpy(0.40)), 3: _frame(_steady(0.05))}
    bench = frames[1][["date", "close"]].assign(close=lambda df: 20000 + df.index * 5.0)
    as_of = frames[1]["date"].iloc[-1]

    snap = compute_point_in_time_universe(frames, bench, params, None, as_of)
    order = sorted(snap, key=lambda sid: snap[sid].overall_score, reverse=True)
    # 40% steady first. The jumpy 40% swings 6x as hard, so its ratio is
    # 0.40/6 per unit of volatility — still ahead of the steady 5% (0.05).
    assert order == [1, 2, 3]

    # The ranking is computed outside the indicator cache, so a cached and an
    # uncached scoring of the same date must agree exactly.
    cut = frames[1]["date"].iloc[-41]
    bounded = {sid: df[df["date"] <= cut] for sid, df in frames.items()}
    early = compute_point_in_time_universe(bounded, bench[bench["date"] <= cut], params, None, cut)
    again = compute_point_in_time_universe(bounded, bench[bench["date"] <= cut], params, {}, cut)
    assert {k: v.overall_score for k, v in early.items()} == {k: v.overall_score for k, v in again.items()}
