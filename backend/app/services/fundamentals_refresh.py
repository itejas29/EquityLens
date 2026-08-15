"""Monthly fundamentals refresh — isolated from the daily price pipeline.

Fetches yfinance fundamentals (P/E, P/B, ROE, debt-to-equity, growth rates,
margins) for all active stocks. Runs monthly rather than nightly because this
data changes at most quarterly and the serial yfinance calls take ~7.5 min
for 500 stocks.

Uses the same fetch_fundamentals() and upsert_fundamentals() functions as
the full universe rebuild — no logic change, only scheduling isolation.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import date as date_type

from sqlalchemy.orm import Session

from app.core.ingestion_status import IngestionStatus
from app.core.universe_config import FUNDAMENTALS_DELAY_SECONDS
from app.models.stock import Stock
from app.services.ingestion import upsert_fundamentals, upsert_stock
from app.services.market_data import SymbolNotFoundError, fetch_fundamentals, fetch_stock_meta

logger = logging.getLogger(__name__)


@dataclass
class FundamentalsSymbolResult:
    symbol: str
    status: IngestionStatus
    error: str | None = None


@dataclass
class FundamentalsResult:
    requested: int = 0
    succeeded: int = 0
    failed: int = 0
    results: list[FundamentalsSymbolResult] = field(default_factory=list)
    seconds: float = 0.0


def refresh_fundamentals(db: Session) -> FundamentalsResult:
    """Fetch fundamentals for all active stocks. Intended to run monthly."""
    started = time.time()
    result = FundamentalsResult()

    stocks = (
        db.query(Stock)
        .filter(Stock.is_active == True)  # noqa: E712
        .order_by(Stock.symbol)
        .all()
    )
    result.requested = len(stocks)

    logger.info("pipeline.fundamentals.start stocks=%d", len(stocks))

    for i, stock in enumerate(stocks, start=1):
        try:
            meta = fetch_stock_meta(stock.symbol)
            upsert_stock(db, stock.symbol, meta)

            fundamentals = fetch_fundamentals(stock.symbol)
            upsert_fundamentals(db, stock.id, fundamentals["data"], date_type.today())
            db.commit()

            result.succeeded += 1
            result.results.append(FundamentalsSymbolResult(stock.symbol, IngestionStatus.SUCCESS))

        except SymbolNotFoundError:
            db.rollback()
            result.failed += 1
            result.results.append(
                FundamentalsSymbolResult(stock.symbol, IngestionStatus.RATE_LIMITED, error="symbol not found (likely throttled)")
            )
            logger.warning("pipeline.fundamentals.failure symbol=%s status=RATE_LIMITED", stock.symbol)

        except ConnectionError as exc:
            db.rollback()
            result.failed += 1
            result.results.append(
                FundamentalsSymbolResult(stock.symbol, IngestionStatus.NETWORK_ERROR, error=str(exc))
            )
            logger.warning("pipeline.fundamentals.failure symbol=%s status=NETWORK_ERROR", stock.symbol)

        except Exception as exc:
            db.rollback()
            result.failed += 1
            result.results.append(
                FundamentalsSymbolResult(stock.symbol, IngestionStatus.PROVIDER_ERROR, error=str(exc))
            )
            logger.error("pipeline.fundamentals.failure symbol=%s status=PROVIDER_ERROR error=%s", stock.symbol, exc)

        if i % 50 == 0:
            logger.info("pipeline.fundamentals.progress %d/%d", i, len(stocks))

        time.sleep(FUNDAMENTALS_DELAY_SECONDS)

    result.seconds = time.time() - started
    logger.info(
        "pipeline.fundamentals.finish succeeded=%d failed=%d duration=%.1fs",
        result.succeeded, result.failed, result.seconds,
    )
    return result
