"""Two ways the forward record was being damaged, and their guards.

1. A published shortlist could be rewritten. generate_daily_signals deleted
   and reinserted a date's rows, and POST /daily-signals/run accepted any
   date from any registered user — and registration is open.
2. A renamed ticker blocked whole mornings. NSE renamed ICICIPRULI to
   ICICILIFE (same ISIN) in October 2026; `stocks` is keyed by symbol, so the
   old label went stale and the all-or-nothing publish gate skipped 10-28.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest

import app.api.v1.daily_signals as daily_signals_api
import app.services.catalogue as catalogue
from app.core.market_hours import IST
from app.models.daily_signal import DailySignal
from app.models.nse_symbol import NseSymbol
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.services.daily_signals import ShortlistAlreadyPublished, generate_daily_signals

PUBLISHED_DAY = date(2026, 10, 29)  # a Thursday
OLD_ISIN = "INE726G01019"


def _publish(db, day, close="2460.00"):
    stock = Stock(symbol="TCS", is_active=True, sector="IT")
    db.add(stock)
    db.flush()
    row = DailySignal(
        date=day, stock_id=stock.id, rank=1, overall_score=Decimal("70"), signal="BUY",
        reference_close=Decimal(close), reference_date=date(2026, 10, 28),
        entry_low=Decimal("2400"), entry_high=Decimal("2480"), stop_loss=Decimal("2200"),
        target_price=Decimal("2900"), risk_reward=Decimal("1.5"), stop_method="atr", rationale=[],
    )
    db.add(row)
    db.commit()
    return row


def _freeze_api_clock(monkeypatch, hh, mm, day=PUBLISHED_DAY):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)

    monkeypatch.setattr(daily_signals_api, "datetime", FrozenDatetime)


# ------------------------------------------------------------ record lock --

def test_a_published_shortlist_is_never_rebuilt(db_session):
    row = _publish(db_session, PUBLISHED_DAY)

    with pytest.raises(ShortlistAlreadyPublished):
        generate_daily_signals(db_session, PUBLISHED_DAY)

    rows = db_session.query(DailySignal).filter(DailySignal.date == PUBLISHED_DAY).all()
    assert [(r.id, r.reference_close) for r in rows] == [(row.id, Decimal("2460.00"))]


def test_run_endpoint_refuses_a_past_date(client, auth_headers, db_session, monkeypatch):
    _publish(db_session, PUBLISHED_DAY)
    _freeze_api_clock(monkeypatch, 10, 0, day=date(2026, 10, 30))

    resp = client.post("/api/v1/daily-signals/run", params={"date": "2026-10-29"}, headers=auth_headers)

    assert resp.status_code == 409
    assert "after the fact" in resp.json()["error"]["message"]
    assert db_session.query(DailySignal).filter(DailySignal.date == PUBLISHED_DAY).count() == 1


def test_run_endpoint_refuses_to_overwrite_today(client, auth_headers, db_session, monkeypatch):
    row = _publish(db_session, PUBLISHED_DAY)
    _freeze_api_clock(monkeypatch, 10, 0)

    resp = client.post("/api/v1/daily-signals/run", headers=auth_headers)

    assert resp.status_code == 409
    assert "already published" in resp.json()["error"]["message"]
    db_session.expire_all()
    assert [r.id for r in db_session.query(DailySignal).all()] == [row.id]


def test_run_endpoint_refuses_after_the_publish_deadline(client, auth_headers, db_session, monkeypatch):
    _freeze_api_clock(monkeypatch, 15, 30)

    resp = client.post("/api/v1/daily-signals/run", headers=auth_headers)

    assert resp.status_code == 409
    assert "window closed" in resp.json()["error"]["message"]
    assert db_session.query(DailySignal).count() == 0


# ---------------------------------------------------------------- renames --

def _catalogue_entry(db, symbol, isin):
    db.add(NseSymbol(symbol=symbol, company_name=f"{symbol} Ltd", series="EQ", isin=isin))


def _nse_list(*pairs):
    return lambda: [
        {"symbol": s, "company_name": f"{s} Ltd", "series": "EQ", "isin": i, "listed_on": None}
        for s, i in pairs
    ]


def test_a_renamed_ticker_keeps_its_stock_row_and_history(db_session, monkeypatch):
    _catalogue_entry(db_session, "ICICIPRULI", OLD_ISIN)
    stock = Stock(symbol="ICICIPRULI", is_active=True, sector="Insurance")
    db_session.add(stock)
    db_session.flush()
    db_session.add(PriceHistory(stock_id=stock.id, date=date(2026, 10, 27), open=Decimal("600"),
                                high=Decimal("610"), low=Decimal("590"), close=Decimal("605"), volume=1000))
    db_session.commit()
    monkeypatch.setattr(catalogue, "fetch_nse_equity_list", _nse_list(("ICICILIFE", OLD_ISIN), ("TCS", "INE467B01029")))

    report = catalogue.load_catalogue(db_session)
    db_session.commit()

    assert report["renamed"] == ["ICICIPRULI->ICICILIFE"]
    moved = db_session.get(Stock, stock.id)
    assert moved.symbol == "ICICILIFE" and moved.is_active
    assert db_session.query(PriceHistory).filter(PriceHistory.stock_id == stock.id).count() == 1
    assert db_session.query(Stock).count() == 1, "a rename must not create a second stock"


def test_a_delisted_ticker_is_not_renamed(db_session, monkeypatch):
    _catalogue_entry(db_session, "DHFL", "INE202B01012")
    db_session.add(Stock(symbol="DHFL", is_active=True))
    db_session.commit()
    monkeypatch.setattr(catalogue, "fetch_nse_equity_list", _nse_list(("TCS", "INE467B01029")))

    report = catalogue.load_catalogue(db_session)

    assert report["renamed"] == []
    assert db_session.query(Stock).one().symbol == "DHFL"


def test_a_rename_onto_an_existing_stock_is_left_for_a_person(db_session, monkeypatch):
    _catalogue_entry(db_session, "ICICIPRULI", OLD_ISIN)
    db_session.add_all([Stock(symbol="ICICIPRULI", is_active=True), Stock(symbol="ICICILIFE", is_active=False)])
    db_session.commit()
    monkeypatch.setattr(catalogue, "fetch_nse_equity_list", _nse_list(("ICICILIFE", OLD_ISIN)))

    report = catalogue.load_catalogue(db_session)

    assert report["renamed"] == []
    assert sorted(s.symbol for s in db_session.query(Stock).all()) == ["ICICILIFE", "ICICIPRULI"]
