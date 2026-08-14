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
