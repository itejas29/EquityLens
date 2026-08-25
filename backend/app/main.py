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
    weekly_universe_rebuild_loop,
)

# Set up logging before anything else
setup_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the background tasks; cancel them on shutdown."""
    tasks = [
        asyncio.create_task(price_refresh_loop(), name="live-price-refresh"),
        asyncio.create_task(fast_quote_loop(), name="fast-quote-refresh"),
        asyncio.create_task(daily_price_update_loop(), name="daily-price-update"),
        asyncio.create_task(daily_signals_loop(), name="daily-signals"),
        asyncio.create_task(weekly_universe_rebuild_loop(), name="weekly-universe-rebuild"),
        asyncio.create_task(fundamentals_refresh_loop(), name="fundamentals-refresh"),
        asyncio.create_task(ai_trading_loop(), name="ai-trading"),
    ]
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
