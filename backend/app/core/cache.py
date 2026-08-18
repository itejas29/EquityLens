"""Redis caching helpers.

Cached: scored universe (1h — percentile ranks don't meaningfully shift
minute to minute), stock detail payload (15m), backtest results by config
hash (24h — identical config always produces identical point-in-time
results, so this is a pure cache, not a staleness risk).

Never cached: live price lookups, user portfolios, auth — anything where a
stale read would show a user wrong money or let a stale session survive
past a real state change.
"""

import hashlib
import json
from typing import Any

from app.core.redis_client import redis_client

TTL_SCORED_UNIVERSE = 3600
TTL_STOCK_DETAIL = 900
TTL_BACKTEST = 86400


def _get_json(key: str) -> Any | None:
    raw = redis_client.get(key)
    if raw is None:
        return None
    return json.loads(raw)


def _set_json(key: str, value: Any, ttl: int) -> None:
    redis_client.set(key, json.dumps(value), ex=ttl)


def scored_universe_key() -> str:
    return "cache:scored_universe"


def get_scored_universe_cache() -> list[dict] | None:
    return _get_json(scored_universe_key())


def set_scored_universe_cache(value: list[dict]) -> None:
    _set_json(scored_universe_key(), value, TTL_SCORED_UNIVERSE)


def invalidate_scored_universe_cache() -> None:
    redis_client.delete(scored_universe_key())


def stock_detail_key(symbol: str) -> str:
    return f"cache:stock_detail:{symbol.upper()}"


def get_stock_detail_cache(symbol: str) -> dict | None:
    return _get_json(stock_detail_key(symbol))


def set_stock_detail_cache(symbol: str, value: dict) -> None:
    _set_json(stock_detail_key(symbol), value, TTL_STOCK_DETAIL)


def invalidate_stock_detail_cache(symbol: str) -> None:
    redis_client.delete(stock_detail_key(symbol))


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
