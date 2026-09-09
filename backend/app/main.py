import asyncio
import logging
from contextlib import asynccontextmanager

import anyio.to_thread
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import THREADPOOL_LIMIT
from app.core.exceptions import register_exception_handlers
from app.core.logging_config import setup_logging
from app.core.scheduler import (
    ai_trading_loop,
    daily_price_update_loop,
    daily_signals_loop,
    fast_quote_loop,
    fundamentals_refresh_loop,
    health_watchdog_loop,
    price_refresh_loop,
    supervise,
    weekly_universe_rebuild_loop,
)

# Set up logging before anything else
setup_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the background tasks; cancel them on shutdown."""
    # Cap the worker threadpool that FastAPI runs `def` endpoints in. AnyIO
    # defaults it to 40, and every one of those threads can hold a database
    # Session — against a pool that tops out at 30. Left as-is the two
    # disagree, and the disagreement surfaces as requests queueing on the
    # connection pool under load rather than as honest backpressure. See the
    # sizing note in core/database.py; the two numbers are meant to be read
    # together and changed together.
    anyio.to_thread.current_default_thread_limiter().total_tokens = THREADPOOL_LIMIT
    logging.getLogger(__name__).info(
        "threadpool capped at %d workers to match the DB pool", THREADPOOL_LIMIT
    )

    # Every loop runs under supervise(), which logs and restarts it if it ever
    # exits. Holding these task references for the process lifetime is what
    # previously made a dying loop invisible: Python only warns about an
    # unretrieved task exception from Task.__del__, which never runs while a
    # reference is held. See the loop-liveness note in core/scheduler.py.
    loops = [
        ("live-price-refresh", price_refresh_loop),
        ("fast-quote-refresh", fast_quote_loop),
        ("daily-price-update", daily_price_update_loop),
        ("daily-signals", daily_signals_loop),
        ("weekly-universe-rebuild", weekly_universe_rebuild_loop),
        ("fundamentals-refresh", fundamentals_refresh_loop),
        ("ai-trading", ai_trading_loop),
        ("health-watchdog", health_watchdog_loop),
    ]
    tasks = [asyncio.create_task(supervise(name, factory), name=name) for name, factory in loops]
    yield
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="EquityLens API", lifespan=lifespan)

# allow_credentials is False on purpose. Authentication here is a bearer token
# read from localStorage and set on the Authorization header — no cookies are
# used in either direction. Leaving credentials on costs nothing today but
# makes the "*" origin case genuinely dangerous the moment someone sets it,
# because Starlette then reflects whatever Origin the browser sent. Settings
# rejects "*" outright for the same reason; this is the second lock.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

app.include_router(api_router, prefix="/api/v1")
