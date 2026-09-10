"""Known-answer tests for the indicator computations.

These feed the composite score, the risk sub-score and the ML feature set. They
are NOT on V1's signal path — V1 ranks on momentum alone, and levels.py computes
its own ATR for stops — so the stakes are lower than the money or backtest
paths. They were the last service module with no test, and the point of testing
them is to pin behaviour that is currently correct rather than to fix it.

Expectations are computed from the definitions, not from the functions.
"""

import numpy as np
import pandas as pd
import pytest

from app.services.indicators import RSI_PERIOD
from app.services.indicators import _rolling_beta, _rolling_max_drawdown, _rsi_wilder  # noqa: E402


# ------------------------------------------------------------------- RSI --

def test_rsi_is_100_when_nothing_ever_falls():
    close = pd.Series([100.0 + i for i in range(RSI_PERIOD + 10)])
    rsi = _rsi_wilder(close)
    assert rsi.iloc[-1] == 100.0


def test_rsi_is_0_when_nothing_ever_rises():
    close = pd.Series([200.0 - i for i in range(RSI_PERIOD + 10)])
    rsi = _rsi_wilder(close)
    assert rsi.iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_rsi_sits_near_50_when_gains_and_losses_are_symmetric():
    """Alternating +1/-1 gives equal average gain and loss over any full pair.

    It does not settle on exactly 50 at an arbitrary index, and that is not an
    error: Wilder's smoothing is recursive, so on a strictly alternating series
    the two running averages oscillate a little either side of each other
    depending on whether the last bar was the up or the down one. The invariant
    worth asserting is symmetry — the value straddles 50 rather than drifting
    away from it.
    """
    close = pd.Series([100.0 + (i % 2) for i in range(RSI_PERIOD + 60)])
    rsi = _rsi_wilder(close)

    tail = rsi.iloc[-20:]
    assert tail.min() > 40 and tail.max() < 60
    assert tail.mean() == pytest.approx(50.0, abs=2.0)


def test_rsi_seeds_on_a_plain_average_then_smooths_by_wilder():
    """Not an EMA. The seed is the simple mean of the first `period` gains and
    losses; every value after applies weight (period-1)/period to the prior
    average. An .ewm() seeded from the first observation differs materially in
    the early values, which is why this is an explicit loop."""
    rng = np.random.default_rng(4)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, RSI_PERIOD + 30)))
    rsi = _rsi_wilder(close)

    delta = close.diff()
    gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
    ag = gain.iloc[1 : RSI_PERIOD + 1].mean()
    al = loss.iloc[1 : RSI_PERIOD + 1].mean()
    assert rsi.iloc[RSI_PERIOD] == pytest.approx(100 - 100 / (1 + ag / al), abs=1e-9)

    ag = (ag * (RSI_PERIOD - 1) + gain.iloc[RSI_PERIOD + 1]) / RSI_PERIOD
    al = (al * (RSI_PERIOD - 1) + loss.iloc[RSI_PERIOD + 1]) / RSI_PERIOD
    assert rsi.iloc[RSI_PERIOD + 1] == pytest.approx(100 - 100 / (1 + ag / al), abs=1e-9)


def test_a_missing_close_carries_the_average_forward_instead_of_poisoning_it():
    """The recursion has no fixed window to roll a bad day off, so a NaN let
    through the arithmetic would corrupt every later value, not just one."""
    close = pd.Series([100.0 + i for i in range(RSI_PERIOD + 15)])
    close.iloc[RSI_PERIOD + 5] = np.nan
    rsi = _rsi_wilder(close)
    assert rsi.iloc[-1] == pytest.approx(100.0), "one missing close poisoned the rest of the series"


def test_too_little_history_is_all_nan_not_a_guess():
    close = pd.Series([100.0] * (RSI_PERIOD - 1))
    assert _rsi_wilder(close).isna().all()


# ------------------------------------------------------------- drawdown --

def test_rolling_max_drawdown_measures_peak_to_trough_inside_the_window():
    close = pd.Series([100.0, 120.0, 90.0, 95.0, 130.0])
    dd = _rolling_max_drawdown(close, window=5)
    # Within the 5-day window: peak 120 -> trough 90 is -25%.
    assert dd.iloc[-1] == pytest.approx(-0.25, abs=1e-9)


def test_a_monotonic_rise_has_no_drawdown():
    close = pd.Series([100.0 * 1.01 ** i for i in range(10)])
    assert _rolling_max_drawdown(close, window=10).iloc[-1] == pytest.approx(0.0, abs=1e-12)


def test_a_missing_close_blanks_the_window_it_falls_in():
    """Documenting current behaviour rather than asserting it is ideal.

    _rolling_beta was deliberately rewritten to drop unpaired days rather than
    let one gap block a whole window; _rolling_max_drawdown was not given the
    same treatment, so a single NaN blanks `window` rows. Impact is limited —
    max_drawdown feeds the risk sub-score, which is not on V1's signal path —
    but the two functions handle the same situation differently and that is
    worth knowing before someone relies on either.
    """
    close = pd.Series([100.0, 120.0, np.nan, 95.0, 130.0])
    assert np.isnan(_rolling_max_drawdown(close, window=5).iloc[-1])


# ----------------------------------------------------------------- beta --

def _bench(dates, closes):
    return pd.DataFrame({"date": dates, "close": closes})


def test_beta_is_1_when_the_stock_tracks_the_benchmark_exactly():
    """A VARYING series, not a constant-return one.

    My first attempt used 100 * 1.002**i for both sides. Its returns are
    constant, so the benchmark variance is zero and beta becomes 0/0 — which
    came back as 4e10 rather than 1.0. The degenerate case is worth knowing
    about (see the next test) but it is not what "tracks the benchmark" means.
    """
    n = 60
    dates = [d.date() for d in pd.date_range("2026-01-01", periods=n, freq="B")]
    rng = np.random.default_rng(2)
    closes = (100 * np.cumprod(1 + rng.normal(0.0005, 0.01, n))).tolist()
    stock_ret = pd.Series(closes).pct_change()

    beta = _rolling_beta(pd.Series(dates), stock_ret, _bench(dates, closes), window=30)
    assert beta.iloc[-1] == pytest.approx(1.0, abs=1e-6)


def test_a_benchmark_that_never_moves_gives_a_meaningless_beta():
    """Documenting a real edge, not asserting it is handled well.

    beta = cov/var, and a benchmark with zero return variance makes that 0/0.
    The result is whatever floating point produces — in one run, 4e10. A real
    index always varies so this cannot arise in production, and the risk
    sub-score maps beta through a fixed band so an absurd value lands in the
    worst bucket rather than propagating. Recorded so the next person who sees
    a nonsense beta in a synthetic fixture knows why.
    """
    n = 60
    dates = [d.date() for d in pd.date_range("2026-01-01", periods=n, freq="B")]
    flat = [100.0 * 1.002 ** i for i in range(n)]  # constant return, zero variance
    beta = _rolling_beta(pd.Series(dates), pd.Series(flat).pct_change(),
                         _bench(dates, flat), window=30)
    assert not (0.5 < beta.iloc[-1] < 2.0), "expected a degenerate value from 0/0"


def test_beta_is_2_when_the_stock_moves_twice_as_much():
    n = 80
    dates = [d.date() for d in pd.date_range("2026-01-01", periods=n, freq="B")]
    rng = np.random.default_rng(9)
    bench_ret = rng.normal(0, 0.01, n)
    bench = 100 * np.cumprod(1 + bench_ret)
    stock = 100 * np.cumprod(1 + 2 * bench_ret)

    beta = _rolling_beta(pd.Series(dates), pd.Series(stock).pct_change(),
                         _bench(dates, bench.tolist()), window=40)
    assert beta.iloc[-1] == pytest.approx(2.0, abs=0.05)


def test_a_gap_day_does_not_block_beta_for_a_whole_window():
    """The documented reason this drops unpaired days first: NSE occasionally
    has a date a stock trades on but ^NSEI does not. A plain rolling().cov()
    needs every row non-NaN, so one gap would block beta for `window` rows even
    with hundreds of good paired observations either side."""
    n = 80
    dates = [d.date() for d in pd.date_range("2026-01-01", periods=n, freq="B")]
    rng = np.random.default_rng(9)
    bench_ret = rng.normal(0, 0.01, n)
    bench = (100 * np.cumprod(1 + bench_ret)).tolist()
    stock = 100 * np.cumprod(1 + 2 * bench_ret)

    bench_with_gap = list(bench)
    bench_with_gap[50] = np.nan

    beta = _rolling_beta(pd.Series(dates), pd.Series(stock).pct_change(),
                         _bench(dates, bench_with_gap), window=40)
    assert not np.isnan(beta.iloc[-1]), "one missing benchmark day blocked beta entirely"
    assert beta.iloc[-1] == pytest.approx(2.0, abs=0.1)


def test_no_benchmark_gives_nan_not_a_default_beta():
    """Missing data stays missing — a fabricated beta of 1.0 would flow into
    the risk sub-score as if it had been measured."""
    stock_ret = pd.Series([0.01, -0.01, 0.02])
    dates = pd.Series([d.date() for d in pd.date_range("2026-01-01", periods=3)])
    assert _rolling_beta(dates, stock_ret, None, window=2).isna().all()
    assert _rolling_beta(dates, stock_ret, pd.DataFrame(), window=2).isna().all()
