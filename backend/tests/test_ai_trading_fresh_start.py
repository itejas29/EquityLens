"""The ₹15 lakh fresh start (2026-11-13).

The AI cycle moved to a new system account with ₹15,00,000 so the live record
is not a mix of two capital bases. The old account keeps its history, and its
leftover open positions are closed by the next cycle — on a live quote only,
never a held-over price — with exit_reason "retired".
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from app.core.ai_trading_config import AI_TRADER_CAPITAL, RETIRED_AI_TRADER_EMAILS
from app.models.ai_trading_log import AITradingLog
from app.models.paper_trading import PaperTrade
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.models.user import User
from app.services import ai_trading, paper_trading as pt
from app.services.telegram_messages import cycle_message

DAY = date(2026, 11, 16)


class _Feed:
    def __init__(self, prices=None, status="live"):
        self.prices = prices or {}
        self.status = status


def _old_account_with_position(db, symbol="M&M"):
    user = User(name="Old AI", email=RETIRED_AI_TRADER_EMAILS[0], password_hash="x")
    stock = Stock(symbol=symbol, is_active=True)
    db.add_all([user, stock])
    db.flush()
    account = pt.get_or_create_account(db, user.id)
    db.add(PriceHistory(stock_id=stock.id, date=date(2026, 11, 13), close=Decimal("100.00")))
    db.add(PaperTrade(
        account_id=account.id, stock_id=stock.id, side="buy", quantity=10, price=Decimal("90.00"),
        cost_basis=Decimal("900.00"), status="open", stop_loss=Decimal("80.00"),
        target_price=Decimal("140.00"), executed_at=datetime(2026, 11, 1, tzinfo=timezone.utc)))
    db.flush()
    return account, stock


def test_the_new_account_opens_with_fifteen_lakh(db_session):
    account = ai_trading.get_ai_trader_account(db_session)
    assert account.virtual_capital == AI_TRADER_CAPITAL == Decimal("1500000.00")
    assert account.cash == AI_TRADER_CAPITAL
    # And an ordinary user account still opens with the default ₹10L.
    user = User(name="Someone", email="someone@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    assert pt.get_or_create_account(db_session, user.id).virtual_capital == Decimal("1000000.00")


def test_the_old_accounts_position_is_closed_on_a_live_quote(db_session, monkeypatch):
    old_account, stock = _old_account_with_position(db_session)
    monkeypatch.setattr(pt, "get_price_feed", lambda: _Feed({"M&M": {"price": 110.0}}, status="live"))
    monkeypatch.setattr(ai_trading, "compute_market_regime", lambda db, d: {"regime": "bull", "exposure": 1.0})

    result = ai_trading.run_ai_trading_cycle(db_session, DAY)
    db_session.flush()

    assert [r["symbol"] for r in result.retired] == ["M&M"]
    trade = db_session.query(PaperTrade).filter(PaperTrade.account_id == old_account.id).one()
    assert (trade.status, trade.exit_reason) == ("closed", "retired")
    entry = db_session.query(AITradingLog).filter(AITradingLog.symbol == "M&M").one()
    assert "retired" in entry.reason.lower()
    # The new account is untouched: full cash, no positions.
    new_account = ai_trading.get_ai_trader_account(db_session)
    assert new_account.id != old_account.id
    assert new_account.cash == AI_TRADER_CAPITAL


def test_without_a_live_quote_the_old_position_stays_open_for_tomorrow(db_session, monkeypatch):
    old_account, stock = _old_account_with_position(db_session)
    # The frozen snapshot holds yesterday's close — that must NOT close it.
    monkeypatch.setattr(pt, "get_price_feed", lambda: _Feed({"M&M": {"price": 100.0}}, status="closed"))
    monkeypatch.setattr(ai_trading, "compute_market_regime", lambda db, d: {"regime": "bull", "exposure": 1.0})

    result = ai_trading.run_ai_trading_cycle(db_session, DAY)

    assert result.retired == []
    trade = db_session.query(PaperTrade).filter(PaperTrade.account_id == old_account.id).one()
    assert trade.status == "open"


def test_the_buy_message_carries_reason_size_and_levels():
    result = ai_trading.AITradingCycleResult(
        as_of=DAY, regime="bull", rebalanced=True,
        bought=[{"symbol": "M&M", "quantity": 5, "price": Decimal("100.00"), "stop": Decimal("80"),
                 "target": Decimal("140"), "rank": 1, "entry_low": Decimal("95"),
                 "entry_high": Decimal("105"), "risk_reward": Decimal("1.6667")}],
        retired=[{"symbol": "ARE&M", "reason": "retired", "pnl": Decimal("200.00"), "quantity": 10,
                  "price": Decimal("110.00"), "pnl_pct": Decimal("22.22"), "days": 15}])
    text = cycle_message(result, Decimal("1500000"))

    for part in ("🟢 AUTO BUY: M&amp;M × 5 @ ₹100.00 = ₹500.00",
                 "Reason: rank 1 momentum pick, in entry zone ₹95.00–₹105.00",
                 "Stop ₹80.00 | Target ₹140.00 | R:R 1.67",
                 "♻️ RETIRED ACCOUNT SELL: ARE&amp;M × 10 @ ₹110.00",
                 "+₹200.00 (+22.22%)",
                 "Account equity: ₹1,500,000.00"):
        assert part in text
    assert text.count("&") == text.count("&amp;")


def test_a_fresh_accounts_first_run_counts_as_its_rebalance(db_session, monkeypatch):
    """November's rebalance was already consumed by the old account's runs, so
    without this rule the new account would sit fully in cash until Dec 1.
    A backtest started mid-month rebalances on its first session; so does a
    fresh account."""
    from app.models.ai_trading_run import AITradingRun
    from app.models.daily_signal import DailySignal

    ai_trading.get_ai_trader_account(db_session)
    # A completed run earlier this month: the monthly rule alone says "no".
    db_session.add(AITradingRun(run_date=date(2026, 11, 2), status="complete", regime="bull"))
    stock = Stock(symbol="PICK", is_active=True)
    db_session.add(stock)
    db_session.flush()
    db_session.add(PriceHistory(stock_id=stock.id, date=date(2026, 11, 13), close=Decimal("100.00")))
    db_session.add(DailySignal(
        date=DAY, stock_id=stock.id, rank=1, overall_score=Decimal("90"), signal="ACCUMULATE",
        reference_close=Decimal("100"), reference_date=date(2026, 11, 13), entry_low=Decimal("95"),
        entry_high=Decimal("105"), stop_loss=Decimal("80"), target_price=Decimal("140"),
        risk_reward=Decimal("1.67"), stop_method="atr", rationale=[]))
    db_session.flush()
    monkeypatch.setattr(pt, "get_price_feed", lambda: _Feed({"PICK": {"price": 100.0}}, status="live"))
    monkeypatch.setattr(ai_trading, "compute_market_regime", lambda db, d: {"regime": "bull", "exposure": 1.0})

    result = ai_trading.run_ai_trading_cycle(db_session, DAY)

    assert result.rebalanced is True
    assert [b["symbol"] for b in result.bought] == ["PICK"]

    # With a position on the books, the monthly cadence governs again.
    db_session.add(AITradingRun(run_date=DAY, status="complete", regime="bull"))
    db_session.flush()
    result2 = ai_trading.run_ai_trading_cycle(db_session, date(2026, 11, 17))
    assert result2.rebalanced is False
