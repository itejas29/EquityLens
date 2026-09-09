"""The published risk:reward has to describe the trade a user can actually take.

levels.py builds the target from the CLOSE — target = close + rr * (close -
stop) — while the entry zone extends half an ATR ABOVE that close. So a buyer
filling at the top of the published zone gets less upside and more downside
than the nominal ratio claims, always in the optimistic direction.

/daily-signals already measured its upside_pct and downside_pct from
entry_high, with a comment stating they are "directly comparable to each other
and to the stated risk:reward". They were not: the stated figure was the
nominal parameter. This is the CLAUDE.md rule-6 case — directive output is
fine, an overstated reward on a BUY call is not.
"""

import pandas as pd
import pytest

from app.core.strategy_params import StrategyParams
from app.core.v1_strategy import V1
from app.services.levels import compute_levels


def _flat_series(price=100.0, atr=4.0, n=60):
    """A series whose true range is exactly `atr` every day, so ATR is known."""
    close = pd.Series([price] * n)
    half = atr / 2
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n).date,
        "open": close, "high": close + half, "low": close - half, "close": close,
    })


def test_the_reported_ratio_matches_the_levels_it_is_published_with():
    """The invariant: whatever risk_reward says must equal
    (target - entry_high) / (entry_high - stop) for the SAME rounded levels
    shown next to it."""
    for params in (V1, StrategyParams()):
        lv = compute_levels(_flat_series(), params)
        assert lv is not None
        expected = (lv.target_price - lv.entry_high) / (lv.entry_high - lv.stop_loss)
        assert lv.risk_reward == pytest.approx(expected, abs=1e-4)


def test_live_v1_reports_1_67_not_2_00():
    """Measured, with the live frozen parameters: 4.0 ATR stop, no support
    stop, nominal rr 2.0. Reporting the nominal overstated it by 20%."""
    lv = compute_levels(_flat_series(), V1)
    assert lv.stop_method == "atr"
    assert lv.risk_reward_nominal == 2.0
    assert lv.risk_reward == pytest.approx(1.6667, abs=1e-4)


def test_a_support_stop_can_invert_the_ratio_entirely():
    """The worse case. With the support stop enabled the stop can sit ABOVE
    the ATR stop, collapsing the reward and inflating the risk — a measured
    0.50 against a nominal 2.00, a 4x overstatement."""
    lv = compute_levels(_flat_series(), StrategyParams())
    assert lv.stop_method == "support"
    assert lv.risk_reward == pytest.approx(0.5, abs=1e-4)
    assert lv.risk_reward_nominal == 2.0


def test_the_nominal_parameter_is_still_reported_separately():
    """The geometry the target was built from stays visible; it just no longer
    masquerades as the outcome."""
    lv = compute_levels(_flat_series(), V1)
    assert lv.risk_reward_nominal == V1.risk_reward_ratio
    assert lv.risk_reward != lv.risk_reward_nominal


def test_the_ratio_is_measured_on_the_rounded_levels():
    """Published levels are rounded to the paisa. A ratio computed from the
    unrounded intermediates would describe numbers nobody can see."""
    lv = compute_levels(_flat_series(price=137.77, atr=3.33), V1)
    assert lv is not None
    for value in (lv.entry_low, lv.entry_high, lv.stop_loss, lv.target_price):
        assert value == round(value, 2)
    expected = (lv.target_price - lv.entry_high) / (lv.entry_high - lv.stop_loss)
    assert lv.risk_reward == pytest.approx(expected, abs=1e-4)


def test_an_invalid_setup_is_still_none_not_a_fabricated_ratio():
    """A stop at or above entry is not a trade. Nulled, never clamped."""
    n = 60
    close = pd.Series([100.0] * n)
    df = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=n).date,
                       "open": close, "high": close, "low": close, "close": close})
    # Zero true range -> zero ATR -> stop == entry -> rejected.
    assert compute_levels(df, V1) is None


def test_too_little_history_returns_none():
    close = pd.Series([100.0] * 5)
    df = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=5).date,
                       "open": close, "high": close + 1, "low": close - 1, "close": close})
    assert compute_levels(df, V1) is None


def test_the_backtest_path_uses_the_same_definition():
    """levels_from_atr exists so a parameter sweep does not re-derive ATR, and
    its docstring says it is "kept beside compute_levels so the two cannot
    drift apart". It is the BACKTEST's path — a divergence here would put the
    research engine and the published signal on different definitions of the
    same reported number.

    This caught a real miss: the first pass at this fix changed
    compute_levels and left levels_from_atr on the nominal ratio.
    """
    from app.services.levels import atr_and_support, levels_from_atr

    df = _flat_series()
    atr, support, close = atr_and_support(df)

    live = compute_levels(df, V1)
    backtest = levels_from_atr(close, atr, support, V1)

    assert backtest is not None
    assert backtest.risk_reward == pytest.approx(live.risk_reward, abs=1e-4)
    assert backtest.risk_reward_nominal == live.risk_reward_nominal
    assert (backtest.entry_high, backtest.stop_loss, backtest.target_price) == (
        live.entry_high, live.stop_loss, live.target_price
    )
