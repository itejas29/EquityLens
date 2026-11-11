"""A held-over price must never pass as a live one (code audit, 2026-11-11).

Three places let yesterday's price through as today's:
  1. _download_prices stamped the previous session's bars with the fetch time,
     so a market holiday produced a "live" tape of stale quotes.
  2. _marks_with_source called any feed quote "tape", including the frozen
     last-session snapshot.
  3. The AI buy pass judged the entry zone against whatever price it got. The
     zone is built around the previous close, so a stale price is always in it.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from app.core import scheduler
from app.core.market_hours import IST
from app.models.daily_signal import DailySignal
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.models.user import User
from app.services import ai_trading, paper_trading as pt


class _Feed:
    def __init__(self, prices=None, status="closed"):
        self.prices = prices or {}
        self.status = status


def _minute_bars(day: datetime) -> pd.DataFrame:
    idx = pd.DatetimeIndex([day.replace(hour=9, minute=15), day.replace(hour=9, minute=16)])
    frame = pd.DataFrame({"Close": [100.0, 101.0], "Volume": [10, 20]}, index=idx)
    frame.columns = pd.MultiIndex.from_product([["AAA.NS"], frame.columns])
    return frame


def test_bars_from_the_previous_session_are_not_published_as_live(monkeypatch):
    yesterday = datetime.now(IST) - timedelta(days=1)
    monkeypatch.setattr(scheduler.yf, "download", lambda **kw: _minute_bars(yesterday))
    assert scheduler._download_prices(["AAA"]) == {}


def test_todays_bars_are_published_with_the_bars_own_time(monkeypatch):
    today = datetime.now(IST).replace(second=0, microsecond=0)
    monkeypatch.setattr(scheduler.yf, "download", lambda **kw: _minute_bars(today))
    quote = scheduler._download_prices(["AAA"])["AAA"]
    assert quote["price"] == 101.0
    assert datetime.fromisoformat(quote["timestamp"]) == today.replace(hour=9, minute=16)


@pytest.mark.parametrize("status, stamp, expected", [
    ("live", None, pt.MARK_SOURCE_TAPE),
    ("live", "today", pt.MARK_SOURCE_TAPE),
    ("live", "yesterday", pt.MARK_SOURCE_SNAPSHOT),   # carried forward by the tape merge
    ("closed", "today", pt.MARK_SOURCE_SNAPSHOT),     # the frozen last-session snapshot
    ("closed", None, pt.MARK_SOURCE_SNAPSHOT),
])
def test_only_a_quote_from_todays_live_tape_counts_as_tape(status, stamp, expected):
    now = datetime.now(IST)
    quote = {"price": 100.0}
    if stamp:
        quote["timestamp"] = (now if stamp == "today" else now - timedelta(days=1)).isoformat()
    assert pt._quote_source(status, quote) == expected


def test_the_latest_close_skips_a_newest_bar_with_no_close(db_session):
    stock = Stock(symbol="GAP", is_active=True)
    db_session.add(stock)
    db_session.flush()
    db_session.add(PriceHistory(stock_id=stock.id, date=date(2026, 9, 1), close=Decimal("100.00")))
    db_session.add(PriceHistory(stock_id=stock.id, date=date(2026, 9, 2), close=None))
    db_session.flush()
    assert pt._latest_close(db_session, stock.id) == Decimal("100.00")


@pytest.fixture
def shortlist(db_session):
    """An AI account with cash and one shortlisted stock whose zone is 95–105."""
    user = User(name="AI", email="ai@example.com", password_hash="x")
    stock = Stock(symbol="PICK", is_active=True)
    db_session.add_all([user, stock])
    db_session.flush()
    account = pt.get_or_create_account(db_session, user.id)
    day = date(2026, 9, 2)
    db_session.add(PriceHistory(stock_id=stock.id, date=date(2026, 9, 1), close=Decimal("100.00")))
    db_session.add(DailySignal(
        date=day, stock_id=stock.id, rank=1, overall_score=Decimal("90"), signal="ACCUMULATE",
        reference_close=Decimal("100"), reference_date=date(2026, 9, 1), entry_low=Decimal("95"),
        entry_high=Decimal("105"), stop_loss=Decimal("80"), target_price=Decimal("140"),
        risk_reward=Decimal("1.67"), stop_method="atr", rationale=[]))
    db_session.flush()
    return account, user.id, day


def test_the_buy_pass_does_not_enter_on_a_held_over_price(db_session, shortlist, monkeypatch):
    account, user_id, day = shortlist
    # The frozen snapshot holds yesterday's close, which sits inside the zone.
    monkeypatch.setattr(pt, "get_price_feed", lambda: _Feed({"PICK": {"price": 100.0}}, status="closed"))
    bought, skipped = ai_trading._buy_pass(db_session, account, user_id, day, {"exposure": 1.0})
    assert bought == []
    assert skipped == [{"symbol": "PICK", "reason": "no live quote"}]


def test_the_buy_pass_enters_on_a_live_in_zone_quote(db_session, shortlist, monkeypatch):
    account, user_id, day = shortlist
    monkeypatch.setattr(pt, "get_price_feed", lambda: _Feed({"PICK": {"price": 101.0}}, status="live"))
    bought, skipped = ai_trading._buy_pass(db_session, account, user_id, day, {"exposure": 1.0})
    assert [b["symbol"] for b in bought] == ["PICK"]
    assert skipped == []


def test_a_refused_order_is_recorded_not_dropped(db_session, shortlist, monkeypatch):
    account, user_id, day = shortlist
    monkeypatch.setattr(pt, "get_price_feed", lambda: _Feed({"PICK": {"price": 101.0}}, status="live"))

    def refuse(*args, **kwargs):
        raise pt.PaperTradingError("Insufficient cash for this trade")

    monkeypatch.setattr(ai_trading, "buy", refuse)
    bought, skipped = ai_trading._buy_pass(db_session, account, user_id, day, {"exposure": 1.0})
    assert bought == []
    assert skipped == [{"symbol": "PICK", "reason": "Insufficient cash for this trade"}]
