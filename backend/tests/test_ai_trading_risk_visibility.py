"""A risk check that could not run must not look like one that passed.

The AI cycle evaluates stop-loss, target and horizon against _mark_prices().
Two failure modes used to be invisible at the call site:

  1. No price at all for a held position -> `continue`, silently. The stop did
     not fire because it was never tested.
  2. No live quote, so the mark falls back to the newest stored daily bar. The
     cycle runs at ~09:20 IST and today's bar is not written until 20:00, so
     that bar is YESTERDAY'S close — a stop that gapped through overnight is
     compared against a price from before the gap.

Both are still the right behaviour (a stale price beats no price; you cannot
act without one). What changed is that the cycle now reports them.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.paper_trading import PaperTrade
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.models.user import User
from app.services import ai_trading, paper_trading as pt


class _Feed:
    def __init__(self, prices=None, status="closed"):
        self.prices = prices or {}
        self.status = status


@pytest.fixture
def account(db_session):
    user = User(name="AI", email="ai@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    acct = pt.get_or_create_account(db_session, user.id)
    return acct, user.id


def _held(db_session, account, symbol, *, stop: str, with_bar: bool):
    """One open position, optionally with a stored daily bar behind it."""
    stock = Stock(symbol=symbol, is_active=True)
    db_session.add(stock)
    db_session.flush()
    if with_bar:
        db_session.add(PriceHistory(stock_id=stock.id, date=date(2026, 9, 1), close=Decimal("100.00")))
    trade = PaperTrade(
        account_id=account.id, stock_id=stock.id, side="buy", quantity=10,
        price=Decimal("100.00"), cost_basis=Decimal("1000.00"), status="open",
        stop_loss=Decimal(stop), executed_at=datetime.now(timezone.utc),
    )
    db_session.add(trade)
    db_session.flush()
    return stock, trade


def test_a_position_with_no_price_is_reported_not_skipped(db_session, account, monkeypatch):
    acct, user_id = account
    _held(db_session, acct, "NOPRICE", stop="90.00", with_bar=False)
    monkeypatch.setattr(pt, "get_price_feed", lambda: _Feed())

    sold, unpriced, stale = ai_trading._sell_pass(
        db_session, acct, user_id, date(2026, 9, 2), {"exposure": 1.0}, is_rebalance=False
    )

    assert sold == []
    assert unpriced == ["NOPRICE"], "an unevaluated stop must be named, not silently skipped"
    assert stale == []


def test_a_position_marked_off_the_previous_close_is_flagged(db_session, account, monkeypatch):
    acct, user_id = account
    _held(db_session, acct, "STALE", stop="90.00", with_bar=True)
    # Empty tape: the mark falls back to the stored bar.
    monkeypatch.setattr(pt, "get_price_feed", lambda: _Feed())

    sold, unpriced, stale = ai_trading._sell_pass(
        db_session, acct, user_id, date(2026, 9, 2), {"exposure": 1.0}, is_rebalance=False
    )

    assert unpriced == []
    assert stale == ["STALE"]


def test_a_live_quote_is_not_flagged(db_session, account, monkeypatch):
    acct, user_id = account
    _held(db_session, acct, "LIVE", stop="90.00", with_bar=True)
    monkeypatch.setattr(pt, "get_price_feed", lambda: _Feed({"LIVE": {"price": 105.0}}, status="live"))

    sold, unpriced, stale = ai_trading._sell_pass(
        db_session, acct, user_id, date(2026, 9, 2), {"exposure": 1.0}, is_rebalance=False
    )

    assert (unpriced, stale) == ([], [])


def test_a_live_quote_below_the_stop_still_sells(db_session, account, monkeypatch):
    """The degradation reporting must not have changed what the cycle does."""
    acct, user_id = account
    _held(db_session, acct, "DROP", stop="90.00", with_bar=True)
    monkeypatch.setattr(pt, "get_price_feed", lambda: _Feed({"DROP": {"price": 85.0}}, status="live"))

    sold, unpriced, stale = ai_trading._sell_pass(
        db_session, acct, user_id, date(2026, 9, 2), {"exposure": 1.0}, is_rebalance=False
    )

    assert [s["symbol"] for s in sold] == ["DROP"]
    assert sold[0]["reason"] == "stop"
    assert (unpriced, stale) == ([], [])


def test_the_cycle_result_reports_degradation(db_session, account, monkeypatch):
    acct, user_id = account
    _held(db_session, acct, "NOPRICE", stop="90.00", with_bar=False)
    monkeypatch.setattr(pt, "get_price_feed", lambda: _Feed())
    monkeypatch.setattr(ai_trading, "get_ai_trader_account", lambda db: acct)
    monkeypatch.setattr(ai_trading, "compute_market_regime", lambda db, d: {"regime": "bull", "exposure": 1.0})

    result = ai_trading.run_ai_trading_cycle(db_session, date(2026, 9, 2))

    assert result.unpriced == ["NOPRICE"]
    assert result.risk_checks_degraded is True


def test_a_clean_cycle_is_not_marked_degraded(db_session, account, monkeypatch):
    acct, user_id = account
    _held(db_session, acct, "FINE", stop="90.00", with_bar=True)
    monkeypatch.setattr(pt, "get_price_feed", lambda: _Feed({"FINE": {"price": 101.0}}, status="live"))
    monkeypatch.setattr(ai_trading, "get_ai_trader_account", lambda db: acct)
    monkeypatch.setattr(ai_trading, "compute_market_regime", lambda db, d: {"regime": "bull", "exposure": 1.0})

    result = ai_trading.run_ai_trading_cycle(db_session, date(2026, 9, 2))

    assert result.risk_checks_degraded is False


def test_the_notification_names_the_unevaluated_positions(db_session):
    from app.core.scheduler import _ai_trading_notification_text

    result = ai_trading.AITradingCycleResult(
        as_of=date(2026, 9, 2), regime="bull", rebalanced=False,
        unpriced=["NOPRICE"], stale_marked=["STALE"],
    )
    text = _ai_trading_notification_text(result, Decimal("1000000.00"))

    assert "NO PRICE for NOPRICE" in text
    assert "STALE checked against the previous close" in text
