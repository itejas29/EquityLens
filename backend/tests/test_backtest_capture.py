"""Known-answer tests for the benchmark-relative metrics.

Phase 19's published conclusion cites downside capture of 154-196% as evidence
that V1's risk control fails. That number comes from _capture_ratios, and
nothing had ever checked it. If the function were wrong, a headline finding
would be wrong with it.

Expectations below are computed by hand from the standard definition —
compound the strategy over the months the BENCHMARK rose, divide by the
benchmark compounded over those same months; repeat for the months it fell.
"""

from datetime import date, timedelta

import pytest

from app.services.backtest import _capture_ratios, _relative_metrics

# Month-end equity for five consecutive month-ends, chosen so the monthly
# returns are exact and the capture ratios are hand-computable.
#
#   benchmark : +10%, -10%, +20%, -20%
#   strategy  :  +5%, -20%, +10%, -30%
#
#   up months   (benchmark > 0) : months 1 and 3
#     strategy compounded : 1.05 * 1.10 - 1 =  0.1550
#     benchmark compounded: 1.10 * 1.20 - 1 =  0.3200
#     upside capture      : 0.1550 / 0.3200 =  48.44%
#
#   down months (benchmark < 0) : months 2 and 4
#     strategy compounded : 0.80 * 0.70 - 1 = -0.4400
#     benchmark compounded: 0.90 * 0.80 - 1 = -0.2800
#     downside capture    : -0.4400 / -0.2800 = 157.14%
MONTH_ENDS = [date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 31),
              date(2024, 4, 30), date(2024, 5, 31)]
BENCH = [100.0, 110.0, 99.0, 118.8, 95.04]
STRAT = [100.0, 105.0, 84.0, 92.4, 64.68]


def _curve(dates, values):
    return [{"date": d, "equity": v} for d, v in zip(dates, values)]


def test_capture_ratios_match_the_standard_definition():
    out = _capture_ratios(_curve(MONTH_ENDS, STRAT), _curve(MONTH_ENDS, BENCH))
    assert out["upside_capture_pct"] == pytest.approx(48.44, abs=0.02)
    assert out["downside_capture_pct"] == pytest.approx(157.14, abs=0.02)


def test_downside_capture_above_100_means_losing_more_than_the_index():
    """The interpretation Phase 19 rests on. 157% here: the strategy fell 44%
    across the months the benchmark fell 28%."""
    out = _capture_ratios(_curve(MONTH_ENDS, STRAT), _curve(MONTH_ENDS, BENCH))
    assert out["downside_capture_pct"] > 100


def test_matching_the_index_exactly_gives_100_percent_both_ways():
    out = _capture_ratios(_curve(MONTH_ENDS, BENCH), _curve(MONTH_ENDS, BENCH))
    assert out["upside_capture_pct"] == pytest.approx(100.0, abs=0.01)
    assert out["downside_capture_pct"] == pytest.approx(100.0, abs=0.01)


def test_holding_cash_captures_neither_side():
    flat = [100.0] * len(MONTH_ENDS)
    out = _capture_ratios(_curve(MONTH_ENDS, flat), _curve(MONTH_ENDS, BENCH))
    assert out["upside_capture_pct"] == pytest.approx(0.0, abs=0.01)
    assert out["downside_capture_pct"] == pytest.approx(0.0, abs=0.01)


def test_gaining_while_the_index_falls_gives_NEGATIVE_downside_capture():
    """Not a bug, and the docstring used to claim it could not happen.

    A strategy that MAKES money in the benchmark's down months has a negative
    numerator over a negative denominator... no: a positive numerator over a
    negative denominator. The ratio is negative, which is correct and is the
    best possible outcome. "Lower is better" still holds; "the ratio stays
    positive" did not.
    """
    strat = [100.0, 105.0, 115.5, 121.3, 133.4]  # rises every month
    out = _capture_ratios(_curve(MONTH_ENDS, strat), _curve(MONTH_ENDS, BENCH))
    assert out["downside_capture_pct"] < 0


def test_too_few_months_reports_none_rather_than_a_number_from_one_point():
    two = MONTH_ENDS[:2]
    out = _capture_ratios(_curve(two, STRAT[:2]), _curve(two, BENCH[:2]))
    assert out["upside_capture_pct"] is None
    assert out["downside_capture_pct"] is None


def test_empty_curves_report_none():
    assert _capture_ratios([], []) == {"upside_capture_pct": None, "downside_capture_pct": None}


def test_the_first_partial_month_cannot_contaminate_a_return():
    """The backtest rarely starts on a month-end, so the first resampled point
    covers a partial month. pct_change drops it, so the first RETURN runs
    month-end to month-end. Asserted by adding a mid-month starting point and
    confirming the answer does not move."""
    dates = [date(2024, 1, 15)] + MONTH_ENDS
    out_with_partial = _capture_ratios(
        _curve(dates, [93.0] + STRAT), _curve(dates, [97.0] + BENCH))
    out_clean = _capture_ratios(_curve(MONTH_ENDS, STRAT), _curve(MONTH_ENDS, BENCH))
    assert out_with_partial == out_clean


# ------------------------------------------------------- relative metrics --

def test_tracking_error_and_information_ratio_match_the_definition():
    import numpy as np
    import pandas as pd

    out = _relative_metrics(_curve(MONTH_ENDS, STRAT), _curve(MONTH_ENDS, BENCH))

    s = pd.Series(STRAT).pct_change().dropna()
    b = pd.Series(BENCH).pct_change().dropna()
    active = (s - b).dropna()
    te = active.std() * np.sqrt(12)

    assert out["tracking_error_pct"] == pytest.approx(te * 100, abs=0.05)
    assert out["information_ratio"] == pytest.approx(active.mean() * 12 / te, abs=0.01)


def test_a_strategy_that_tracks_the_index_has_no_tracking_error():
    out = _relative_metrics(_curve(MONTH_ENDS, BENCH), _curve(MONTH_ENDS, BENCH))
    assert out["tracking_error_pct"] is None
    assert out["information_ratio"] is None
