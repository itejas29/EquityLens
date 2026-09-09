"""Sliding-window rate limit (100 req/min) on analysis endpoints —
POST /portfolio/analyze, /stocks/{symbol}/score, /scoring/run-universe,
/backtest, and GET /recommendations. Keyed by user id when authenticated,
else by client IP, so unauthenticated analysis endpoints are still limited.

True sliding window via a Redis sorted set (member=request timestamp): each
call adds itself, trims anything older than the window, then counts what's
left — unlike a fixed window this can't let through a 2x burst across a
window boundary.

FAILS CLOSED. core/cache.py deliberately treats an unreachable Redis as a cache
miss; this module must not, and the difference is the point. Every endpoint
behind this limiter is one of the expensive ones — a full-universe scoring
pass, a backtest, a portfolio analysis — so "allow everything because the
counter is down" removes the protection at exactly the moment the system is
already unhealthy. A 503 (retryable, with Retry-After) is the honest answer:
the limiter cannot say whether this request is within budget, so it does not
pretend to. Read paths keep working throughout, because they degrade instead.

The unhandled RedisError this replaces produced a 500, which said "we are
broken" when the correct signal was "try again shortly".
"""

import logging
import secrets
import time

from fastapi import Request, status
from jose import JWTError, jwt
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.redis_client import redis_client

logger = logging.getLogger(__name__)

RATE_LIMIT_MAX_REQUESTS = 100
RATE_LIMIT_WINDOW_SECONDS = 60

# Credential endpoints get their own, much tighter budget. bcrypt at the
# default cost takes ~214 ms of CPU per verification on this box, measured — so
# roughly 9 concurrent login attempts saturate both vCPUs of a t3.small
# indefinitely. That is an unauthenticated denial of service costing the
# attacker nothing, on a host that has already had four resource incidents. It
# is also the brute-force control: 20/min per IP against an 8-character minimum
# is not a practical guessing rate.
AUTH_RATE_LIMIT_MAX_REQUESTS = 20
AUTH_RATE_LIMIT_WINDOW_SECONDS = 60


def _resolve_identity(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]
        try:
            payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
            user_id = payload.get("sub")
            if user_id:
                return f"user:{user_id}"
        except JWTError:
            pass
    client_host = request.client.host if request.client else "unknown"
    return f"ip:{client_host}"


class RateLimiterUnavailable(Exception):
    """Redis could not be reached, so the request budget is unknowable."""


def _check_and_record(identity: str, bucket: str = "", max_requests: int = RATE_LIMIT_MAX_REQUESTS,
                      window_seconds: int = RATE_LIMIT_WINDOW_SECONDS) -> bool:
    now = time.time()
    key = f"ratelimit:{bucket}{identity}"

    pipe = redis_client.pipeline()
    # A random suffix, not id(now): CPython reuses object ids, so two requests
    # landing in the same microsecond could produce the same member and zadd
    # would overwrite one instead of counting it — an undercount, in the
    # direction that lets traffic through.
    pipe.zadd(key, {f"{now:.6f}:{secrets.token_hex(4)}": now})
    pipe.zremrangebyscore(key, 0, now - window_seconds)
    pipe.zcard(key)
    pipe.expire(key, window_seconds)
    try:
        _, _, count, _ = pipe.execute()
    except RedisError as exc:
        raise RateLimiterUnavailable(str(exc)) from exc
    return count <= max_requests


_RETRY_AFTER = {"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)}


def rate_limit_analysis(request: Request) -> None:
    identity = _resolve_identity(request)
    try:
        within_budget = _check_and_record(identity)
    except RateLimiterUnavailable as exc:
        logger.warning("rate limiter unavailable, refusing analysis request: %s", exc)
        raise AppError(
            "Analysis endpoints are temporarily unavailable — the rate limiter cannot be reached. "
            "Read-only endpoints are unaffected.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers=_RETRY_AFTER,
        )

    if not within_budget:
        raise AppError(
            f"Rate limit exceeded — max {RATE_LIMIT_MAX_REQUESTS} requests/min on analysis endpoints",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers=_RETRY_AFTER,
        )


_AUTH_RETRY_AFTER = {"Retry-After": str(AUTH_RATE_LIMIT_WINDOW_SECONDS)}


def rate_limit_auth(request: Request) -> None:
    """Throttle /auth/login and /auth/register. Always keyed by IP.

    Not _resolve_identity(): these endpoints are how you GET a token, so there
    is nothing to key on but the caller's address, and keying a login limiter
    on a token the caller supplies would let an attacker rotate past it.

    FAILS OPEN, which is the opposite of rate_limit_analysis and a deliberate
    split rather than an oversight. Failing closed here means an Upstash blip
    locks the group out of their own app entirely — no login, no recovery, and
    no way to see why. Failing open leaves brute-force protection down for the
    duration of that outage, but the remaining controls (bcrypt's cost, the
    8-character minimum, no user enumeration) still hold, and the window is
    bounded by how long Redis stays unreachable. Lockout is the worse failure
    for a private tool with a handful of users; the trade is logged at ERROR so
    it is never silent.
    """
    client_host = request.client.host if request.client else "unknown"
    try:
        within_budget = _check_and_record(
            f"ip:{client_host}",
            bucket="auth:",
            max_requests=AUTH_RATE_LIMIT_MAX_REQUESTS,
            window_seconds=AUTH_RATE_LIMIT_WINDOW_SECONDS,
        )
    except RateLimiterUnavailable as exc:
        logger.error(
            "rate limiter unavailable — auth endpoints are UNTHROTTLED until Redis returns: %s", exc
        )
        return

    if not within_budget:
        raise AppError(
            f"Too many attempts — max {AUTH_RATE_LIMIT_MAX_REQUESTS} per minute.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers=_AUTH_RETRY_AFTER,
        )
