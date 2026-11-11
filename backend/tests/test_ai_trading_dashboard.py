"""The AI paper account's switch, decision log, dashboard endpoints and
Telegram texts.

The switch is opt-in and pauses the whole cycle; every decision a cycle takes
leaves a log row; messages never carry a raw "&" (Telegram rejects the whole
message) and never fill in a figure they were not given.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.ai_trading_log import AITradingLog
from app.models.daily_signal import DailySignal
from app.models.paper_trading import PaperEquitySnapshot, PaperTrade
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.services import ai_trading, paper_trading as pt, telegram_messages as tm

DAY = date(2026, 9, 2)
BULL = {"regime": "bull", "exposure": 1.0, "nifty_close": 25000.0, "nifty_200dma": 24000.0}
BEAR = {"regime": "bear", "exposure": 0.25, "nifty_close": 22936.75, "nifty_200dma": 24056.78}


class _Feed:
    def __init__(self, prices=None, status="live"):
        self.prices = prices or {}
        self.status = status


def _stock(db, symbol, close="100.00"):
    stock = Stock(symbol=symbol, is_active=True)
    db.add(stock)
    db.flush()
    db.add(PriceHistory(stock_id=stock.id, date=date(2026, 9, 1), close=Decimal(close)))
    db.flush()
    return stock


def _signal(db, stock, rank, low="95", high="105"):
    db.add(DailySignal(
        date=DAY, stock_id=stock.id, rank=rank, overall_score=Decimal("90"), signal="ACCUMULATE",
        reference_close=Decimal("100"), reference_date=date(2026, 9, 1), entry_low=Decimal(low),
        entry_high=Decimal(high), stop_loss=Decimal("80"), target_price=Decimal("140"),
        risk_reward=Decimal("1.67"), stop_method="atr", rationale=[]))
    db.flush()


# ------------------------------------------------------------------ switch --

def test_the_switch_is_off_until_someone_turns_it_on(db_session):
    assert ai_trading.is_enabled(db_session) is False
    ai_trading.set_enabled(db_session, True, "me@example.com", DAY)
    db_session.flush()
    assert ai_trading.is_enabled(db_session) is True
    entry = db_session.query(AITradingLog).one()
    assert (entry.action, entry.run_date) == ("toggle", DAY)
    assert "ON by me@example.com" in entry.reason
    ai_trading.set_enabled(db_session, False, None, DAY)
    db_session.flush()
    assert ai_trading.is_enabled(db_session) is False


# -------------------------------------------------------------------- log --

def test_a_cycle_logs_every_decision_it_takes(db_session, monkeypatch):
    account = ai_trading.get_ai_trader_account(db_session)
    held, buy, above, quiet = (_stock(db_session, s) for s in ("HELD", "BUY", "ABOVE", "QUIET"))
    db_session.add(PaperTrade(
        account_id=account.id, stock_id=held.id, side="buy", quantity=10, price=Decimal("100.00"),
        cost_basis=Decimal("1000.00"), status="open", stop_loss=Decimal("90.00"),
        target_price=Decimal("140.00"), executed_at=datetime(2026, 9, 1, tzinfo=timezone.utc)))
    for rank, stock in enumerate((held, buy, above, quiet), 1):
        _signal(db_session, stock, rank)
    monkeypatch.setattr(pt, "get_price_feed", lambda: _Feed({
        "HELD": {"price": 101.0}, "BUY": {"price": 100.0}, "ABOVE": {"price": 120.0}}))
    monkeypatch.setattr(ai_trading, "compute_market_regime", lambda db, d: BULL)

    result = ai_trading.run_ai_trading_cycle(db_session, DAY)
    db_session.flush()

    assert [b["symbol"] for b in result.bought] == ["BUY"]
    log = {(r.action, r.symbol): r.reason for r in db_session.query(AITradingLog).all()}
    assert "between stop 90.00 and target 140.00" in log[("hold", "HELD")]
    assert log[("skip", "HELD")] == "Already held"
    assert "in entry zone" in log[("buy", "BUY")]
    assert "above the entry zone" in log[("skip", "ABOVE")]
    assert log[("skip", "QUIET")] == "No live quote: entry zone not evaluated"


def test_a_non_rebalance_day_says_why_there_were_no_entries(db_session, monkeypatch):
    from app.models.ai_trading_run import AITradingRun

    ai_trading.get_ai_trader_account(db_session)
    db_session.add(AITradingRun(run_date=date(2026, 9, 1), status="complete", regime="bull"))
    db_session.flush()
    monkeypatch.setattr(pt, "get_price_feed", lambda: _Feed())
    monkeypatch.setattr(ai_trading, "compute_market_regime", lambda db, d: BEAR)

    ai_trading.run_ai_trading_cycle(db_session, DAY)
    db_session.flush()

    reasons = {r.action: r.reason for r in db_session.query(AITradingLog).all()}
    assert reasons["skip"] == "No new entries: not the month's rebalance day"
    assert reasons["regime"].startswith("Regime changed NORMAL → CAUTION")


# -------------------------------------------------------------- endpoints --

def test_the_dashboard_endpoints(client, auth_headers, db_session, monkeypatch):
    from app.api.v1 import ai_trading as api

    monkeypatch.setattr(api, "compute_market_regime", lambda db, d: BEAR)
    monkeypatch.setattr(pt, "get_price_feed", lambda: _Feed({"OPEN": {"price": 110.0}}))
    account = ai_trading.get_ai_trader_account(db_session)
    open_stock, done = _stock(db_session, "OPEN"), _stock(db_session, "DONE")
    now = datetime.now(timezone.utc)
    db_session.add(PaperTrade(
        account_id=account.id, stock_id=open_stock.id, side="buy", quantity=10, price=Decimal("100.00"),
        cost_basis=Decimal("1000.00"), status="open", stop_loss=Decimal("88.00"),
        target_price=Decimal("132.00"), executed_at=now - timedelta(days=3)))
    db_session.add(PaperTrade(
        account_id=account.id, stock_id=done.id, side="buy", quantity=10, price=Decimal("100.00"),
        cost_basis=Decimal("1000.00"), status="closed", exit_price=Decimal("90.00"), pnl=Decimal("-100.00"),
        exit_reason="stop", executed_at=now - timedelta(days=9), exit_at=now - timedelta(days=2)))
    db_session.commit()

    status = client.get("/api/v1/ai-trading/status", headers=auth_headers).json()
    assert status["enabled"] is False and status["paper_only"] is True
    assert status["regime"] == {"label": "CAUTION", "nifty_close": 22936.75, "nifty_200dma": 24056.78,
                                "distance_pct": -4.66}
    assert (status["open_positions"], status["last_run"]) == (1, None)

    on = client.post("/api/v1/ai-trading/toggle", json={"enabled": True}, headers=auth_headers).json()
    assert on["enabled"] is True
    log = client.get("/api/v1/ai-trading/log", headers=auth_headers).json()
    assert log["total"] == 1 and log["entries"][0]["action"] == "toggle"

    (pos,) = client.get("/api/v1/ai-trading/positions", headers=auth_headers).json()
    assert (pos["symbol"], pos["current_price"], pos["days_held"]) == ("OPEN", 110.0, 3)
    assert pos["stop_distance_pct"] == pytest.approx(20.0)
    assert pos["target_distance_pct"] == pytest.approx(20.0)

    hist = client.get("/api/v1/ai-trading/history", headers=auth_headers).json()
    assert hist["total"] == 1 and hist["trades"][0]["pnl_pct"] == pytest.approx(-10.0)
    assert hist["stats"]["win_rate_pct"] == 0.0 and hist["stats"]["profit_factor"] == 0.0
    assert client.get("/api/v1/ai-trading/history?exit_reason=target", headers=auth_headers).json()["total"] == 0
    assert client.get("/api/v1/ai-trading/status").status_code == 401


# --------------------------------------------------------------- messages --

def test_the_shortlist_message_states_regime_levels_and_disclaimer():
    text = tm.shortlist_message(DAY, BEAR, [{
        "symbol": "M&M", "score": Decimal("91.4"), "entry_low": Decimal("95"), "entry_high": Decimal("105"),
        "stop_loss": Decimal("80"), "target_price": Decimal("140"), "risk_reward": Decimal("1.6667")}])
    assert "Regime: CAUTION (NIFTY 4.66% below its 200-day average)" in text
    assert "1. <b>M&amp;M</b> — Score 91 — Buy ₹95.00–₹105.00" in text
    assert "Stop ₹80.00 | Target ₹140.00 | R:R 1.67" in text
    assert text.endswith(tm.DISCLAIMER)
    assert "No stock passed" in tm.shortlist_message(DAY, {"regime": "unknown"}, [])


def test_the_cycle_message_has_no_raw_ampersand_and_lists_skips():
    result = SimpleNamespace(
        as_of=DAY, regime="bull", rebalanced=True, unpriced=[], stale_marked=[],
        bought=[{"symbol": "M&M", "quantity": 5, "price": Decimal("100"), "stop": Decimal("80"),
                 "target": Decimal("140")}],
        sold=[{"symbol": "TCS", "reason": "stop", "pnl": Decimal("-250.5"), "quantity": 3,
               "price": Decimal("90"), "pnl_pct": Decimal("-8.35"), "days": 12}],
        entries_skipped=[{"symbol": "INFY", "reason": "no live quote"}])
    text = tm.cycle_message(result, Decimal("1000000"))
    assert text.count("&") == text.count("&amp;")
    for part in ("🟢 AUTO BUY: M&amp;M × 5 @ ₹100.00", "Stop ₹80.00 | Target ₹140.00",
                 "🔴 AUTO SELL: TCS × 3 @ ₹90.00", "P&amp;L: −₹250.50 (-8.35%)", "Held: 12 days",
                 "Account equity: ₹1,000,000.00", "Skipped (no live price): INFY"):
        assert part in text


def test_the_regime_change_message_quotes_index_levels_and_the_measured_evidence():
    text = tm.regime_change_message("bull", BEAR)
    assert "REGIME CHANGE: NORMAL → CAUTION" in text
    assert "NIFTY closed at 22,936.75; its 200-day average is 24,056.78." in text
    assert "−26% from −45%" in text and "4 points a year" in text


def test_the_weekly_summary_leaves_out_what_it_was_not_given(db_session):
    account = ai_trading.get_ai_trader_account(db_session)
    friday = date(2026, 9, 4)
    assert ai_trading.weekly_summary(db_session, friday) is None  # no snapshot yet

    for day, equity, cum, nifty in ((date(2026, 8, 28), "1000000", "0", "0"),
                                    (friday, "1020000", "2", "1")):
        db_session.add(PaperEquitySnapshot(
            account_id=account.id, date=day, cash=Decimal(equity), portfolio_value=Decimal("0"),
            total_equity=Decimal(equity), cumulative_return=Decimal(cum), nifty_return=Decimal(nifty),
            drawdown=Decimal("0")))
    db_session.flush()
    summary = ai_trading.weekly_summary(db_session, friday)
    assert summary["week_pct"] == pytest.approx(Decimal("2"))
    assert summary["nifty_week_pct"] == pytest.approx(Decimal("1"))
    text = tm.weekly_summary_message(summary)
    assert "Paper portfolio: ₹1,020,000.00 (+2.00% this week)" in text
    assert "Trades this week: 0 buys, 0 sells" in text
    assert "Best:" not in text and "Worst:" not in text
    assert "Since inception: portfolio +2.00% vs NIFTY +1.00%" in text
