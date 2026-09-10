"""The main page must not make an upstream request per page view.

compute_market_regime fetches ^NSEI from yfinance. GET /daily-signals — the
app's main page, and unauthenticated — called it on EVERY request. Under enough
traffic Yahoo rate-limits, at which point market_data._with_retry sleeps 2s and
then 8s (the rate-limit multiplier is 4) while holding one of 24 worker threads.
The busiest public endpoint could take the API down by being used.
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from app.core import cache
from app.services import daily_signals


def _nifty(n=400, start=200.0, slope=0.1):
    days = pd.date_range("2025-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "date": [d.date() for d in days],
        "close": [start + slope * i for i in range(n)],
    })


@pytest.fixture
def counting_fetch(monkeypatch, fake_redis):
    calls = {"n": 0}

    def _fetch(symbol, period="2y"):
        calls["n"] += 1
        return _nifty()

    monkeypatch.setattr(daily_signals, "fetch_price_history", _fetch)
    return calls


def test_the_second_call_does_not_hit_the_provider(db_session, counting_fetch):
    as_of = date(2026, 9, 9)

    first = daily_signals.compute_market_regime(db_session, as_of)
    second = daily_signals.compute_market_regime(db_session, as_of)

    assert counting_fetch["n"] == 1, "the cached call still fetched from yfinance"
    assert first == second


def test_the_cached_result_is_indistinguishable_from_a_fresh_one(db_session, counting_fetch):
    """Including the `as_of` date, which has to survive a JSON round trip —
    json.dumps cannot encode a date, so it is stored as ISO and rebuilt."""
    as_of = date(2026, 9, 9)

    fresh = daily_signals.compute_market_regime(db_session, as_of)
    cached = daily_signals.compute_market_regime(db_session, as_of)

    assert isinstance(cached["as_of"], date), "as_of came back as a string"
    assert cached["as_of"] == fresh["as_of"]
    assert cached["regime"] == fresh["regime"]
    assert cached["exposure"] == fresh["exposure"]
    assert cached["nifty_200dma"] == fresh["nifty_200dma"]


def test_different_dates_are_cached_separately(db_session, counting_fetch):
    """A point-in-time function keyed on one slot would answer a historical
    query with today's regime."""
    daily_signals.compute_market_regime(db_session, date(2026, 9, 9))
    daily_signals.compute_market_regime(db_session, date(2026, 8, 9))
    assert counting_fetch["n"] == 2


def test_a_redis_outage_degrades_to_the_old_behaviour_not_an_error(db_session, counting_fetch, fake_redis):
    """cache.py turns a Redis failure into a miss, so the worst case here is
    the uncached behaviour — never a 500 on the main page."""
    from tests.conftest import redis_down

    with redis_down(fake_redis):
        first = daily_signals.compute_market_regime(db_session, date(2026, 9, 9))
        second = daily_signals.compute_market_regime(db_session, date(2026, 9, 9))

    assert counting_fetch["n"] == 2, "expected to fetch each time with Redis down"
    assert first["regime"] == second["regime"]


def test_the_not_enough_data_branch_is_not_cached(db_session, monkeypatch, fake_redis):
    """Caching 'unknown' would keep answering it for 15 minutes after the data
    arrived."""
    calls = {"n": 0}
    short = _nifty(n=10)

    def _fetch(symbol, period="2y"):
        calls["n"] += 1
        return short

    monkeypatch.setattr(daily_signals, "fetch_price_history", _fetch)

    for _ in range(2):
        out = daily_signals.compute_market_regime(db_session, date(2026, 9, 9))
        assert out["regime"] == "unknown"
        assert out["exposure"] == 1.0
    assert calls["n"] == 2, "an 'unknown' regime was cached"


def test_the_regime_rule_is_unchanged_by_caching(db_session, monkeypatch, fake_redis):
    """close >= 200DMA is bull. A rising series is bull; a falling one is bear."""
    monkeypatch.setattr(daily_signals, "fetch_price_history",
                        lambda s, period="2y": _nifty(slope=0.5))
    assert daily_signals.compute_market_regime(db_session, date(2026, 9, 9))["regime"] == "bull"

    cache._last_logged.clear()
    monkeypatch.setattr(daily_signals, "fetch_price_history",
                        lambda s, period="2y": _nifty(start=400.0, slope=-0.5))
    # A different date, so the bull result above is not simply served back.
    out = daily_signals.compute_market_regime(db_session, date(2026, 9, 8))
    assert out["regime"] == "bear"
    assert out["exposure"] < 1.0
