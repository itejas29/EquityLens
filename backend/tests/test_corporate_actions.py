"""A split rewrites the provider's whole history. A gap-fill must notice.

yfinance returns split-adjusted prices — checked against BAJFINANCE.NS's 2:1
on 2025-06-16, where the close runs 936.85 -> 938.00 across the split instead
of halving. So the danger is not that stored prices are raw; it is that the
provider RESTATES the past, while incremental_price_update only ever fetches
bars after the last stored date. Everything before the event stays on the old
basis, everything after is on the new one, and the stored series carries a
permanent artificial gap the size of the split ratio.

For a strategy that ranks on 12-month momentum that is not cosmetic. A 2:1
split reads as -50% for a year (the stock is never bought — the safe
direction); an action that reads positive puts it at the TOP of the ranking
and gets it bought on a move that never happened.
"""

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.services import incremental
from app.services.incremental import (
    RESTATEMENT_OVERLAP_DAYS,
    RESTATEMENT_TOLERANCE_PCT,
    _detect_restatement,
    _stored_closes,
)


def _frame(rows):
    return pd.DataFrame(
        [{"date": d, "open": c, "high": c, "low": c, "close": c, "volume": 1000} for d, c in rows]
    )


# ------------------------------------------------------------- the detector --

def test_matching_history_is_not_flagged():
    stored = {date(2026, 9, 1): 100.0, date(2026, 9, 2): 101.0}
    assert _detect_restatement(stored, _frame([(date(2026, 9, 1), 100.0),
                                               (date(2026, 9, 2), 101.0)])) is None


def test_a_two_for_one_split_is_flagged():
    stored = {date(2026, 9, 1): 1870.0, date(2026, 9, 2): 1876.0}
    found = _detect_restatement(stored, _frame([(date(2026, 9, 1), 935.0),
                                                (date(2026, 9, 2), 938.0)]))
    assert found is not None
    when, was, now = found
    assert (when, was, now) == (date(2026, 9, 1), 1870.0, 935.0)
    assert round(was / now, 4) == 2.0


def test_a_bonus_issue_reported_as_a_small_ratio_is_still_flagged():
    """Indian bonus issues are common and Yahoo reports them as splits. A
    1.5:1 moves the price ~33% — nowhere near the tolerance."""
    stored = {date(2026, 9, 1): 150.0}
    assert _detect_restatement(stored, _frame([(date(2026, 9, 1), 100.0)])) is not None


def test_rounding_noise_is_not_a_restatement():
    """Stored is Numeric(12,2); fetched is a float64. The tolerance exists for
    that difference and nothing else."""
    stored = {date(2026, 9, 1): 936.85}
    assert _detect_restatement(stored, _frame([(date(2026, 9, 1), 936.8500061035156)])) is None


def test_the_tolerance_boundary_is_where_it_says_it_is():
    stored = {date(2026, 9, 1): 100.0}
    just_under = 100.0 * (1 + (RESTATEMENT_TOLERANCE_PCT - 0.1) / 100)
    just_over = 100.0 * (1 + (RESTATEMENT_TOLERANCE_PCT + 0.1) / 100)
    assert _detect_restatement(stored, _frame([(date(2026, 9, 1), just_under)])) is None
    assert _detect_restatement(stored, _frame([(date(2026, 9, 1), just_over)])) is not None


def test_dates_we_do_not_hold_are_not_compared():
    """A fetched date with nothing on disk is a new bar, not a disagreement."""
    stored = {date(2026, 9, 1): 100.0}
    assert _detect_restatement(stored, _frame([(date(2026, 9, 1), 100.0),
                                               (date(2026, 9, 2), 200.0)])) is None


def test_no_stored_history_means_nothing_to_compare():
    assert _detect_restatement({}, _frame([(date(2026, 9, 1), 100.0)])) is None


def test_a_null_close_on_disk_is_skipped_not_treated_as_zero(db_session):
    """Missing data stays NULL — it must not be read as a price of 0, which
    would make every comparison against it a division by zero or a 100% move."""
    stock = Stock(symbol="NULLC", is_active=True)
    db_session.add(stock)
    db_session.flush()
    db_session.add(PriceHistory(stock_id=stock.id, date=date(2026, 9, 1), close=None))
    db_session.add(PriceHistory(stock_id=stock.id, date=date(2026, 9, 2), close=Decimal("100.00")))
    db_session.flush()

    stored = _stored_closes(db_session, stock.id, date(2026, 9, 1))
    assert date(2026, 9, 1) not in stored
    assert stored[date(2026, 9, 2)] == 100.0


# ------------------------------------------------------------- the window --

def test_the_fetch_window_reaches_back_far_enough_to_overlap(db_session, monkeypatch):
    """The detector is useless if the download never re-returns a stored date.

    Asserts the start date actually handed to yfinance, because the whole
    mechanism rests on it and it costs nothing to get wrong.
    """
    stock = Stock(symbol="WINDOW", is_active=True)
    db_session.add(stock)
    db_session.flush()
    last_stored = date(2026, 9, 8)
    db_session.add(PriceHistory(stock_id=stock.id, date=last_stored, close=Decimal("100.00")))
    db_session.flush()

    seen = {}

    def spy(symbols, start_date, end_date):
        seen["start"] = start_date
        return {}

    monkeypatch.setattr(incremental, "_download_incremental_batch", spy)
    result = incremental.IncrementalResult()
    incremental._process_incremental_pulls(
        db_session, [(stock, last_stored + timedelta(days=1))],
        date(2026, 9, 9), pd.DataFrame(), result,
    )

    assert seen["start"] <= last_stored, (
        "the window never re-returns a stored bar, so nothing can be compared"
    )
    assert seen["start"] == last_stored + timedelta(days=1) - timedelta(days=RESTATEMENT_OVERLAP_DAYS)


def test_a_restated_symbol_is_repulled_and_not_appended_to(db_session, monkeypatch):
    """The whole point: on a restatement, do NOT upsert the incremental window.

    Appending would leave the pre-event bars on the old basis. The symbol goes
    to the full-pull path instead, which rewrites every stored bar.
    """
    stock = Stock(symbol="SPLIT", is_active=True)
    db_session.add(stock)
    db_session.flush()
    last_stored = date(2026, 9, 8)
    for offset, close in ((2, "1860.00"), (1, "1865.00"), (0, "1870.00")):
        db_session.add(PriceHistory(
            stock_id=stock.id, date=last_stored - timedelta(days=offset), close=Decimal(close)))
    db_session.flush()

    # Provider now returns the same dates halved — a 2:1 split.
    def fake_download(symbols, start_date, end_date):
        return {"SPLIT": pd.DataFrame(
            {"Open": [930.0, 932.5, 935.0, 938.0],
             "High": [930.0, 932.5, 935.0, 938.0],
             "Low": [930.0, 932.5, 935.0, 938.0],
             "Close": [930.0, 932.5, 935.0, 938.0],
             "Volume": [100, 100, 100, 100]},
            index=pd.DatetimeIndex([last_stored - timedelta(days=2), last_stored - timedelta(days=1),
                                    last_stored, last_stored + timedelta(days=1)], name="Date"),
        )}

    repulled = []
    periods = []
    monkeypatch.setattr(incremental, "_download_incremental_batch", fake_download)

    def fake_full_pulls(db, stocks, bench, res, period=incremental.HISTORY_PERIOD):
        repulled.extend(s.symbol for s in stocks)
        periods.append(period)

    monkeypatch.setattr(incremental, "_process_full_pulls", fake_full_pulls)

    result = incremental.IncrementalResult()
    incremental._process_incremental_pulls(
        db_session, [(stock, last_stored + timedelta(days=1))],
        last_stored + timedelta(days=1), pd.DataFrame(), result,
    )

    assert repulled == ["SPLIT"]
    # The whole stored series must be replaced, so the re-pull reaches back as
    # far as the provider goes, not the 10y HISTORY_PERIOD. See
    # RESTATEMENT_REPULL_PERIOD.
    assert periods == ["max"]
    assert result.restated == 1
    assert result.results[0].status.value == "CORPORATE_ACTION"
    # Nothing was written: the old basis is still on disk, untouched, waiting
    # for the full re-pull to replace it wholesale.
    stored = _stored_closes(db_session, stock.id, date(2026, 1, 1))
    assert stored[last_stored] == 1870.0
    assert last_stored + timedelta(days=1) not in stored


def test_an_unchanged_symbol_still_takes_the_normal_path(db_session, monkeypatch):
    """The overlap must not make every ordinary day look like a corporate
    action — that would re-pull the entire universe nightly."""
    stock = Stock(symbol="NORMAL", is_active=True)
    db_session.add(stock)
    db_session.flush()
    last_stored = date(2026, 9, 8)
    db_session.add(PriceHistory(stock_id=stock.id, date=last_stored, close=Decimal("100.00")))
    db_session.flush()

    def fake_download(symbols, start_date, end_date):
        return {"NORMAL": pd.DataFrame(
            {"Open": [100.0, 101.0], "High": [100.0, 101.0], "Low": [100.0, 101.0],
             "Close": [100.0, 101.0], "Volume": [100, 100]},
            index=pd.DatetimeIndex([last_stored, last_stored + timedelta(days=1)], name="Date"),
        )}

    monkeypatch.setattr(incremental, "_download_incremental_batch", fake_download)
    monkeypatch.setattr(incremental, "upsert_indicators", lambda *a, **k: 0)
    monkeypatch.setattr(incremental, "compute_indicators", lambda *a, **k: pd.DataFrame())

    result = incremental.IncrementalResult()
    incremental._process_incremental_pulls(
        db_session, [(stock, last_stored + timedelta(days=1))],
        last_stored + timedelta(days=1), pd.DataFrame(), result,
    )

    assert result.restated == 0
    assert result.succeeded == 1
    stored = _stored_closes(db_session, stock.id, date(2026, 1, 1))
    assert stored[last_stored + timedelta(days=1)] == 101.0
