"""Survivorship bias, and the delisting hole that has to be closed before it
can be turned off.

Point-in-time in this engine applies to the DATA, not to universe MEMBERSHIP.
The default universe is `Stock.is_active == True` — today's survivors,
projected backwards across the whole window. Measured against production on
2026-09-10: 67 inactive stocks holding 112,385 bars from 2016-08-16 to
2026-08-28 are excluded from every backtest, while the active universe itself
grows from 315 names with data in 2016 to 500 in 2026.

Before `include_inactive` could be offered at all, the daily loop had a hole
that would have made an unbiased run look BETTER than a biased one: a position
whose stock stops having bars was held forever, valued at its last traded price
on every remaining day and then "sold" there at the end.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.services.backtest import STALE_POSITION_EXIT_DAYS, BacktestConfig


def _stock(db_session, symbol, *, active, first, last, price=100.0):
    stock = Stock(symbol=symbol, is_active=active, sector="Tech")
    db_session.add(stock)
    db_session.flush()
    day = first
    while day <= last:
        if day.weekday() < 5:
            db_session.add(PriceHistory(
                stock_id=stock.id, date=day,
                open=Decimal(str(price)), high=Decimal(str(price * 1.01)),
                low=Decimal(str(price * 0.99)), close=Decimal(str(price)), volume=100000,
            ))
            price *= 1.001
        day += timedelta(days=1)
    db_session.flush()
    return stock


def _run(db_session, monkeypatch, **overrides):
    """Run a real backtest over a synthetic universe.

    The NIFTY series is monkeypatched rather than fetched: run_backtest derives
    its trading calendar from it, and a test that reaches the network is not a
    test of this engine.
    """
    import pandas as pd

    from app.services import backtest as bt

    calendar = pd.date_range("2026-01-01", "2026-06-30", freq="B")
    bench = pd.DataFrame({
        "date": [d.date() for d in calendar],
        "close": [10000.0 * (1.0005 ** i) for i in range(len(calendar))],
    })
    monkeypatch.setattr(bt, "fetch_price_history", lambda *a, **k: bench)

    cfg = BacktestConfig(
        start_date=date(2026, 1, 1), end_date=date(2026, 6, 30),
        initial_capital=1_000_000.0, risk_appetite="moderate", **overrides,
    )
    return bt.run_backtest(db_session, cfg)


def test_the_default_universe_is_survivors_only(db_session, monkeypatch):
    """Pinning the biased default, because it IS the default and a reader
    should not have to infer it from a query buried in run_backtest."""
    _stock(db_session, "ALIVE", active=True, first=date(2025, 1, 1), last=date(2026, 6, 30))
    _stock(db_session, "GONE", active=False, first=date(2025, 1, 1), last=date(2026, 2, 2))

    result = _run(db_session, monkeypatch)

    traded = {t.symbol for t in result.trade_log}
    assert "GONE" not in traded, "a delisted name entered a survivors-only backtest"


def test_include_inactive_lets_a_delisted_name_into_the_universe(db_session, monkeypatch):
    _stock(db_session, "ALIVE", active=True, first=date(2025, 1, 1), last=date(2026, 6, 30))
    _stock(db_session, "GONE", active=False, first=date(2025, 1, 1), last=date(2026, 2, 2))

    biased = _run(db_session, monkeypatch)
    unbiased = _run(db_session, monkeypatch, include_inactive=True)

    assert "GONE" not in {t.symbol for t in biased.trade_log}
    assert "GONE" in {t.symbol for t in unbiased.trade_log}, (
        "include_inactive did not widen the universe"
    )


def test_a_position_in_a_delisted_name_is_closed_not_carried(db_session, monkeypatch):
    """The hole this had to close first.

    A position whose stock stops having bars used to be held forever — valued
    at its last traded price on every remaining day, then 'sold' there at the
    end. With include_inactive that would fire constantly and make an
    unbiased run look BETTER than the biased one it was meant to correct.
    """
    _stock(db_session, "ALIVE", active=True, first=date(2025, 1, 1), last=date(2026, 6, 30))
    _stock(db_session, "GONE", active=False, first=date(2025, 1, 1), last=date(2026, 2, 2))

    result = _run(db_session, monkeypatch, include_inactive=True)

    gone_trades = [t for t in result.trade_log if t.symbol == "GONE"]
    assert gone_trades, "GONE never traded, so the exit path was not exercised"
    assert any(t.exit_reason == "delisted" for t in gone_trades), (
        f"a dead series produced no delisted exit: {[t.exit_reason for t in gone_trades]}"
    )
    # And it was closed within the grace period, not carried to the end date.
    delisted = next(t for t in gone_trades if t.exit_reason == "delisted")
    assert (delisted.exit_date - date(2026, 2, 2)).days <= STALE_POSITION_EXIT_DAYS + 5
    assert delisted.exit_date < date(2026, 6, 30)


# --------------------------------------------------- the delisting exit --

def test_a_series_that_stops_forces_an_exit(db_session, monkeypatch):
    """The hole: no bar meant `hold, can't evaluate triggers`, forever.

    With only active stocks that almost never fired. With include_inactive it
    would fire constantly — and holding a dead position at its last traded
    price, then selling it there, makes an unbiased run look better than the
    biased one it was meant to correct.
    """
    from app.services import backtest as bt

    gone = _stock(db_session, "GONE", active=False,
                  first=date(2026, 1, 1), last=date(2026, 2, 2), price=100.0)

    frame = bt._load_ohlcv_df(db_session, gone.id)
    assert not frame.empty
    last_bar = frame["date"].max()

    # A position opened while the series was alive, evaluated well after it
    # stopped, must be closed rather than carried.
    stale_by = last_bar + timedelta(days=STALE_POSITION_EXIT_DAYS + 1)
    assert (stale_by - last_bar).days >= STALE_POSITION_EXIT_DAYS


def test_the_grace_period_tolerates_a_holiday_stretch():
    """A gap is normal; a stop is not. The threshold has to sit above any
    legitimate NSE closure and below a month, or it either closes live
    positions on holidays or carries dead ones for a quarter."""
    assert 7 < STALE_POSITION_EXIT_DAYS <= 31


def test_a_delisted_exit_is_labelled_not_hidden(db_session):
    """exit_reason="delisted" exists so the share of a result that rests on
    the last-known-close assumption is countable from the trade log."""
    import inspect

    from app.services import backtest as bt

    source = inspect.getsource(bt.run_backtest)
    assert '"delisted"' in source
    # And it must exit at the last known close, not at zero — assuming a
    # wipeout would be inventing a number the data does not support.
    assert 'last_known_close.get(pos.stock_id, pos.entry_price), "delisted"' in source


def test_a_delisted_name_that_ranks_well_actually_gets_traded(db_session, monkeypatch):
    """The property scripts/phase21_survivorship.py depends on.

    include_inactive only measures anything if a delisted name can actually be
    SELECTED, not merely be present in the universe. Momentum ranks are
    percentile cuts, so this gives GONE the strongest trajectory: if it is in
    the pool it must be picked, and if the two arms still match the membership
    wiring is broken rather than the strategy being indifferent.

    Worth pinning because the first version of the Phase 21 smoke test gave all
    three stocks identical price paths. Their momentum tied, the percentile cut
    dropped GONE, both arms returned the same number — and that looked exactly
    like include_inactive doing nothing. A fixture tidier than reality hides
    the thing it is testing.
    """
    _stock(db_session, "ALIVE1", active=True, first=date(2025, 1, 1), last=date(2026, 6, 30))
    _stock(db_session, "ALIVE2", active=True, first=date(2025, 1, 1), last=date(2026, 6, 30))
    gone = _stock(db_session, "GONE", active=False,
                  first=date(2025, 1, 1), last=date(2026, 2, 2))

    # Give GONE a far stronger trajectory than the survivors.
    from decimal import Decimal

    from app.models.price_history import PriceHistory
    price = 100.0
    for row in (db_session.query(PriceHistory)
                .filter(PriceHistory.stock_id == gone.id)
                .order_by(PriceHistory.date).all()):
        price *= 1.003
        row.close = Decimal(str(round(price, 2)))
        row.high = Decimal(str(round(price * 1.01, 2)))
        row.low = Decimal(str(round(price * 0.99, 2)))
        row.open = Decimal(str(round(price, 2)))
    db_session.flush()

    biased = _run(db_session, monkeypatch)
    unbiased = _run(db_session, monkeypatch, include_inactive=True)

    assert "GONE" not in {t.symbol for t in biased.trade_log}
    assert "GONE" in {t.symbol for t in unbiased.trade_log}, (
        "a delisted name with the best momentum was still never traded"
    )
    assert {t.symbol for t in biased.trade_log} != {t.symbol for t in unbiased.trade_log}
