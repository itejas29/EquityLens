import pytest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock
from sqlalchemy import text
from app.models.pipeline_run import PipelineRun
from app.models.price_history import PriceHistory
from app.models.stock import Stock

def test_single_failure_detected(db_session):
    # Setup
    stock1 = Stock(symbol="RELIANCE", is_active=True)
    stock2 = Stock(symbol="MISSING", is_active=True)
    db_session.add_all([stock1, stock2])
    db_session.commit()
    
    # Mock
    with patch("app.services.incremental._download_incremental_batch") as mock_batch:
        import pandas as pd
        # Return data for RELIANCE but none for MISSING
        df = pd.DataFrame({
            "Date": ["2026-08-15"], "Open": [100], "High": [105], "Low": [95], "Close": [102], "Volume": [1000]
        })
        df.set_index("Date", inplace=True)
        mock_batch.return_value = {"RELIANCE": df}
        
        # Test
        from app.services.incremental import incremental_price_update
        result = incremental_price_update(db_session, today=date(2026, 8, 15))
        
        assert result.requested == 2
        assert result.succeeded == 1
        assert result.failed == 1
        assert result.status == "incomplete"
        
        failed = [r for r in result.results if r.status.value != "SUCCESS"]
        assert len(failed) == 1
        assert failed[0].symbol == "MISSING"


# --- unsettled-session bars -------------------------------------------------
#
# Regression tests for the 2026-08-25 production incident: a catch-up run
# executed 63 minutes into the session wrote in-progress bars as daily closes
# for all 500 stocks, and the "already current" check then froze them there
# permanently. See docs/audit/unsettled-session-bars.md.


def _frame(dates):
    import pandas as pd
    return pd.DataFrame({
        "date": [date.fromisoformat(d) for d in dates],
        "open": [100.0] * len(dates),
        "high": [105.0] * len(dates),
        "low": [95.0] * len(dates),
        "close": [102.0] * len(dates),
        "volume": [1000] * len(dates),
    })


def test_in_progress_bar_is_dropped_mid_session():
    from datetime import datetime
    from app.core.market_hours import IST
    from app.services.incremental import _drop_unsettled_session

    # 10:18 IST — the exact wall-clock time of the incident's catch-up run.
    now = datetime(2026, 8, 25, 10, 18, tzinfo=IST)
    kept, dropped = _drop_unsettled_session(_frame(["2026-08-21", "2026-08-24", "2026-08-25"]), now)

    assert dropped == 1
    assert kept["date"].max() == date(2026, 8, 24)


def test_settled_bar_is_kept_after_close():
    from datetime import datetime
    from app.core.market_hours import IST
    from app.services.incremental import _drop_unsettled_session

    # 20:00 IST — when the nightly ingest actually runs. Nothing may be dropped
    # here, or the pipeline would never store a bar at all.
    now = datetime(2026, 8, 25, 20, 0, tzinfo=IST)
    kept, dropped = _drop_unsettled_session(_frame(["2026-08-24", "2026-08-25"]), now)

    assert dropped == 0
    assert kept["date"].max() == date(2026, 8, 25)


def test_boundary_is_1600_ist_not_market_close():
    from datetime import datetime
    from app.core.market_hours import IST
    from app.services.incremental import _drop_unsettled_session

    # core.market_hours closes its polling window at 15:40. A daily bar needs
    # longer to settle, so 15:59 must still withhold and 16:00 must admit.
    frame = _frame(["2026-08-25"])
    _, dropped_before = _drop_unsettled_session(frame, datetime(2026, 8, 25, 15, 59, tzinfo=IST))
    _, dropped_after = _drop_unsettled_session(frame, datetime(2026, 8, 25, 16, 0, tzinfo=IST))

    assert dropped_before == 1
    assert dropped_after == 0


def test_future_dated_bar_is_always_dropped():
    from datetime import datetime
    from app.core.market_hours import IST
    from app.services.incremental import _drop_unsettled_session

    now = datetime(2026, 8, 25, 20, 0, tzinfo=IST)
    kept, dropped = _drop_unsettled_session(_frame(["2026-08-25", "2026-08-26"]), now)

    assert dropped == 1
    assert kept["date"].max() == date(2026, 8, 25)


def test_utc_server_clock_does_not_shift_the_cutoff():
    """The box runs UTC, where date.today() is still yesterday until 05:30 IST.
    Judging "has today settled?" on the UTC day is the mistake this guards, so
    the same instant expressed in UTC must give the same answer."""
    from datetime import datetime, timezone
    from app.core.market_hours import IST
    from app.services.incremental import _drop_unsettled_session

    # 2026-08-25 20:00 IST == 2026-08-25 14:30 UTC. Today's bar is settled.
    as_utc = datetime(2026, 8, 25, 14, 30, tzinfo=timezone.utc)
    kept, dropped = _drop_unsettled_session(_frame(["2026-08-25"]), as_utc.astimezone(IST))

    assert dropped == 0
    assert kept["date"].max() == date(2026, 8, 25)


def test_partial_bar_never_reaches_the_database(db_session):
    """End-to-end: the ingest must not store an in-progress bar even when the
    provider hands one over. This is the invariant the incident violated."""
    import pandas as pd
    from datetime import datetime
    from app.core.market_hours import IST

    stock = Stock(symbol="TCS", is_active=True)
    db_session.add(stock)
    db_session.commit()
    db_session.add(PriceHistory(stock_id=stock.id, date=date(2026, 8, 24), open=100,
                                high=105, low=95, close=100.0, volume=5000))
    db_session.commit()

    # Provider returns the settled 08-24 bar plus an in-progress 08-25 bar
    # whose close (102) is nowhere near where the session actually finished.
    df = pd.DataFrame({
        "Date": ["2026-08-24", "2026-08-25"],
        "Open": [100, 101], "High": [105, 103], "Low": [95, 100],
        "Close": [100.0, 102.0], "Volume": [5000, 400],
    }).set_index("Date")

    with patch("app.services.incremental._download_incremental_batch", return_value={"TCS": df}), \
         patch("app.services.incremental.fetch_benchmark_df", return_value=pd.DataFrame()), \
         patch("app.services.incremental.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 8, 25, 10, 18, tzinfo=IST)
        from app.services.incremental import incremental_price_update
        incremental_price_update(db_session, today=date(2026, 8, 25))

    stored = {r.date: float(r.close) for r in
              db_session.query(PriceHistory).filter(PriceHistory.stock_id == stock.id).all()}
    assert date(2026, 8, 25) not in stored, "an in-progress bar was written as a daily close"
    assert stored[date(2026, 8, 24)] == 100.0


def test_restatement_repull_fetches_max_history(db_session):
    """A restated symbol must be re-pulled at period="max", not HISTORY_PERIOD.
    A 10y pull can start after the stored series does; upsert leaves the older
    bars on the pre-split basis and the gap moves instead of disappearing."""
    import pandas as pd

    stock = Stock(symbol="PGIL", is_active=True)
    db_session.add(stock)
    db_session.commit()
    db_session.add(PriceHistory(stock_id=stock.id, date=date(2026, 8, 21), open=200,
                                high=205, low=195, close=200.0, volume=5000))
    db_session.commit()

    # Provider now serves 08-21 at half the stored price: a 2:1 restatement.
    restated = pd.DataFrame({
        "Date": ["2026-08-21", "2026-08-24"],
        "Open": [100, 101], "High": [103, 104], "Low": [98, 99],
        "Close": [100.0, 101.0], "Volume": [10000, 9000],
    }).set_index("Date")

    with patch("app.services.incremental._download_incremental_batch", return_value={"PGIL": restated}), \
         patch("app.services.incremental.fetch_benchmark_df", return_value=pd.DataFrame()), \
         patch("app.services.incremental._download_full_batch", return_value={}) as full:
        from app.services.incremental import incremental_price_update
        result = incremental_price_update(db_session, today=date(2026, 8, 25))

    assert result.restated == 1
    assert full.call_count == 1
    assert full.call_args.args[1] == "max"
