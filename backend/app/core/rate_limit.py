"""Sliding-window rate limit (100 req/min) on analysis endpoints —
POST /portfolio/analyze, /stocks/{symbol}/score, /scoring/run-universe,
/backtest, and GET /recommendations. Keyed by user id when authenticated,
else by client IP, so unauthenticated analysis endpoints are still limited.

True sliding window via a Redis sorted set (member=request timestamp): each
call adds itself, trims anything older than the window, then counts what's
left — unlike a fixed window this can't let through a 2x burst across a
window boundary.
"""

import time

from fastapi import Request, status
from jose import JWTError, jwt

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.redis_client import redis_client

RATE_LIMIT_MAX_REQUESTS = 100
RATE_LIMIT_WINDOW_SECONDS = 60


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


def _check_and_record(identity: str) -> bool:
    now = time.time()
    key = f"ratelimit:{identity}"

    pipe = redis_client.pipeline()
    pipe.zadd(key, {f"{now:.6f}:{id(now)}": now})
    pipe.zremrangebyscore(key, 0, now - RATE_LIMIT_WINDOW_SECONDS)
    pipe.zcard(key)
    pipe.expire(key, RATE_LIMIT_WINDOW_SECONDS)
    _, _, count, _ = pipe.execute()
    return count <= RATE_LIMIT_MAX_REQUESTS


def rate_limit_analysis(request: Request) -> None:
    identity = _resolve_identity(request)
    if not _check_and_record(identity):
        raise AppError(
            f"Rate limit exceeded — max {RATE_LIMIT_MAX_REQUESTS} requests/min on analysis endpoints",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
