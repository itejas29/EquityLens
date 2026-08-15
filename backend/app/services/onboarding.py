"""On-demand ingestion: turn a catalogue entry into an analysable stock.

The app holds two populations — every NSE ticker (searchable, ~2,400 rows,
loaded from one CSV) and the much smaller set with real price history behind
it (analysable). This module closes the gap for a single symbol at a time,
when someone actually asks for it.

Doing it on demand rather than bulk-seeding the exchange is a deliberate
trade. Bulk-seeding ~2,400 symbols is 3 market-data calls each behind a rate
limiter — hours of wall time, near-certain throttling part way through, and
most of the result would be illiquid names nobody opens. Paying ~3-6 seconds
once, the first time a symbol is opened, spreads that cost over actual use and
keeps the stored universe to things people care about.
"""

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.models.stock import Stock
from app.services.indicators import compute_indicators, fetch_benchmark_df, load_price_history_df
from app.services.ingestion import upsert_fundamentals, upsert_indicators, upsert_price_history, upsert_stock
from app.services.market_data import (
    SymbolNotFoundError,
    fetch_fundamentals,
    fetch_price_history,
    fetch_stock_meta,
)

logger = logging.getLogger(__name__)


class IngestionError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def is_ingested(db: Session, symbol: str) -> Stock | None:
    return db.query(Stock).filter(Stock.symbol == symbol.strip().upper()).first()


def ingest_symbol(db: Session, symbol: str, period: str = "2y") -> tuple[Stock, dict]:
    """Fetch, store and index one symbol. Returns (stock, report).

    Indicators are computed in the same pass: a stock with price rows but no
    indicator rows cannot be scored, so leaving that to a second call would
    make a freshly-ingested symbol look broken rather than new.

    Does NOT commit — the caller owns the transaction boundary.
    """
    symbol = symbol.strip().upper()

    try:
        meta = fetch_stock_meta(symbol)
        history = fetch_price_history(symbol, period=period)
        fundamentals = fetch_fundamentals(symbol)
    except SymbolNotFoundError:
        raise IngestionError(
            f"'{symbol}' is listed on NSE but the market data provider returned no data for it. "
            "This usually means the ticker was recently renamed or the listing is too new."
        )

    stock = upsert_stock(db, symbol, meta)
    price_rows = upsert_price_history(db, stock.id, history)
    upsert_fundamentals(db, stock.id, fundamentals["data"], date.today())
    db.flush()

    indicator_rows = 0
    price_df = load_price_history_df(db, stock.id)
    if not price_df.empty:
        benchmark_df = fetch_benchmark_df(period=period)
        indicators = compute_indicators(price_df, benchmark_df)
        indicator_rows = upsert_indicators(db, stock.id, indicators)

    db.flush()
    logger.info("Ingested %s — %d price rows, %d indicator rows", symbol, price_rows, indicator_rows)

    return stock, {
        "symbol": symbol,
        "price_rows": price_rows,
        "indicator_rows": indicator_rows,
        "missing_fundamentals": fundamentals["missing_fields"],
    }
