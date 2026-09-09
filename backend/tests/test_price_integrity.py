"""The discriminator between a corporate action and a bad day.

Real stocks fall 30% in a session. What separates a split is not the size of
the move but its RATIO: a corporate action moves the price by a small rational
factor, because that is what the action does. Every one of the nine
discontinuities found in production on 2026-09-10 lands within 2% of such a
ratio, and the production values are used verbatim below rather than invented.
"""

import pytest

from app.services.price_integrity import (
    MIN_MOVE_PCT,
    RATIO_TOLERANCE,
    find_discontinuity,
)


def _series(*closes):
    return [float(c) for c in closes]


# The real rows, from docs/audit/price-series-discontinuities.md.
PRODUCTION_CASES = [
    ("CDSL", 261.75, 131.07, 2.0),
    ("CGCL", 769.65, 193.73, 4.0),
    ("MOTILALOFS", 1240.85, 315.01, 4.0),
    ("PARAS", 1008.20, 502.90, 2.0),
    ("SAMMAANCAP", 347.73, 228.11, 1.5),
    ("TDPOWERSYS", 1507.50, 767.40, 2.0),
    ("TRENT", 4279.00, 2864.93, 1.5),
    ("ZEEL", 235.00, 155.95, 1.5),
]


@pytest.mark.parametrize("symbol,previous,current,nominal", PRODUCTION_CASES)
def test_every_production_discontinuity_is_caught(symbol, previous, current, nominal):
    found = find_discontinuity(_series(previous - 1, previous, current, current + 1))
    assert found is not None, f"{symbol} would still be scored"
    assert found.matched == nominal


def test_a_clean_series_is_not_flagged():
    assert find_discontinuity(_series(100, 101, 99, 103, 102, 105)) is None


def test_a_steady_climb_is_not_flagged():
    """A stock that doubles over months must not look like a 2:1 split."""
    closes = [100.0 * (1.01 ** i) for i in range(200)]
    assert find_discontinuity(closes) is None


def test_a_large_but_non_ratio_crash_is_not_flagged():
    """-38% in a day is a bad day, not a corporate action. Flagging it would
    silently remove a real stock from the ranking for a year."""
    assert find_discontinuity(_series(100, 62.0, 60)) is None


def test_a_limit_up_move_is_not_flagged():
    assert find_discontinuity(_series(100, 140.0, 142)) is None


def test_a_consolidation_is_caught_in_the_upward_direction():
    """The dangerous direction: an upward step reads as a huge gain and puts
    the stock at the TOP of the momentum ranking."""
    found = find_discontinuity(_series(50, 100.0, 101))
    assert found is not None
    assert found.matched == 2.0


def test_moves_below_the_floor_are_ignored_whatever_their_ratio():
    """The tolerance band around 1.5 must not be reachable by a normal day."""
    small = 100.0 * (1 - (MIN_MOVE_PCT - 5) / 100)
    assert find_discontinuity(_series(100, small)) is None


def test_zero_and_negative_closes_do_not_divide(caplog):
    """Missing data stays NULL upstream, but a 0 that reaches here must not
    raise or be read as an infinite ratio."""
    assert find_discontinuity(_series(0, 100)) is None
    assert find_discontinuity(_series(100, 0)) is None


def test_the_reported_index_points_at_the_second_bar():
    found = find_discontinuity(_series(100, 101, 102, 51.0, 50))
    assert found is not None
    assert found.index == 3
    assert (found.previous, found.current) == (102.0, 51.0)


def test_the_tolerance_boundary_is_where_it_says_it_is():
    inside = 100.0 / (2.0 * (1 - RATIO_TOLERANCE / 2))
    outside = 100.0 / (2.0 * (1 - RATIO_TOLERANCE * 3))
    assert find_discontinuity(_series(100, inside)) is not None
    assert find_discontinuity(_series(100, outside)) is None


# ------------------------------------------------- the gate in the pipeline --

def test_a_split_stock_gets_no_momentum_score(db_session):
    """End to end through _momentum_scores, which is the single chokepoint —
    published signals, momentum leaders, the screener and market_ext all go
    through it, so gating there covers every consumer.
    """
    from datetime import date, timedelta
    from decimal import Decimal

    from app.core.v1_strategy import V1
    from app.models.price_history import PriceHistory
    from app.models.stock import Stock
    from app.services.daily_signals import _momentum_scores

    as_of = date(2026, 9, 9)
    bars = V1.momentum_long_days + 40

    def add(symbol, split_at: int | None):
        stock = Stock(symbol=symbol, is_active=True)
        db_session.add(stock)
        db_session.flush()
        price = 100.0
        for i in range(bars):
            if split_at is not None and i == split_at:
                price /= 2  # a 2:1 the provider never applied to the earlier bars
            price *= 1.001
            db_session.add(PriceHistory(
                stock_id=stock.id, date=as_of - timedelta(days=bars - i),
                close=Decimal(str(round(price, 2))),
            ))
        db_session.flush()
        return stock

    clean_a = add("CLEANA", None)
    clean_b = add("CLEANB", None)
    split = add("SPLITTER", split_at=bars // 2)

    scores = _momentum_scores(db_session, [clean_a.id, clean_b.id, split.id], as_of)

    assert split.id not in scores, "a stock with an unadjusted split was still ranked"
    assert clean_a.id in scores and clean_b.id in scores


def test_live_and_backtest_apply_the_same_gate():
    """daily_signals._momentum_scores documents that its construction is
    identical to backtest_scoring "so a signal published in production matches
    what the backtest would have selected". Gating only one of them breaks the
    property every phase of the research programme rests on, so both call the
    same function and this asserts they still do.
    """
    import inspect

    from app.services import backtest_scoring, daily_signals

    for module in (daily_signals, backtest_scoring):
        source = inspect.getsource(module)
        assert "find_discontinuity" in source, (
            f"{module.__name__} does not apply the corporate-action gate — "
            "live and backtest selection have diverged"
        )
