import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging_config import setup_logging
from app.core.scheduler import (
    ai_trading_loop,
    daily_price_update_loop,
    daily_signals_loop,
    fast_quote_loop,
    fundamentals_refresh_loop,
    price_refresh_loop,
    supervise,
    weekly_universe_rebuild_loop,
)

# Set up logging before anything else
setup_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the background tasks; cancel them on shutdown."""
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

app.include_router(api_router, prefix="/api/v1")
