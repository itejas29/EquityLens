"""Authentication, abuse controls and graceful degradation at the HTTP edge.

Each test here corresponds to something that was actually open, not to a
hypothetical. The endpoint list in test_mutating_endpoints_require_auth is the
set that could be driven by anyone able to reach the host: on-demand yfinance
ingestion, a full-universe scoring pass, and a republish of the dated shortlist
that the product describes as frozen.
"""

import pytest

from tests.conftest import redis_down

# (method, path, body) for every endpoint that writes or spends real resources.
MUTATING_ENDPOINTS = [
    ("post", "/api/v1/stocks/RELIANCE/ingest", None),
    ("post", "/api/v1/stocks/RELIANCE/refresh", None),
    ("post", "/api/v1/stocks/RELIANCE/compute-indicators", None),
    ("post", "/api/v1/stocks/catalogue/refresh", None),
    ("post", "/api/v1/scoring/run-universe", None),
    ("post", "/api/v1/daily-signals/run", None),
]


@pytest.mark.parametrize("method,path,body", MUTATING_ENDPOINTS)
def test_mutating_endpoints_require_auth(client, method, path, body):
    resp = getattr(client, method)(path, json=body)
    assert resp.status_code == 401, f"{path} answered {resp.status_code} to an anonymous caller"


@pytest.mark.parametrize("method,path,body", MUTATING_ENDPOINTS)
def test_mutating_endpoints_reject_a_forged_token(client, method, path, body):
    headers = {"Authorization": "Bearer not.a.real.token"}
    resp = getattr(client, method)(path, json=body, headers=headers)
    assert resp.status_code == 401


def test_paper_and_ai_trading_require_auth(client):
    for path in ("/api/v1/paper/account", "/api/v1/ai-trading/account",
                 "/api/v1/paper/transactions", "/api/v1/watchlist"):
        assert client.get(path).status_code == 401, path


# ------------------------------------------------------------ auth surface --

def test_login_does_not_reveal_whether_an_account_exists(client, auth_headers):
    """Same status and same message for a wrong password and a missing user.

    The timing gap that used to accompany it (~1ms vs ~214ms, because bcrypt
    was skipped entirely for an unknown email) is closed by always running a
    verification; that part is asserted in
    test_login_spends_a_bcrypt_verify_even_for_an_unknown_email.
    """
    wrong_password = client.post("/api/v1/auth/login", json={
        "email": "audit@example.com", "password": "not-the-password"})
    unknown_user = client.post("/api/v1/auth/login", json={
        "email": "nobody@example.com", "password": "not-the-password"})

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()


def test_login_spends_a_bcrypt_verify_even_for_an_unknown_email(client, monkeypatch):
    from app.api.v1 import auth as auth_api

    calls = []
    monkeypatch.setattr(auth_api, "dummy_password_verify", lambda pw: calls.append(pw))
    client.post("/api/v1/auth/login", json={
        "email": "nobody@example.com", "password": "whatever"})
    assert calls == ["whatever"], "unknown email short-circuited past the bcrypt cost"


def test_login_rejects_an_oversized_password_before_hashing_it(client):
    resp = client.post("/api/v1/auth/login", json={
        "email": "audit@example.com", "password": "x" * 5000})
    assert resp.status_code == 422


def test_register_rejects_an_oversized_risk_profile(client):
    resp = client.post("/api/v1/auth/register", json={
        "name": "X", "email": "x@example.com", "password": "password123",
        "risk_profile": "y" * 500})
    # String(50) in the model: unbounded here meant Postgres raised at INSERT
    # and the caller saw a 500 for what is a validation problem.
    assert resp.status_code == 422


def test_auth_endpoints_are_rate_limited(client, monkeypatch):
    from app.core import rate_limit

    monkeypatch.setattr(rate_limit, "AUTH_RATE_LIMIT_MAX_REQUESTS", 3)
    body = {"email": "audit@example.com", "password": "wrong"}
    statuses = [client.post("/api/v1/auth/login", json=body).status_code for _ in range(6)]

    assert 429 in statuses, f"login was never throttled: {statuses}"
    limited = client.post("/api/v1/auth/login", json=body)
    assert limited.headers.get("Retry-After") is not None


# --------------------------------------------------- degradation under load --

def test_analysis_endpoints_fail_closed_when_redis_is_down(client, fake_redis, auth_headers):
    """503, not 500 and not 200.

    The limiter cannot tell whether this request is within budget, so it
    refuses rather than waving through the most expensive endpoint in the app.
    """
    with redis_down(fake_redis):
        resp = client.post("/api/v1/scoring/run-universe", headers=auth_headers)
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After") is not None


def test_auth_endpoints_fail_open_when_redis_is_down(client, fake_redis):
    """The deliberate opposite of the test above.

    Failing closed here would lock the group out of their own app for the
    duration of an Upstash incident, with no way in and no way to see why.
    """
    with redis_down(fake_redis):
        resp = client.post("/api/v1/auth/login", json={
            "email": "nobody@example.com", "password": "whatever"})
    assert resp.status_code == 401, "a Redis outage must not break logging in"


def test_cache_reads_degrade_to_a_miss_rather_than_raising(fake_redis):
    from app.core import cache

    cache.set_scored_universe_cache([{"stock_id": 1}])
    assert cache.get_scored_universe_cache() == [{"stock_id": 1}]

    with redis_down(fake_redis):
        assert cache.get_scored_universe_cache() is None      # read -> miss
        cache.set_scored_universe_cache([{"stock_id": 2}])    # write -> no-op
        cache.invalidate_scored_universe_cache()              # delete -> no-op


def test_a_corrupt_cache_value_is_a_miss_not_a_500(fake_redis):
    from app.core import cache

    fake_redis.data[cache.scored_universe_key()] = "{not json"
    assert cache.get_scored_universe_cache() is None


def test_price_feed_still_answers_with_redis_down(fake_redis):
    """get_price_feed() is on the hot path of the signals, paper and AI pages,
    and is also what paper_trading.buy()/sell() consult. An outage must leave
    it reporting 'stale' rather than raising through all four."""
    from app.services.market import get_price_feed

    with redis_down(fake_redis):
        feed = get_price_feed()
    assert feed.status == "stale"
    assert feed.prices == {}


# ----------------------------------------------------------------- health --

def test_liveness_probes_stay_public(client):
    """A container or uptime probe must not need a credential."""
    assert client.get("/api/v1/ping").status_code == 200
    assert client.get("/api/v1/health").status_code == 200


def test_pipeline_health_requires_auth(client):
    """It names the universe and describes the scheduler. Not a public page."""
    assert client.get("/api/v1/health/pipeline").status_code == 401


def test_pipeline_health_caps_the_missing_symbol_list(client, db_session, auth_headers):
    """Unbounded, this list is the whole active universe on any day the
    pipeline is behind — a large response and a free readout of composition."""
    from datetime import date

    from app.api.v1.health import MAX_MISSING_SYMBOLS_REPORTED
    from app.models.price_history import PriceHistory
    from app.models.stock import Stock

    for i in range(MAX_MISSING_SYMBOLS_REPORTED + 20):
        db_session.add(Stock(symbol=f"SYM{i:03d}", is_active=True))
    db_session.flush()
    # One stock does have a bar for the session, so latest_date exists and the
    # coverage branch actually runs.
    covered = db_session.query(Stock).filter(Stock.symbol == "SYM000").one()
    db_session.add(PriceHistory(stock_id=covered.id, date=date(2026, 9, 1), close=100))
    db_session.commit()

    body = client.get("/api/v1/health/pipeline", headers=auth_headers).json()
    coverage = body["data"]

    assert len(coverage["stocks_missing_today"]) == MAX_MISSING_SYMBOLS_REPORTED
    assert coverage["stocks_missing_today_truncated"] is True
    # The count is the operational signal and is NOT truncated.
    assert coverage["stocks_missing_today_count"] == MAX_MISSING_SYMBOLS_REPORTED + 19
    assert coverage["stocks_with_today_data"] == 1


def test_wildcard_cors_origin_is_rejected_at_startup(monkeypatch):
    """Config refuses "*" rather than serving with it."""
    import pydantic
    from app.core.config import Settings

    monkeypatch.setenv("CORS_ORIGINS", "https://ok.example.com,*")
    with pytest.raises(pydantic.ValidationError, match=r'may not contain'):
        Settings()
