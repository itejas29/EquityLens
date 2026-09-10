"""Redis caching helpers.

Cached: scored universe (1h — percentile ranks don't meaningfully shift
minute to minute), stock detail payload (15m), backtest results by config
hash (24h — identical config always produces identical point-in-time
results, so this is a pure cache, not a staleness risk).

Never cached: live price lookups, user portfolios, auth — anything where a
stale read would show a user wrong money or let a stale session survive
past a real state change.

A REDIS OUTAGE IS A CACHE MISS, NOT AN ERROR. Every access here used to let
redis.RedisError propagate. Because get_price_feed() reads this module, that
turned an Upstash blip into 500s on the signals page, the paper account and
the AI trading page — all three of which have a working database fallback and
none of which need Redis to answer correctly. Worse, paper_trading.buy()/sell()
also call get_price_feed(), so the same blip failed the whole AI trading cycle.

So reads degrade to None (the caller's existing "not cached" path), writes and
deletes become no-ops, and the failure is logged rather than raised. Two
deliberate consequences:

  * A failed write means the next read recomputes. Correct, just slower.
  * A failed DELETE means a stale entry can outlive its invalidation, up to its
    TTL. That is the one genuinely lossy case, so it is logged at WARNING with
    the key — the longest exposure is the scored universe at one hour.

What does NOT degrade: core/rate_limit.py. A rate limiter that treats an
unreachable Redis as "allow" removes the protection exactly when the system is
already unhealthy, so it fails closed instead.
"""

import hashlib
import json
import logging
import time
from typing import Any

from redis.exceptions import RedisError

from app.core.redis_client import redis_client

logger = logging.getLogger(__name__)

# One log line per minute per operation kind while Redis is down. An outage
# would otherwise emit a line per request, which buries the cause it is
# reporting.
_LOG_THROTTLE_SECONDS = 60
_last_logged: dict[str, float] = {}


def _log_degraded(operation: str, key: str, exc: Exception, level: int = logging.INFO) -> None:
    now = time.monotonic()
    if now - _last_logged.get(operation, 0.0) < _LOG_THROTTLE_SECONDS:
        return
    _last_logged[operation] = now
    logger.log(level, "redis unavailable, cache %s degraded (key=%s): %s", operation, key, exc)

TTL_SCORED_UNIVERSE = 3600
TTL_STOCK_DETAIL = 900
TTL_BACKTEST = 86400


def _get_json(key: str) -> Any | None:
    try:
        raw = redis_client.get(key)
    except RedisError as exc:
        _log_degraded("read", key, exc)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        # A corrupt or half-written value is a miss, not a 500. Logged at
        # WARNING because unlike an outage this should never happen.
        logger.warning("discarding unparseable cache value (key=%s): %s", key, exc)
        return None


def _set_json(key: str, value: Any, ttl: int) -> None:
    try:
        redis_client.set(key, json.dumps(value), ex=ttl)
    except RedisError as exc:
        _log_degraded("write", key, exc)


def _delete(key: str) -> None:
    """Invalidation. The one lossy degradation — see the module docstring."""
    try:
        redis_client.delete(key)
    except RedisError as exc:
        _log_degraded("invalidate", key, exc, level=logging.WARNING)


def scored_universe_key() -> str:
    return "cache:scored_universe"


def get_scored_universe_cache() -> list[dict] | None:
    return _get_json(scored_universe_key())


def set_scored_universe_cache(value: list[dict]) -> None:
    _set_json(scored_universe_key(), value, TTL_SCORED_UNIVERSE)


def invalidate_scored_universe_cache() -> None:
    _delete(scored_universe_key())


def stock_detail_key(symbol: str) -> str:
    return f"cache:stock_detail:{symbol.upper()}"


def get_stock_detail_cache(symbol: str) -> dict | None:
    return _get_json(stock_detail_key(symbol))


def set_stock_detail_cache(symbol: str, value: dict) -> None:
    _set_json(stock_detail_key(symbol), value, TTL_STOCK_DETAIL)


def invalidate_stock_detail_cache(symbol: str) -> None:
    _delete(stock_detail_key(symbol))


def hash_backtest_config(config: dict) -> str:
    canonical = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def backtest_key(config_hash: str) -> str:
    return f"cache:backtest:{config_hash}"


def get_backtest_cache(config_hash: str) -> dict | None:
    return _get_json(backtest_key(config_hash))


def set_backtest_cache(config_hash: str, value: dict) -> None:
    _set_json(backtest_key(config_hash), value, TTL_BACKTEST)


# ── Live prices (written by scheduler, read by WS endpoint & REST fallback) ──

# Was 90s, on the assumption of a ~60s refresh cycle. That assumption broke:
# the 501-symbol fetch now takes 30-95s depending on Yahoo, so a full cycle runs
# 90-155s and the key was expiring BETWEEN healthy refreshes. The tape went
# empty for most of every cycle and the UI read it as "market closed" mid-
# session. Sized to outlast a slow-but-working cycle while still expiring if the
# refresher genuinely dies.
TTL_LIVE_PRICES = 300  # seconds

# The frozen last-session snapshot. Long-lived on purpose: the 90s key above is
# a liveness signal (is the refresher currently ticking?), and letting it expire
# is how "the market is open" stops being true. But the PRICES themselves should
# not disappear with it — when NSE shuts, the tape should hold at the last
# traded price rather than emptying out. A week covers a long weekend plus
# holidays; anything older is genuinely stale and better shown as absent.
TTL_SESSION_SNAPSHOT = 7 * 24 * 3600


# ── Market regime (NIFTY 200DMA) ──

# The regime is derived from DAILY closes, so for a past date it never changes
# and for today it moves only as the still-forming close settles. 15 minutes is
# far shorter than that and far longer than a page load.
#
# This cache is not an optimisation, it is a fix. compute_market_regime()
# fetches ^NSEI from yfinance, and GET /daily-signals — the app's main page,
# unauthenticated — called it on EVERY request. One upstream HTTP round trip per
# page view, and under enough traffic Yahoo rate-limits, at which point
# _with_retry sleeps 2s then 8s while holding one of 24 worker threads. The
# busiest public endpoint could take the API down by being used.
TTL_MARKET_REGIME = 900


def market_regime_key(as_of: str) -> str:
    return f"cache:market_regime:{as_of}"


def get_market_regime_cache(as_of: str) -> dict | None:
    return _get_json(market_regime_key(as_of))


def set_market_regime_cache(as_of: str, value: dict) -> None:
    _set_json(market_regime_key(as_of), value, TTL_MARKET_REGIME)


def live_prices_key() -> str:
    return "live:prices"


def session_snapshot_key() -> str:
    return "live:prices:session"


def get_live_prices() -> dict | None:
    """Prices from a currently-ticking refresher, or None when it is not running.

    None means "the market is not open right now", not "there are no prices" —
    callers wanting the last known values should use get_session_snapshot().
    """
    return _get_json(live_prices_key())


def set_live_prices(value: dict) -> None:
    _set_json(live_prices_key(), value, TTL_LIVE_PRICES)


def get_session_snapshot() -> dict | None:
    """Last captured prices with their capture time.

    Shape: {"prices": {...}, "captured_at": iso8601, "session_date": "YYYY-MM-DD"}
    """
    return _get_json(session_snapshot_key())


def set_session_snapshot(prices: dict, captured_at: str, session_date: str) -> None:
    _set_json(
        session_snapshot_key(),
        {"prices": prices, "captured_at": captured_at, "session_date": session_date},
        TTL_SESSION_SNAPSHOT,
    )


# ── Fast-tier quotes (Tier 2: small watched set, ~10s cadence) ──

# Deliberately much longer than the refresh interval. Staleness is judged from
# each quote's own timestamp, never from key expiry — so one missed tick must
# not empty the map and make the UI fall back to the 60s tape. This TTL is only
# the backstop for a loop that has died outright.
TTL_FAST_QUOTES = 300


def fast_quotes_key() -> str:
    return "live:quotes:fast"


def get_fast_quotes() -> dict | None:
    """Shape: {"quotes": {SYMBOL: {price, change, change_pct, volume, timestamp}},
    "fetched_at": iso8601}. Each quote carries its own timestamp, so a symbol
    that failed to refresh can be aged out individually rather than the batch
    being treated as uniformly fresh.
    """
    return _get_json(fast_quotes_key())


def set_fast_quotes(quotes: dict, fetched_at: str) -> None:
    _set_json(fast_quotes_key(), {"quotes": quotes, "fetched_at": fetched_at}, TTL_FAST_QUOTES)


# ── Market overview (Home dashboard) ──

# Derived entirely from stored daily bars, which only change when the 20:00
# ingestion writes a new session — so this cannot go stale mid-session. The key
# carries the session date, which is what actually invalidates it; the TTL is
# only a backstop so a stale session's entry cannot live forever.
TTL_MARKET_OVERVIEW = 3600


def market_overview_key(as_of: str, limit: int) -> str:
    return f"cache:market_overview:{as_of}:{limit}"


def get_market_overview_cache(as_of: str, limit: int) -> dict | None:
    return _get_json(market_overview_key(as_of, limit))


def set_market_overview_cache(as_of: str, limit: int, value: dict) -> None:
    _set_json(market_overview_key(as_of, limit), value, TTL_MARKET_OVERVIEW)
