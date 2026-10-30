"""A shortlist dated D is a pre-market call: it may use only sessions that
closed before D, and it may only be published while D's session is still open.

Both properties failed in production. On 2026-09-15 and 2026-10-22 the 09:15
publish was skipped (incomplete pipeline run), the catch-up loop kept retrying,
and at ~20:05 — once the nightly ingest had stored that day's own bars and
recorded a complete run dated that day — it accepted that run and published a
list dated D built from D's close. Each one also duplicated the next morning's
list, which was built from the same bars.
"""

import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core import scheduler
from app.core.market_hours import IST
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.services.daily_signals import _gather_candidates

CALL_DATE = date(2026, 10, 28)  # a Wednesday
PREVIOUS_SESSION = date(2026, 10, 27)

# The latest-indicator query uses DISTINCT ON, which is Postgres-only; SQLite
# ignores it with a deprecation warning (escalated to an error by pytest.ini).
# Scoped to the generator tests rather than global, and harmless here: these
# fixtures store no indicator rows, so there is nothing for DISTINCT ON to pick.
sqlite_distinct_on = pytest.mark.filterwarnings(
    "ignore:DISTINCT ON is currently supported only by the PostgreSQL dialect"
)


# ------------------------------------------------------------- generator --

def _business_days_before(day: date, n: int) -> list[date]:
    out, d = [], day
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return list(reversed(out))


def _add_stock(db, symbol, sector, daily_drift, call_day_move=None):
    stock = Stock(symbol=symbol, is_active=True, sector=sector)
    db.add(stock)
    db.flush()
    price = 100.0
    closes = {}
    for d in _business_days_before(CALL_DATE, 320):
        price *= 1 + daily_drift
        closes[d] = round(price, 2)
        db.add(PriceHistory(
            stock_id=stock.id, date=d,
            open=Decimal(str(closes[d])), high=Decimal(str(round(price * 1.01, 2))),
            low=Decimal(str(round(price * 0.99, 2))), close=Decimal(str(closes[d])),
            volume=1_000_000,
        ))
    if call_day_move is not None:
        # The bar the nightly ingest stores for the call date itself.
        p = price * (1 + call_day_move)
        db.add(PriceHistory(
            stock_id=stock.id, date=CALL_DATE,
            open=Decimal(str(round(price, 2))), high=Decimal(str(round(price * 1.01, 2))),
            low=Decimal(str(round(p * 0.99, 2))), close=Decimal(str(round(p, 2))),
            volume=1_000_000,
        ))
    db.flush()
    return stock, closes


@sqlite_distinct_on
def test_the_call_dates_own_bar_never_reaches_a_candidate(db_session):
    """LEADER trends up into the call date, then drops 12% ON it. Reading that
    bar would make it the reference close and trip the 5-day trend gate; the
    call must instead be priced off the previous session's close."""
    leader, leader_closes = _add_stock(db_session, "LEADER", "A", 0.004, call_day_move=-0.12)
    _add_stock(db_session, "MIDDLE", "B", 0.001, call_day_move=0.0)
    _add_stock(db_session, "FLAT", "C", 0.0, call_day_move=0.0)
    _add_stock(db_session, "SLIDER", "D", -0.002, call_day_move=0.0)

    candidates = _gather_candidates(db_session, CALL_DATE)
    by_symbol = {c.stock.symbol: c for c in candidates}

    assert candidates, "nothing was selected — the fixture no longer exercises the path"
    for c in candidates:
        assert c.reference_date < CALL_DATE, f"{c.stock.symbol} was priced off the call date's own bar"

    assert "LEADER" in by_symbol, "the call-date crash leaked into the trend gate"
    lead = by_symbol["LEADER"]
    assert lead.reference_date == PREVIOUS_SESSION
    assert lead.reference_close == pytest.approx(leader_closes[PREVIOUS_SESSION])
    assert lead.levels.entry_high == pytest.approx(leader_closes[PREVIOUS_SESSION], rel=0.02)


@sqlite_distinct_on
def test_a_late_run_and_a_morning_run_produce_the_same_call(db_session):
    """The same call regardless of when it is generated: a run made after the
    call date's bar exists must match one made before it did."""
    _add_stock(db_session, "LEADER", "A", 0.004)
    _add_stock(db_session, "MIDDLE", "B", 0.001)
    _add_stock(db_session, "SLIDER", "D", -0.002)
    morning = {c.stock.symbol: (c.reference_close, c.score.overall_score)
               for c in _gather_candidates(db_session, CALL_DATE)}

    for s in db_session.query(Stock).all():
        db_session.add(PriceHistory(stock_id=s.id, date=CALL_DATE, open=Decimal("1"),
                                    high=Decimal("500"), low=Decimal("1"), close=Decimal("500"),
                                    volume=1))
    db_session.flush()
    evening = {c.stock.symbol: (c.reference_close, c.score.overall_score)
               for c in _gather_candidates(db_session, CALL_DATE)}

    assert evening == morning


# ------------------------------------------------------------- scheduler --

class _Stop(BaseException):
    """Breaks the loop out of `while True`; BaseException so the loop's own
    `except Exception` cannot swallow it."""


@pytest.fixture
def loop_harness(monkeypatch):
    calls = {"generate": [], "recovery": []}
    runs: dict[date, SimpleNamespace] = {}
    ticks = {"n": 0}

    async def one_tick(name, seconds):
        ticks["n"] += 1
        if ticks["n"] > 1:
            raise _Stop

    monkeypatch.setattr(scheduler, "_heavy_lock", asyncio.Lock())
    monkeypatch.setattr(scheduler, "_idle", one_tick)
    monkeypatch.setattr(scheduler, "_has_signals_for", lambda d: False)
    monkeypatch.setattr(scheduler, "_get_latest_pipeline_run", lambda d: runs.get(d))
    monkeypatch.setattr(scheduler, "_run_incremental_update", lambda d: calls["recovery"].append(d) or 0)
    monkeypatch.setattr(scheduler, "_generate_signals", lambda d: calls["generate"].append(d) or 8)

    def run_at(hh, mm):
        monkeypatch.setattr(scheduler, "_now_ist",
                            lambda: datetime(CALL_DATE.year, CALL_DATE.month, CALL_DATE.day, hh, mm, tzinfo=IST))
        with pytest.raises(_Stop):
            asyncio.run(scheduler.daily_signals_loop())

    return SimpleNamespace(calls=calls, runs=runs, run_at=run_at)


def _run(status, run_date):
    return SimpleNamespace(status=status, run_date=run_date)


def test_the_production_sequence_no_longer_publishes(loop_harness):
    """The exact 2026-10-22 sequence: the morning was skipped, it is now 20:04,
    the previous session's run is incomplete and tonight's run is complete."""
    loop_harness.runs[PREVIOUS_SESSION] = _run("incomplete", PREVIOUS_SESSION)
    loop_harness.runs[CALL_DATE] = _run("complete", CALL_DATE)

    loop_harness.run_at(20, 4)

    assert loop_harness.calls["generate"] == []
    assert loop_harness.calls["recovery"] == [], "a recovery after the close can enable nothing"


def test_a_run_dated_today_is_not_accepted_even_before_the_close(loop_harness):
    loop_harness.runs[PREVIOUS_SESSION] = _run("incomplete", PREVIOUS_SESSION)
    loop_harness.runs[CALL_DATE] = _run("complete", CALL_DATE)

    loop_harness.run_at(11, 0)

    assert loop_harness.calls["generate"] == []
    assert loop_harness.calls["recovery"] == [PREVIOUS_SESSION]


def test_a_complete_previous_session_still_publishes_on_a_late_morning(loop_harness):
    """Catch-up inside the session is intended behaviour and must survive."""
    loop_harness.runs[PREVIOUS_SESSION] = _run("complete", PREVIOUS_SESSION)

    loop_harness.run_at(11, 0)

    assert loop_harness.calls["generate"] == [CALL_DATE]


@pytest.mark.parametrize("hh,mm,closed", [(9, 15, False), (15, 29, False), (15, 30, True), (20, 4, True)])
def test_the_publish_deadline_is_the_close(hh, mm, closed):
    now = datetime(2026, 10, 28, hh, mm, tzinfo=IST)
    assert scheduler._past_publish_deadline(now) is closed


def test_losing_a_publish_race_is_logged_not_retried(loop_harness, monkeypatch, caplog):
    """Two processes on one database (a forgotten second deployment did exist)
    both see an unpublished morning; the second must not overwrite the first."""
    from app.services.daily_signals import ShortlistAlreadyPublished

    def already(d):
        raise ShortlistAlreadyPublished(d)

    monkeypatch.setattr(scheduler, "_generate_signals", already)
    loop_harness.runs[PREVIOUS_SESSION] = _run("complete", PREVIOUS_SESSION)

    loop_harness.run_at(9, 16)

    assert "reason=already_published" in caplog.text
    assert "Daily signal generation failed" not in caplog.text
