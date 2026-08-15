"""Incremental price ingestion — fetch only new bars since each stock's last
stored date, then recompute indicators.

Replaces the nightly full-history re-download with a targeted gap-fill that
typically takes 2–5 minutes instead of 30+. The full-rebuild path in
services/universe.py is preserved for initial setup, recovery, and the weekly
universe-membership rebuild.

IDEMPOTENCY: running this twice produces the same DB state because
upsert_price_history uses ON CONFLICT DO UPDATE on (stock_id, date).

INDICATOR RECOMPUTATION: indicators (RSI, MACD, beta, etc.) are recomputed
from the full stored price series, not just the new bars, because several
indicators (Wilder RSI, MACD EMA) are recursive and require the complete
history. The expensive part is the yfinance download, not the numpy math.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import date as date_type, timedelta

import pandas as pd
import yfinance as yf
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.ingestion_status import IngestionStatus
from app.core.universe_config import DOWNLOAD_BATCH_SIZE, HISTORY_PERIOD
from app.models.indicator import Indicator
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.services.indicators import compute_indicators, fetch_benchmark_df, load_price_history_df
from app.services.ingestion import upsert_indicators, upsert_price_history

logger = logging.getLogger(__name__)


@dataclass
class SymbolResult:
    symbol: str
    status: IngestionStatus
    bars_added: int = 0
    latest_date: date_type | None = None
    error: str | None = None


@dataclass
class IncrementalResult:
    requested: int = 0
    succeeded: int = 0
    already_current: int = 0
    failed: int = 0
    results: list[SymbolResult] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def status(self) -> str:
        if self.failed == 0:
            return "complete"
        if self.succeeded > 0:
            return "incomplete"
        return "failed"


def _get_latest_dates(db: Session, stock_ids: list[int]) -> dict[int, date_type | None]:
    """Per-stock latest stored price date. A single SQL query, not N+1."""
    if not stock_ids:
        return {}
    rows = (
        db.query(PriceHistory.stock_id, func.max(PriceHistory.date))
        .filter(PriceHistory.stock_id.in_(stock_ids))
        .group_by(PriceHistory.stock_id)
        .all()
    )
    return {stock_id: max_date for stock_id, max_date in rows}


def _classify_missing_symbol(symbol: str, has_prior_data: bool) -> IngestionStatus:
    """When yfinance returns nothing for a symbol, decide whether it was
    throttled or genuinely not found."""
    if has_prior_data:
        # We had data for this symbol before. yfinance returning nothing now
        # is almost certainly rate limiting, not a delisting.
        return IngestionStatus.RATE_LIMITED
    return IngestionStatus.NOT_FOUND


def _download_incremental_batch(
    symbols: list[str],
    start_date: date_type,
    end_date: date_type,
) -> dict[str, pd.DataFrame]:
    """Batched yfinance download for a date range.

    Returns {symbol: OHLCV DataFrame} for symbols that returned data.
    """
    if not symbols:
        return {}

    tickers = [f"{s}.NS" for s in symbols]

    try:
        from app.services.market_data import download_batch_with_retry
        raw = download_batch_with_retry(
            tickers=tickers,
            start=start_date.isoformat(),
            end=(end_date + timedelta(days=1)).isoformat(),  # yfinance end is exclusive
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
        )
    except Exception as exc:
        logger.warning("pipeline.incremental.batch_provider_error error=%s", exc)
        return {}

    out: dict[str, pd.DataFrame] = {}
    for symbol, ticker in zip(symbols, tickers):
        try:
            df = raw[ticker] if len(tickers) > 1 else raw
            if df is None or df.empty:
                continue
            df = df.dropna(subset=["Close"])
            if df.empty:
                continue
            out[symbol] = df
        except (KeyError, IndexError):
            continue
    return out


def _normalise_df(df: pd.DataFrame) -> pd.DataFrame:
    """Convert yfinance download output to the shape upsert_price_history expects."""
    normalised = df.reset_index()
    normalised["Date"] = pd.to_datetime(normalised["Date"]).dt.date
    normalised = normalised[["Date", "Open", "High", "Low", "Close", "Volume"]].rename(
        columns={
            "Date": "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    return normalised


def incremental_price_update(db: Session, today: date_type | None = None) -> IncrementalResult:
    """Fetch only missing bars for each active stock since its latest stored date.

    For each active stock:
    1. Find its latest stored PriceHistory date.
    2. If no history at all → full pull (initial setup).
    3. If latest_date >= today → already current, skip.
    4. Otherwise → fetch only the gap.
    5. Upsert new bars.
    6. Recompute indicators from full stored history.
    """
    started = time.time()
    today = today or date_type.today()
    result = IncrementalResult()

    stocks = (
        db.query(Stock)
        .filter(Stock.is_active == True)  # noqa: E712
        .order_by(Stock.symbol)
        .all()
    )
    if not stocks:
        result.seconds = time.time() - started
        return result

    result.requested = len(stocks)
    stock_map = {s.id: s for s in stocks}
    latest_dates = _get_latest_dates(db, [s.id for s in stocks])

    # Separate stocks into: needs_full_pull (no history) and needs_incremental
    needs_full: list[Stock] = []
    needs_incremental: list[tuple[Stock, date_type]] = []  # (stock, start_date)
    already_current: list[Stock] = []

    for stock in stocks:
        latest = latest_dates.get(stock.id)
        if latest is None:
            needs_full.append(stock)
        elif latest >= today:
            already_current.append(stock)
        else:
            needs_incremental.append((stock, latest + timedelta(days=1)))

    # Record already-current stocks
    for stock in already_current:
        result.already_current += 1
        result.succeeded += 1
        result.results.append(SymbolResult(
            symbol=stock.symbol,
            status=IngestionStatus.ALREADY_CURRENT,
            latest_date=latest_dates.get(stock.id),
        ))

    logger.info(
        "pipeline.incremental.start stocks=%d needs_full=%d needs_incremental=%d already_current=%d",
        len(stocks), len(needs_full), len(needs_incremental), len(already_current),
    )

    # Fetch benchmark once for indicator computation
    benchmark_df = fetch_benchmark_df(period=HISTORY_PERIOD)

    # --- Full pulls (new stocks with no history) ---
    if needs_full:
        _process_full_pulls(db, needs_full, benchmark_df, result)

    # --- Incremental pulls ---
    if needs_incremental:
        _process_incremental_pulls(db, needs_incremental, today, benchmark_df, result)

    result.seconds = time.time() - started
    logger.info(
        "pipeline.incremental.finish succeeded=%d already_current=%d failed=%d duration=%.1fs",
        result.succeeded, result.already_current, result.failed, result.seconds,
    )
    return result


def _process_full_pulls(
    db: Session,
    stocks: list[Stock],
    benchmark_df: pd.DataFrame,
    result: IncrementalResult,
) -> None:
    """Full history pull for stocks with no stored data."""
    symbols = [s.symbol for s in stocks]
    symbol_to_stock = {s.symbol: s for s in stocks}

    for batch_idx, batch_start in enumerate(range(0, len(symbols), DOWNLOAD_BATCH_SIZE)):
        batch = symbols[batch_start : batch_start + DOWNLOAD_BATCH_SIZE]
        batch_num = batch_idx + 1
        total_batches = (len(symbols) + DOWNLOAD_BATCH_SIZE - 1) // DOWNLOAD_BATCH_SIZE

        try:
            frames = _download_full_batch(batch)
        except Exception as exc:
            logger.error("pipeline.incremental.full_batch_failed batch=%d/%d error=%s", batch_num, total_batches, exc)
            for sym in batch:
                result.failed += 1
                result.results.append(SymbolResult(sym, IngestionStatus.NETWORK_ERROR, error=str(exc)))
            continue

        for sym in batch:
            df = frames.get(sym)
            if df is None:
                status = _classify_missing_symbol(sym, has_prior_data=False)
                result.failed += 1
                result.results.append(SymbolResult(sym, status, error="no data returned"))
                logger.warning("pipeline.incremental.failure symbol=%s status=%s", sym, status.value)
                continue

            try:
                stock = symbol_to_stock[sym]
                normalised = _normalise_df(df)
                bars = upsert_price_history(db, stock.id, normalised)

                # Compute indicators from full stored history
                price_df = load_price_history_df(db, stock.id)
                if not price_df.empty:
                    upsert_indicators(db, stock.id, compute_indicators(price_df, benchmark_df))

                db.commit()
                latest = normalised["date"].max() if not normalised.empty else None
                result.succeeded += 1
                result.results.append(SymbolResult(sym, IngestionStatus.SUCCESS, bars_added=bars, latest_date=latest))
            except Exception as exc:
                db.rollback()
                result.failed += 1
                result.results.append(SymbolResult(sym, IngestionStatus.PROVIDER_ERROR, error=str(exc)))
                logger.error("pipeline.incremental.store_failed symbol=%s error=%s", sym, exc)

        logger.info(
            "pipeline.incremental.full_batch batch=%d/%d symbols=%d",
            batch_num, total_batches, len(batch),
        )


def _download_full_batch(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Batched yfinance download using period (for initial full pulls)."""
    if not symbols:
        return {}

    tickers = [f"{s}.NS" for s in symbols]
    from app.services.market_data import download_batch_with_retry
    try:
        raw = download_batch_with_retry(
            tickers=tickers,
            period=HISTORY_PERIOD,
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
        )
    except Exception as exc:
        logger.warning("pipeline.incremental.full_download_failed symbols=%d error=%s", len(symbols), exc)
        raise

    out: dict[str, pd.DataFrame] = {}
    for symbol, ticker in zip(symbols, tickers):
        try:
            df = raw[ticker] if len(tickers) > 1 else raw
            if df is not None and not df.empty:
                df = df.dropna(subset=["Close"])
                if not df.empty:
                    out[symbol] = df
        except (KeyError, IndexError):
            continue
    return out


def _process_incremental_pulls(
    db: Session,
    stocks_with_start: list[tuple[Stock, date_type]],
    end_date: date_type,
    benchmark_df: pd.DataFrame,
    result: IncrementalResult,
) -> None:
    """Incremental pulls: fetch only bars after each stock's latest stored date."""
    # Group into batches. All symbols in a batch share the same start date
    # (the oldest start date in the batch) — this over-fetches slightly for
    # recently-updated stocks but keeps the download batched.
    symbols_and_starts = [(s.symbol, start) for s, start in stocks_with_start]
    symbol_to_stock = {s.symbol: s for s, _ in stocks_with_start}

    for batch_idx, batch_start_idx in enumerate(range(0, len(symbols_and_starts), DOWNLOAD_BATCH_SIZE)):
        batch = symbols_and_starts[batch_start_idx : batch_start_idx + DOWNLOAD_BATCH_SIZE]
        batch_num = batch_idx + 1
        total_batches = (len(symbols_and_starts) + DOWNLOAD_BATCH_SIZE - 1) // DOWNLOAD_BATCH_SIZE

        batch_symbols = [sym for sym, _ in batch]
        # Use the oldest start date in this batch so we don't miss bars for any symbol.
        batch_start_date = min(start for _, start in batch)

        try:
            frames = _download_incremental_batch(batch_symbols, batch_start_date, end_date)
        except Exception as exc:
            logger.error(
                "pipeline.incremental.batch_failed batch=%d/%d error=%s",
                batch_num, total_batches, exc,
            )
            for sym in batch_symbols:
                result.failed += 1
                result.results.append(SymbolResult(sym, IngestionStatus.NETWORK_ERROR, error=str(exc)))
            continue

        succeeded_in_batch = 0
        failed_in_batch = 0

        for sym in batch_symbols:
            df = frames.get(sym)
            if df is None:
                # No data returned. If we have prior data this is likely rate limiting.
                status = _classify_missing_symbol(sym, has_prior_data=True)
                result.failed += 1
                failed_in_batch += 1
                result.results.append(SymbolResult(sym, status, error="no data in incremental window"))
                logger.warning("pipeline.incremental.failure symbol=%s status=%s", sym, status.value)
                continue

            try:
                stock = symbol_to_stock[sym]
                normalised = _normalise_df(df)
                bars = upsert_price_history(db, stock.id, normalised)

                # Recompute indicators from full stored history
                price_df = load_price_history_df(db, stock.id)
                if not price_df.empty:
                    upsert_indicators(db, stock.id, compute_indicators(price_df, benchmark_df))

                db.commit()
                latest = normalised["date"].max() if not normalised.empty else None
                result.succeeded += 1
                succeeded_in_batch += 1
                result.results.append(SymbolResult(sym, IngestionStatus.SUCCESS, bars_added=bars, latest_date=latest))
            except Exception as exc:
                db.rollback()
                result.failed += 1
                failed_in_batch += 1
                result.results.append(SymbolResult(sym, IngestionStatus.PROVIDER_ERROR, error=str(exc)))
                logger.error("pipeline.incremental.store_failed symbol=%s error=%s", sym, exc)

        logger.info(
            "pipeline.incremental.batch batch=%d/%d symbols=%d succeeded=%d failed=%d",
            batch_num, total_batches, len(batch_symbols), succeeded_in_batch, failed_in_batch,
        )
