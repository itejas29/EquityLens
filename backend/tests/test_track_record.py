"""The Track Record page is the app's strongest credibility claim.

Its own docstring says so: "the only evidence about the strategy that no
methodology argument can take away". Phases 18 and 19 attacked this project's
backtest on survivorship, universe construction and cost assumptions and it did
not survive; these numbers are forward and out-of-sample. So the arithmetic
behind them has to be right, and it had no test.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.daily_signal import DailySignal, SignalOutcome
from app.models.stock import Stock
from app.services.forward_testing import MIN_SAMPLE_FOR_HORIZON, compute_track_record


def _signal(db, day, stock, rank=1):
    s = DailySignal(
        date=day, stock_id=stock.id, rank=rank, overall_score=Decimal("80.00"),
        signal="BUY", reference_close=Decimal("100.00"), reference_date=day,
        entry_low=Decimal("99.00"), entry_high=Decimal("101.00"),
        stop_loss=Decimal("90.00"), target_price=Decimal("120.00"),
        risk_reward=Decimal("1.6667"), stop_method="atr",
        rationale=["test fixture"],
    )
    db.add(s)
    db.flush()
    return s


def _outcome(db, signal, *, r1=None, n1=None, target_hit=False, stop_hit=False):
    o = SignalOutcome(
        signal_id=signal.id, evaluated_at=signal.date,
        current_price=Decimal("100.00"),
        return_1d=Decimal(str(r1)) if r1 is not None else None,
        nifty_return_1d=Decimal(str(n1)) if n1 is not None else None,
        target_hit=target_hit, stop_hit=stop_hit,
    )
    db.add(o)
    db.flush()
    return o


@pytest.fixture
def stock(db_session):
    s = Stock(symbol="TRK", is_active=True)
    db_session.add(s)
    db_session.flush()
    return s


def _h1(result):
    return next(h for h in result["horizons"] if h["horizon_days"] == 1)


def test_edge_is_the_mean_of_per_signal_differences(db_session, stock):
    day = date(2026, 9, 1)
    for r, n in [(5.0, 2.0), (1.0, 3.0), (4.0, 1.0)]:
        _outcome(db_session, _signal(db_session, day, stock), r1=r, n1=n)
        day += timedelta(days=1)

    h = _h1(compute_track_record(db_session))
    # (5-2) + (1-3) + (4-1) = 4, over 3 signals.
    assert h["edge_vs_nifty_pct"] == pytest.approx(1.33, abs=0.01)
    assert h["avg_return_pct"] == pytest.approx(3.33, abs=0.01)
    assert h["avg_nifty_return_pct"] == pytest.approx(2.0, abs=0.01)
    assert h["edge_sample"] == 3


def test_an_unmatched_nifty_window_does_not_corrupt_the_edge(db_session, stock):
    """The defect this test exists for.

    The edge used to be `mean(all returns) - mean(nifty over matched only)`.
    With an unmatched signal those two averages cover DIFFERENT sets, and the
    difference is not an edge. Here the unmatched signal returns +100%, which
    would drag a naive edge wildly positive while the true paired edge is 0.
    """
    day = date(2026, 9, 1)
    for r, n in [(5.0, 5.0), (3.0, 3.0)]:
        _outcome(db_session, _signal(db_session, day, stock), r1=r, n1=n)
        day += timedelta(days=1)
    # A signal with a return but no NIFTY window for it.
    _outcome(db_session, _signal(db_session, day, stock), r1=100.0, n1=None)

    h = _h1(compute_track_record(db_session))

    assert h["sample"] == 3, "the unmatched signal still counts toward the return average"
    assert h["edge_sample"] == 2, "but the edge is computed only over matched pairs"
    assert h["edge_vs_nifty_pct"] == pytest.approx(0.0, abs=0.01), (
        "an unmatched signal leaked into the edge"
    )
    # The naive computation would have produced this instead:
    naive = h["avg_return_pct"] - h["avg_nifty_return_pct"]
    assert naive == pytest.approx(32.0, abs=0.5)
    assert h["edge_vs_nifty_pct"] != pytest.approx(naive, abs=0.5)


def test_a_negative_edge_is_reported_plainly(db_session, stock):
    """Reported whether or not it flatters the strategy — which, on the live
    data as of this audit, it does not."""
    day = date(2026, 9, 1)
    for r, n in [(1.0, 3.0), (0.0, 2.0)]:
        _outcome(db_session, _signal(db_session, day, stock), r1=r, n1=n)
        day += timedelta(days=1)

    h = _h1(compute_track_record(db_session))
    assert h["edge_vs_nifty_pct"] == pytest.approx(-2.0, abs=0.01)


def test_sufficient_sample_gates_on_the_configured_minimum(db_session, stock):
    day = date(2026, 9, 1)
    for _ in range(MIN_SAMPLE_FOR_HORIZON - 1):
        _outcome(db_session, _signal(db_session, day, stock), r1=1.0, n1=0.0)
        day += timedelta(days=1)
    assert _h1(compute_track_record(db_session))["sufficient_sample"] is False

    _outcome(db_session, _signal(db_session, day, stock), r1=1.0, n1=0.0)
    assert _h1(compute_track_record(db_session))["sufficient_sample"] is True


def test_win_rate_and_beat_rate_use_their_own_denominators(db_session, stock):
    """win_rate is over every signal with a return; beat_nifty_rate only over
    those with a NIFTY window. Different denominators on purpose, both
    labelled."""
    day = date(2026, 9, 1)
    _outcome(db_session, _signal(db_session, day, stock), r1=5.0, n1=1.0)
    _outcome(db_session, _signal(db_session, day + timedelta(days=1), stock), r1=-2.0, n1=1.0)
    _outcome(db_session, _signal(db_session, day + timedelta(days=2), stock), r1=3.0, n1=None)

    h = _h1(compute_track_record(db_session))
    assert h["win_rate_pct"] == pytest.approx(66.7, abs=0.1)   # 2 of 3 positive
    assert h["beat_nifty_rate_pct"] == pytest.approx(50.0, abs=0.1)  # 1 of 2 matched


def test_a_horizon_with_no_data_reports_zero_not_a_fabricated_average(db_session, stock):
    _outcome(db_session, _signal(db_session, date(2026, 9, 1), stock), r1=5.0, n1=1.0)
    result = compute_track_record(db_session)

    h20 = next(h for h in result["horizons"] if h["horizon_days"] == 20)
    assert h20["sample"] == 0
    assert h20["sufficient_sample"] is False
    assert "avg_return_pct" not in h20 or h20.get("avg_return_pct") is None


def test_target_and_stop_counts_are_reported_as_counts(db_session, stock):
    day = date(2026, 9, 1)
    _outcome(db_session, _signal(db_session, day, stock), r1=5.0, n1=1.0, target_hit=True)
    _outcome(db_session, _signal(db_session, day + timedelta(days=1), stock), r1=-9.0, n1=1.0, stop_hit=True)
    _outcome(db_session, _signal(db_session, day + timedelta(days=2), stock), r1=1.0, n1=1.0)

    result = compute_track_record(db_session)
    assert result["target_hit"] == 1
    assert result["stop_hit"] == 1
    assert result["resolved"] == 2
    assert result["signals_published"] == 3


def test_an_empty_database_reports_nothing_rather_than_zeros(db_session):
    result = compute_track_record(db_session)
    assert result["signals_published"] == 0
    assert result["signals_evaluated"] == 0
    assert result["first_signal_date"] is None
    for h in result["horizons"]:
        assert h["sample"] == 0


# ------------------------------------- returns from a price you can actually pay --

def test_returns_are_also_reported_from_the_top_of_the_entry_zone(db_session, stock):
    """Stored returns are measured from reference_close. Nobody can buy there.

    The published call is "buy between entry_low and entry_high", so entry_high
    is the worst fill inside the app's own zone. Measured across all 168 live
    signals, entry_high sits a mean of 1.276% above reference_close — so every
    figure on the page is that much better than following the call would give.
    """
    from app.services.forward_testing import _from_entry

    s = _signal(db_session, date(2026, 9, 1), stock)
    # reference_close 100.00, entry_high 101.00 -> a 1% worse basis.
    _outcome(db_session, s, r1=10.0, n1=2.0)

    h = _h1(compute_track_record(db_session))

    assert h["avg_return_pct"] == pytest.approx(10.0, abs=0.01)
    # 100 * 1.10 = 110 at eval; from 101.00 that is 8.91%, not 10%.
    assert h["avg_return_from_entry_pct"] == pytest.approx(8.91, abs=0.01)
    assert h["edge_from_entry_pct"] == pytest.approx(6.91, abs=0.01)
    assert h["edge_vs_nifty_pct"] == pytest.approx(8.0, abs=0.01)


def test_the_rebasing_is_exact_not_an_approximation():
    """Recovers the evaluation price from the stored return, then re-divides.
    Subtracting the entry gap from the return would be wrong by the cross term."""
    from app.services.forward_testing import _from_entry

    ref, hi, r = 250.0, 253.5, 12.0
    eval_price = ref * (1 + r / 100)
    assert _from_entry(r, ref, hi) == pytest.approx((eval_price - hi) / hi * 100, abs=1e-9)

    naive = r - (hi - ref) / ref * 100
    assert _from_entry(r, ref, hi) != pytest.approx(naive, abs=1e-6), (
        "the naive subtraction happens to match — pick inputs where it does not"
    )


def test_rebasing_makes_a_loss_worse_and_a_gain_smaller(db_session, stock):
    """Direction check: a worse entry price can only reduce the return."""
    from app.services.forward_testing import _from_entry

    assert _from_entry(10.0, 100.0, 101.0) < 10.0
    assert _from_entry(-5.0, 100.0, 101.0) < -5.0
    # An entry zone that never rose above the reference close changes nothing.
    assert _from_entry(10.0, 100.0, 100.0) == pytest.approx(10.0, abs=1e-9)


def test_a_zero_or_negative_basis_does_not_divide(db_session):
    """Bad data must not raise on the app's most-viewed page."""
    from app.services.forward_testing import _from_entry

    assert _from_entry(5.0, 0.0, 100.0) == 5.0
    assert _from_entry(5.0, 100.0, 0.0) == 5.0
