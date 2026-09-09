"""Known-answer tests for the metrics every phase conclusion rests on.

_equity_metrics produces the CAGR, Sharpe, Sortino, max drawdown and Calmar
that Phases 11 through 20 are reported in. Until now nothing checked any of
them against a figure computed independently — the research programme's
headline numbers were unverified.

Each test below computes its expectation from the definition, by hand or with
plain numpy, rather than from the function under test.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.core.backtest_config import TRADING_DAYS_PER_YEAR
from app.services.backtest import _equity_metrics


def _curve(values, start=date(2020, 1, 1)):
    """One point per trading day, weekends skipped so the calendar span is
    realistic — CAGR divides by calendar years, so this matters."""
    out, day = [], start
    for v in values:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        out.append({"date": day, "equity": float(v)})
        day += timedelta(days=1)
    return out


# ------------------------------------------------------------------ returns --

def test_total_return_is_final_over_initial():
    m = _equity_metrics(_curve([100.0, 110.0, 125.0]), 100.0, 0.0)
    assert m["total_return_pct"] == 25.0
    assert m["final_equity"] == 125.0


def test_cagr_is_annualised_over_the_calendar_span():
    """A doubling across exactly 365 days is +100% CAGR. Computed from the
    definition: (final/initial)^(365.25/n_days) - 1."""
    start = date(2020, 1, 1)
    curve = [{"date": start, "equity": 100.0},
             {"date": start + timedelta(days=365), "equity": 200.0}]
    m = _equity_metrics(curve, 100.0, 0.0)

    expected = (200.0 / 100.0) ** (365.25 / 365) - 1
    assert m["cagr_pct"] == pytest.approx(expected * 100, abs=0.01)
    # Slightly above 100% because the span is 365 days and a year is 365.25,
    # so it is annualised UP a fraction. 2 ** (365.25/365) - 1 = 1.00095.
    assert m["cagr_pct"] == pytest.approx(100.09, abs=0.02)


def test_cagr_is_none_when_the_span_is_a_single_day():
    """No elapsed time means no annualisation is defined — None, not a
    fabricated number."""
    m = _equity_metrics(_curve([100.0]), 100.0, 0.0)
    assert m["cagr_pct"] is None


def test_a_wipeout_does_not_produce_a_complex_cagr():
    """(0/100)^(1/years) is 0, but a NEGATIVE final equity would raise a
    fractional power of a negative number. Guarded."""
    start = date(2020, 1, 1)
    curve = [{"date": start, "equity": 100.0},
             {"date": start + timedelta(days=365), "equity": 0.0}]
    m = _equity_metrics(curve, 100.0, 0.0)
    assert m["cagr_pct"] is None
    assert m["total_return_pct"] == -100.0


# ------------------------------------------------------------ risk-adjusted --

def _returns(curve):
    eq = pd.Series([p["equity"] for p in curve], dtype=float)
    return eq.pct_change().dropna()


def test_sharpe_matches_the_definition():
    rng = np.random.default_rng(11)
    eq = 100.0 * np.cumprod(1 + rng.normal(0.0006, 0.01, 400))
    curve = _curve(eq)
    rf = 0.06

    m = _equity_metrics(curve, 100.0, rf)
    r = _returns(curve)
    expected = ((r.mean() * TRADING_DAYS_PER_YEAR) - rf) / (r.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
    assert m["sharpe_ratio"] == pytest.approx(expected, abs=1e-3)


def test_sortino_uses_downside_deviation_not_the_std_of_negative_days():
    """The defect this test exists for.

    Sortino's denominator is the root-mean-square SHORTFALL below the target,
    over EVERY period. The old code took std() of only the negative returns,
    which measures spread about the mean of the negatives (itself negative) and
    divides by the count of negatives — two mistakes, both shrinking the
    denominator and inflating |Sortino|. Measured at 1.18x on a 2,000-day
    series.
    """
    rng = np.random.default_rng(11)
    eq = 100.0 * np.cumprod(1 + rng.normal(0.0006, 0.01, 400))
    curve = _curve(eq)
    rf = 0.06

    m = _equity_metrics(curve, 100.0, rf)
    r = _returns(curve)
    ann_return = r.mean() * TRADING_DAYS_PER_YEAR

    correct = (ann_return - rf) / (
        np.sqrt((r.clip(upper=0.0) ** 2).mean()) * np.sqrt(TRADING_DAYS_PER_YEAR))
    old_wrong = (ann_return - rf) / (r[r < 0].std() * np.sqrt(TRADING_DAYS_PER_YEAR))

    assert m["sortino_ratio"] == pytest.approx(correct, abs=1e-3)
    assert m["sortino_ratio"] != pytest.approx(old_wrong, abs=1e-3), (
        "still computing the std of the negative subset"
    )


def test_sortino_exceeds_sharpe_for_an_upward_trending_curve():
    """A structural sanity check independent of the formula: penalising only
    downside deviation must give a ratio at least as large as penalising total
    deviation, whenever the excess return is positive."""
    rng = np.random.default_rng(3)
    eq = 100.0 * np.cumprod(1 + rng.normal(0.0015, 0.008, 500))
    m = _equity_metrics(_curve(eq), 100.0, 0.0)
    assert m["sharpe_ratio"] > 0
    assert m["sortino_ratio"] >= m["sharpe_ratio"]


def test_a_curve_that_never_falls_has_no_downside_and_no_sortino():
    m = _equity_metrics(_curve([100.0 * 1.001 ** i for i in range(50)]), 100.0, 0.0)
    assert m["sortino_ratio"] is None
    assert m["max_drawdown_pct"] == 0.0


# ---------------------------------------------------------------- drawdown --

def test_max_drawdown_is_measured_from_the_running_peak():
    """100 -> 120 -> 90: the drawdown is 25% from the 120 peak, not 10% from
    the 100 start."""
    m = _equity_metrics(_curve([100.0, 120.0, 90.0, 110.0]), 100.0, 0.0)
    assert m["max_drawdown_pct"] == -25.0


def test_drawdown_duration_counts_trading_sessions_under_water():
    """The equity curve has one point per trading day, so this counts bars.
    120 peak, then three points below it, then a new high."""
    m = _equity_metrics(_curve([100.0, 120.0, 110.0, 105.0, 115.0, 130.0]), 100.0, 0.0)
    assert m["max_drawdown_duration_days"] == 3


def test_the_longest_drawdown_wins_not_the_last():
    curve = _curve([100.0, 90.0, 100.0, 95.0, 94.0, 93.0, 92.0, 101.0, 99.0])
    m = _equity_metrics(curve, 100.0, 0.0)
    assert m["max_drawdown_duration_days"] == 4


def test_calmar_is_cagr_over_the_drawdown_and_none_without_one():
    start = date(2020, 1, 1)
    curve = [{"date": start, "equity": 100.0},
             {"date": start + timedelta(days=180), "equity": 75.0},
             {"date": start + timedelta(days=365), "equity": 150.0}]
    m = _equity_metrics(curve, 100.0, 0.0)

    assert m["max_drawdown_pct"] == -25.0
    assert m["calmar_ratio"] == pytest.approx(
        m["cagr_pct"] / 100 / 0.25, abs=1e-3)

    flat = _equity_metrics(_curve([100.0 * 1.001 ** i for i in range(50)]), 100.0, 0.0)
    assert flat["calmar_ratio"] is None


# ------------------------------------------------------------------ guards --

def test_an_empty_curve_returns_no_metrics_rather_than_zeros():
    assert _equity_metrics([], 100.0, 0.0) == {}


def test_zero_capital_returns_no_metrics():
    assert _equity_metrics(_curve([100.0, 110.0]), 0.0, 0.0) == {}


def test_a_flat_curve_has_no_sharpe_rather_than_a_division_by_zero():
    m = _equity_metrics(_curve([100.0] * 30), 100.0, 0.0)
    assert m["sharpe_ratio"] is None
    assert m["annualised_volatility_pct"] == 0.0
