"""Trade-level metrics, and the cost asymmetry between strategy and benchmark.

_trade_metrics produces the win rate, profit factor and average win/loss
printed in every phase table. _benchmark_buy_and_hold produces the NIFTY curve
that every "vs NIFTY" claim is measured against. Neither had a test.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
import pytest

from app.services.backtest import _benchmark_buy_and_hold, _trade_metrics


@dataclass
class _T:
    pnl: float
    holding_days: int = 10


# ------------------------------------------------------------ trade metrics --

def test_win_rate_profit_factor_and_averages():
    trades = [_T(100.0), _T(50.0), _T(-30.0), _T(-20.0)]
    m = _trade_metrics(trades)

    assert m["num_trades"] == 4
    assert m["win_rate_pct"] == 50.0
    assert m["avg_win"] == 75.0            # (100 + 50) / 2
    assert m["avg_loss"] == -25.0          # (-30 + -20) / 2
    assert m["profit_factor"] == 3.0       # 150 / 50


def test_a_scratch_trade_counts_in_the_denominator_but_not_as_a_win():
    """A break-even trade is not a win. It still happened, so it stays in the
    trade count — which means win% + loss% need not sum to 100."""
    m = _trade_metrics([_T(100.0), _T(0.0)])
    assert m["num_trades"] == 2
    assert m["win_rate_pct"] == 50.0
    assert m["avg_loss"] is None


def test_profit_factor_is_none_rather_than_infinite_when_nothing_lost():
    m = _trade_metrics([_T(100.0), _T(50.0)])
    assert m["profit_factor"] is None, "an undefined ratio must not be reported as a number"
    assert m["avg_loss"] is None


def test_no_trades_reports_zero_and_nulls_not_zeros():
    m = _trade_metrics([])
    assert m["num_trades"] == 0
    for key in ("win_rate_pct", "avg_win", "avg_loss", "profit_factor",
                "avg_holding_period_days"):
        assert m[key] is None, f"{key} fabricated a value from no trades"


def test_holding_period_is_in_calendar_days():
    """Deliberately different from max_drawdown_duration_days, which counts
    trading sessions. Trade.holding_days is (exit_date - entry_date).days, so
    a position held over a weekend counts the weekend."""
    m = _trade_metrics([_T(10.0, holding_days=7), _T(-5.0, holding_days=21)])
    assert m["avg_holding_period_days"] == 14.0


# ------------------------------------------------------------- the benchmark --

def _bench_df(prices, start=date(2024, 1, 1)):
    return pd.DataFrame({
        "date": [start + timedelta(days=i) for i in range(len(prices))],
        "close": prices,
    })


def test_the_benchmark_is_a_frictionless_buy_and_hold():
    """Documented rather than changed: the benchmark pays NO transaction cost
    and NO slippage, while the strategy pays both on every leg.

    The bias runs AGAINST the strategy — the opposite direction to
    survivorship — and it is small: one round trip at the 0.12% default. It is
    asserted here so the asymmetry is a stated property of the comparison
    rather than an accident nobody has looked at.
    """
    df = _bench_df([100.0, 110.0])
    curve, metrics = _benchmark_buy_and_hold(df, date(2024, 1, 1), date(2024, 1, 2),
                                             100000.0, 0.0)

    assert curve[0]["equity"] == 100000.0, "the benchmark paid an entry cost"
    assert curve[-1]["equity"] == 110000.0, "the benchmark paid an exit cost"
    assert metrics["total_return_pct"] == 10.0


def test_the_benchmark_uses_fractional_shares():
    """initial_capital / start_price, unrounded — so the curve starts at
    exactly the capital rather than a share-rounded approximation of it."""
    df = _bench_df([137.77, 137.77])
    curve, _ = _benchmark_buy_and_hold(df, date(2024, 1, 1), date(2024, 1, 2),
                                       100000.0, 0.0)
    assert curve[0]["equity"] == pytest.approx(100000.0, abs=0.01)


def test_the_benchmark_window_is_inclusive_at_both_ends():
    df = _bench_df([100.0, 101.0, 102.0, 103.0])
    curve, _ = _benchmark_buy_and_hold(df, date(2024, 1, 2), date(2024, 1, 3),
                                       100000.0, 0.0)
    assert [p["date"] for p in curve] == [date(2024, 1, 2), date(2024, 1, 3)]


def test_an_empty_window_returns_nothing_rather_than_a_flat_line():
    df = _bench_df([100.0, 101.0])
    curve, metrics = _benchmark_buy_and_hold(df, date(2025, 1, 1), date(2025, 1, 2),
                                             100000.0, 0.0)
    assert curve == []
    assert metrics == {}
