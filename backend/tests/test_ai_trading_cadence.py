"""The monthly rebalance cadence, and what makes a month's rebalance 'used up'.

V1 rebalances monthly and backtest.py gates BOTH the bear-regime exposure trim
and every new entry behind that date. The live loop derives the cadence from
AITradingRun rows rather than the calendar, so a holiday or an outage shifts the
rebalance to whichever day the loop next runs instead of skipping the month.

These tests pin the part of that derivation that was wrong: a run row is only
evidence a rebalance happened if the run COMPLETED.
"""

from datetime import date

import pytest

from app.models.ai_trading_run import AITradingRun
from app.services.ai_trading import _is_rebalance_day


def _run(db, day: date, status: str) -> AITradingRun:
    row = AITradingRun(run_date=day, status=status)
    db.add(row)
    db.flush()
    return row


def test_first_run_of_the_month_is_the_rebalance(db_session):
    assert _is_rebalance_day(db_session, date(2026, 9, 1)) is True


def test_a_completed_run_earlier_in_the_month_consumes_it(db_session):
    _run(db_session, date(2026, 9, 1), "complete")
    assert _is_rebalance_day(db_session, date(2026, 9, 2)) is False


def test_a_failed_run_does_not_consume_the_months_rebalance(db_session):
    """The defect this test exists for.

    run_date is UNIQUE and the scheduler writes the row before trading, marking
    it "failed" if the cycle raises. Counting that row as the month's rebalance
    meant one transient failure on the 1st — a yfinance timeout, a DB blip —
    left the account doing stop/target checks only for the rest of the month:
    no entries, no regime trim, and nothing anywhere saying so.
    """
    _run(db_session, date(2026, 9, 1), "failed")
    assert _is_rebalance_day(db_session, date(2026, 9, 2)) is True


def test_a_run_stuck_at_running_does_not_consume_it_either(db_session):
    """Process died mid-cycle: the row was written, nothing was committed."""
    _run(db_session, date(2026, 9, 1), "running")
    assert _is_rebalance_day(db_session, date(2026, 9, 2)) is True


def test_last_months_completed_run_does_not_carry_over(db_session):
    _run(db_session, date(2026, 8, 31), "complete")
    assert _is_rebalance_day(db_session, date(2026, 9, 1)) is True


def test_todays_own_row_is_not_counted_against_itself(db_session):
    """The scheduler creates this cycle's row before calling, so a filter of
    `< as_of` — not `<=` — is what keeps a cycle from disqualifying itself."""
    _run(db_session, date(2026, 9, 1), "running")
    assert _is_rebalance_day(db_session, date(2026, 9, 1)) is True


def test_a_holiday_shifts_the_rebalance_rather_than_skipping_it(db_session):
    """No run on the 1st or 2nd (weekend); the 3rd takes the rebalance."""
    assert _is_rebalance_day(db_session, date(2026, 9, 3)) is True
    _run(db_session, date(2026, 9, 3), "complete")
    assert _is_rebalance_day(db_session, date(2026, 9, 4)) is False
