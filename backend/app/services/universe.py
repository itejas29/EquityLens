"""Two-stage universe construction over the whole NSE equity list.

Stage 1 — screen: batch-download a short price window for every tradeable
listing, compute 20-day average traded value, keep what clears the liquidity
floor. Nothing is written to the database; this stage exists purely to decide
what is worth the expensive second stage.

Stage 2 — ingest: for the survivors, pull full history (batched) and
fundamentals (one call each, the only unavoidably serial part), store, and
compute indicators.

See app/core/universe_config.py for why it is staged rather than ingesting
everything.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import date

import pandas as pd
import yfinance as yf
from sqlalchemy.orm import Session

from app.core.universe_config import (
    DOWNLOAD_BATCH_SIZE,
    FUNDAMENTALS_DELAY_SECONDS,
    HISTORY_PERIOD,
    LIQUIDITY_WINDOW_DAYS,
    MAX_UNIVERSE_SIZE,
    MIN_AVG_TRADED_VALUE,
    MIN_SCREEN_BARS,
    SCREEN_PERIOD,
    STAGE_GAP_SECONDS,
)
from app.models.indicator import Indicator
from app.models.nse_symbol import NseSymbol
from app.models.stock import Stock
from app.services.catalogue import TRADEABLE_SERIES
from app.services.indicators import compute_indicators, fetch_benchmark_df, load_price_history_df
from app.services.ingestion import upsert_fundamentals, upsert_indicators, upsert_price_history, upsert_stock
from app.services.market_data import fetch_fundamentals, fetch_stock_meta

logger = logging.getLogger(__name__)


@dataclass
class ScreenResult:
    considered: int
    with_data: int
    passed: int
    ranked: list[tuple[str, float]] = field(default_factory=list)  # (symbol, avg traded value)


@dataclass
class BuildResult:
    screen: ScreenResult
    ingested: int
    failed: list[tuple[str, str]] = field(default_factory=list)
    deactivated: int = 0
    seconds: float = 0.0


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _download_batch(symbols: list[str], period: str) -> dict[str, pd.DataFrame]:
    """Batched yfinance download → {symbol: OHLCV frame}. A batch that fails
    wholesale returns empty rather than raising, so one bad chunk cannot abort
    a run over thousands of symbols.
    """
    tickers = [f"{s}.NS" for s in symbols]
    from app.services.market_data import download_batch_with_retry
    try:
        raw = download_batch_with_retry(
            tickers=tickers,
            period=period,
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
        )
    except Exception as exc:
        logger.warning("Batch download failed for %d symbols: %s", len(symbols), exc)
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


def screen_by_liquidity(db: Session) -> ScreenResult:
    """Stage 1. Rank the whole exchange by 20-day average traded value."""
    symbols = [
        s.symbol
        for s in db.query(NseSymbol).filter(NseSymbol.series.in_(TRADEABLE_SERIES)).order_by(NseSymbol.symbol).all()
    ]
    logger.info("Liquidity screen over %d tradeable listings", len(symbols))

    ranked: list[tuple[str, float]] = []
    with_data = 0

    for batch in _chunks(symbols, DOWNLOAD_BATCH_SIZE):
        frames = _download_batch(batch, SCREEN_PERIOD)
        with_data += len(frames)
        for symbol, df in frames.items():
            if len(df) < MIN_SCREEN_BARS:
                continue
            recent = df.tail(LIQUIDITY_WINDOW_DAYS)
            traded = (recent["Close"] * recent["Volume"]).dropna()
            if traded.empty:
                continue
            avg = float(traded.mean())
            if avg >= MIN_AVG_TRADED_VALUE:
                ranked.append((symbol, avg))

    ranked.sort(key=lambda r: r[1], reverse=True)
    ranked = ranked[:MAX_UNIVERSE_SIZE]

    logger.info(
        "Screen complete — %d listings, %d returned data, %d cleared ₹%.0f/day floor (capped at %d)",
        len(symbols), with_data, len(ranked), MIN_AVG_TRADED_VALUE, MAX_UNIVERSE_SIZE,
    )
    return ScreenResult(considered=len(symbols), with_data=with_data, passed=len(ranked), ranked=ranked)


def _get_or_create_stock(db: Session, symbol: str) -> Stock:
    """Row lookup that never touches metadata — see the call site for why."""
    stock = db.query(Stock).filter(Stock.symbol == symbol).first()
    if stock is None:
        stock = Stock(symbol=symbol, is_active=True)
        db.add(stock)
        db.flush()
    return stock


def _ingest_prices_batched(db: Session, symbols: list[str]) -> tuple[dict[str, int], list[tuple[str, str]]]:
    stored: dict[str, int] = {}
    failed: list[tuple[str, str]] = []

    for batch in _chunks(symbols, DOWNLOAD_BATCH_SIZE):
        frames = _download_batch(batch, HISTORY_PERIOD)
        for symbol in batch:
            df = frames.get(symbol)
            if df is None:
                failed.append((symbol, "no price data returned"))
                continue
            try:
                # Match fetch_price_history's output exactly: date coerced to a
                # plain date (yfinance returns tz-aware timestamps here) and the
                # columns renamed and ordered the same way.
                normalised = df.reset_index()
                normalised["Date"] = pd.to_datetime(normalised["Date"]).dt.date
                normalised = normalised[["Date", "Open", "High", "Low", "Close", "Volume"]].rename(
                    columns={
                        "Date": "date", "Open": "open", "High": "high",
                        "Low": "low", "Close": "close", "Volume": "volume",
                    }
                )
                # NOT upsert_stock — that writes every metadata field from the
                # dict it is given, so passing an empty one here would blank
                # company_name/sector on already-ingested stocks. Metadata is
                # set in stage 2, where it is actually fetched.
                stock = _get_or_create_stock(db, symbol)
                stored[symbol] = upsert_price_history(db, stock.id, normalised)
            except Exception as exc:
                db.rollback()
                failed.append((symbol, f"price store failed: {exc}"))
        db.commit()

    return stored, failed


def _ingest_fundamentals_and_indicators(db: Session, symbols: list[str]) -> list[tuple[str, str]]:
    """The serial part. Fundamentals have no batch endpoint, and indicators need
    the benchmark frame, which is fetched once and reused across every symbol.
    """
    failed: list[tuple[str, str]] = []
    benchmark_df = fetch_benchmark_df(period=HISTORY_PERIOD)

    for i, symbol in enumerate(symbols, start=1):
        stock = db.query(Stock).filter(Stock.symbol == symbol).first()
        if stock is None:
            continue
        try:
            meta = fetch_stock_meta(symbol)
            stock = upsert_stock(db, symbol, meta)

            fundamentals = fetch_fundamentals(symbol)
            upsert_fundamentals(db, stock.id, fundamentals["data"], date.today())
            db.flush()

            price_df = load_price_history_df(db, stock.id)
            if not price_df.empty:
                upsert_indicators(db, stock.id, compute_indicators(price_df, benchmark_df))
            db.commit()
        except Exception as exc:
            db.rollback()
            failed.append((symbol, f"fundamentals/indicators failed: {exc}"))

        if i % 50 == 0:
            logger.info("  fundamentals+indicators: %d/%d", i, len(symbols))
        time.sleep(FUNDAMENTALS_DELAY_SECONDS)

    return failed


def deepen_history(db: Session) -> BuildResult:
    """Re-pull prices for the existing active universe at HISTORY_PERIOD and
    recompute indicators. Fundamentals are untouched.

    Separate from build_universe because it skips both the 14-minute liquidity
    screen and the serial fundamentals stage — neither is needed when the
    membership is already decided and only the depth of history is changing.
    """
    started = time.time()
    symbols = [
        s.symbol
        for s in db.query(Stock).filter(Stock.is_active == True).order_by(Stock.symbol).all()  # noqa: E712
    ]
    logger.info("Deepening history to %s for %d active stocks", HISTORY_PERIOD, len(symbols))
    if not symbols:
        return BuildResult(screen=ScreenResult(0, 0, 0), ingested=0, seconds=time.time() - started)

    _, price_failures = _ingest_prices_batched(db, symbols)
    priced = [s for s in symbols if not any(f[0] == s for f in price_failures)]

    # Indicators only — the fundamentals snapshot does not change with history
    # depth, and re-fetching it for 500 symbols would add ~8 minutes for nothing.
    failures: list[tuple[str, str]] = list(price_failures)
    benchmark_df = fetch_benchmark_df(period=HISTORY_PERIOD)
    for i, symbol in enumerate(priced, start=1):
        stock = db.query(Stock).filter(Stock.symbol == symbol).first()
        if stock is None:
            continue
        try:
            price_df = load_price_history_df(db, stock.id)
            if not price_df.empty:
                upsert_indicators(db, stock.id, compute_indicators(price_df, benchmark_df))
            db.commit()
        except Exception as exc:
            db.rollback()
            failures.append((symbol, f"indicator recompute failed: {exc}"))
        if i % 100 == 0:
            logger.info("  indicators: %d/%d", i, len(priced))

    result = BuildResult(
        screen=ScreenResult(len(symbols), len(symbols), len(priced)),
        ingested=len(priced),
        failed=failures,
        seconds=time.time() - started,
    )
    logger.info("History deepened in %.0fs — %d stocks, %d failures", result.seconds, result.ingested, len(failures))
    return result


def repair_missing_details(db: Session) -> BuildResult:
    """Re-run stage 2 for active stocks that have prices but no indicators.

    Exists because stage 2's failure mode is partial and recoverable: a
    throttled `Ticker.info` call surfaces as SymbolNotFoundError, so a symbol
    can end up with a full price history and nothing else. Repairing those
    directly avoids repeating the 14-minute screen just to fix a handful.
    """
    started = time.time()
    symbols = [
        s.symbol
        for s in db.query(Stock)
        .filter(Stock.is_active == True)  # noqa: E712
        .order_by(Stock.symbol)
        .all()
        if not db.query(Indicator).filter(Indicator.stock_id == s.id).first()
    ]
    logger.info("Repairing %d active stocks with prices but no indicators", len(symbols))
    if not symbols:
        return BuildResult(screen=ScreenResult(0, 0, 0), ingested=0, seconds=time.time() - started)

    failures = _ingest_fundamentals_and_indicators(db, symbols)
    repaired = len(symbols) - len(failures)
    logger.info("Repair finished — %d fixed, %d still failing", repaired, len(failures))
    return BuildResult(
        screen=ScreenResult(len(symbols), len(symbols), len(symbols)),
        ingested=repaired,
        failed=failures,
        seconds=time.time() - started,
    )


def build_universe(db: Session) -> BuildResult:
    """Full rebuild: screen the exchange, then ingest what survives.

    - Dropped stocks: marked is_active=False.
    - Newly added stocks: 10y price pull, fundamentals, indicators, marked is_active=True.
    - Existing kept stocks: unchanged here (handled by daily incremental job).
    """
    started = time.time()

    screen = screen_by_liquidity(db)
    keep_list = [symbol for symbol, _ in screen.ranked]
    if not keep_list:
        return BuildResult(screen=screen, ingested=0, seconds=time.time() - started)

    keep_set = set(keep_list)
    current_active_stocks = db.query(Stock).filter(Stock.is_active == True).all()  # noqa: E712
    current_active_symbols = {s.symbol for s in current_active_stocks}

    newly_added = [sym for sym in keep_list if sym not in current_active_symbols]
    dropped = [sym for sym in current_active_symbols if sym not in keep_set]
    existing_kept = [sym for sym in keep_list if sym in current_active_symbols]

    logger.info(
        "Universe refresh: %d to keep, %d new, %d dropped, %d existing carried over",
        len(keep_list), len(newly_added), len(dropped), len(existing_kept)
    )

    price_failures = []
    detail_failures = []
    priced = []

    if newly_added:
        logger.info("Ingesting 10-year history for %d newly added symbols", len(newly_added))
        _, price_failures = _ingest_prices_batched(db, newly_added)
        priced = [s for s in newly_added if not any(f[0] == s for f in price_failures)]
        
        if priced:
            time.sleep(STAGE_GAP_SECONDS)
            detail_failures = _ingest_fundamentals_and_indicators(db, priced)

    # Apply changes to DB
    for stock in current_active_stocks:
        if stock.symbol in dropped:
            stock.is_active = False

    # newly_added that failed ingestion are not activated
    failed_new = {f[0] for f in price_failures + detail_failures}
    successful_new = set(priced) - failed_new

    for sym in successful_new:
        stock = db.query(Stock).filter(Stock.symbol == sym).first()
        if stock:
            stock.is_active = True

    db.commit()

    result = BuildResult(
        screen=screen,
        ingested=len(successful_new) + len(existing_kept),
        failed=price_failures + detail_failures,
        deactivated=len(dropped),
        seconds=time.time() - started,
    )
    logger.info(
        "Universe rebuilt in %.0fs — %d active, %d deactivated, %d failures",
        result.seconds, result.ingested, result.deactivated, len(result.failed),
    )
    return result
