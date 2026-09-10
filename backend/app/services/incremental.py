"""Incremental price ingestion — fetch only new bars since each stock's last
stored date, then recompute indicators.

Replaces the nightly full-history re-download with a targeted gap-fill that
typically takes 2–5 minutes instead of 30+. The full-rebuild path in
services/universe.py is preserved for initial setup, recovery, and the weekly
universe-membership rebuild.

IDEMPOTENCY: running this twice produces the same DB state because
upsert_price_history uses ON CONFLICT DO UPDATE on (stock_id, date).

CORPORATE ACTIONS. A gap-fill has one failure mode a full re-download does not:
the provider can RESTATE history underneath it. yfinance returns split-adjusted
prices — verified against BAJFINANCE.NS's 2:1 on 2025-06-16, where the close
goes 936.85 -> 938.00 across the split rather than halving, and auto_adjust
turns out to control dividends, not splits. That adjustment is applied to the
WHOLE series retroactively. Fetching only bars after the last stored date
therefore leaves everything before the split on the old basis and everything
after it on the new one, and the stored series acquires a permanent artificial
gap the size of the split ratio.

That is not cosmetic for this app. V1 ranks on 12-month momentum, so a 2:1
split makes a stock read as -50% for a year (it never gets bought — the safe
direction), while a bonus or consolidation that reads positive puts it at the
TOP of the ranking and gets it bought on an event that never happened. Indian
bonus issues are common and Yahoo reports them as splits: NESTLEIND 10:1 in
2024 and 2:1 in 2025, BSE 3:1 twice, TRENT 1.5:1 — all inside the universe.

The fix costs no extra requests: the incremental window is extended backwards
by RESTATEMENT_OVERLAP_DAYS so it re-returns bars already stored, those are
compared against what is on disk, and any symbol whose past has moved is
re-pulled in full instead of being appended to. See _detect_restatement.

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
from app.core.memory_hygiene import trim_every
from app.core.universe_config import DOWNLOAD_BATCH_SIZE, HISTORY_PERIOD
from app.models.indicator import Indicator
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.services.indicators import compute_indicators, fetch_benchmark_df, load_price_history_df
from app.services.ingestion import upsert_indicators, upsert_price_history

logger = logging.getLogger(__name__)


# How far back the incremental window reaches beyond what is strictly needed.
# Purely to re-receive bars already on disk so they can be compared — see the
# corporate-actions note above. ~6 trading days, enough to survive a long
# weekend plus a holiday and still overlap.
RESTATEMENT_OVERLAP_DAYS = 10

# A stored and a freshly-fetched close for the SAME date should be identical.
# They are compared with a tolerance anyway because the stored value is
# Numeric(12,2) and the fetched one is a float64. 1% sits far above that noise
# and far below the smallest corporate action worth acting on (a 1.05:1 bonus
# moves the price ~4.8%).
RESTATEMENT_TOLERANCE_PCT = 1.0


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
    # A stock that needed new bars, queried without error, but got zero new
    # rows back — Yahoo has not published that session's data yet. Confirmed
    # live: a catch-up run at ~00:45 IST (9+ hours after a 15:30 close) still
    # got this for 500/501 stocks, and every one of them was being counted as
    # `succeeded` before this field existed, so the run reported "complete"
    # while almost nothing had actually updated.
    stale: int = 0
    failed: int = 0
    # Symbols whose stored history no longer matched what the provider returned
    # for the same dates — a split, bonus or other restatement. Counted, not
    # failed: each one is re-pulled in full in the same run, and the re-pull
    # reports its own SUCCESS or failure.
    restated: int = 0
    results: list[SymbolResult] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def status(self) -> str:
        if self.failed == 0 and self.stale == 0:
            return "complete"
        if self.succeeded > 0:
            return "incomplete"
        return "failed"


def _indicator_lookback_start(as_of: date_type) -> date_type:
    """Earliest date compute_indicators() can still read from. Its longest
    window is ANNUALIZATION_DAYS (252 trading days, for volatility/beta/
    max_drawdown); 1.6x plus a fixed cushion converts that to calendar days
    generously, so a holiday stretch never truncates the window. Deliberately
    matches the sizing already used for the same purpose in daily_signals.py.
    """
    from app.services.indicators import ANNUALIZATION_DAYS
    return as_of - timedelta(days=int(ANNUALIZATION_DAYS * 1.6) + 30)


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


def _stored_closes(db: Session, stock_id: int, since: date_type) -> dict[date_type, float]:
    """Closes already on disk for this stock from `since` onwards."""
    rows = (
        db.query(PriceHistory.date, PriceHistory.close)
        .filter(PriceHistory.stock_id == stock_id, PriceHistory.date >= since,
                PriceHistory.close.isnot(None))
        .all()
    )
    return {d: float(close) for d, close in rows}


def _detect_restatement(
    stored: dict[date_type, float], fetched: pd.DataFrame
) -> tuple[date_type, float, float] | None:
    """First date where the provider now disagrees with what is on disk.

    Returns (date, stored_close, fetched_close), or None if every overlapping
    date matches. A disagreement means the past changed — a split, a bonus, or
    a correction — and appending to that history would splice two different
    price bases together.

    Only dates present on BOTH sides are compared: a date we have and the
    provider no longer returns is outside the fetch window, not a restatement.
    """
    if not stored or fetched.empty:
        return None

    for row in fetched.itertuples(index=False):
        previous = stored.get(row.date)
        if previous is None or previous == 0:
            continue
        current = float(row.close)
        if abs(current - previous) / previous * 100 > RESTATEMENT_TOLERANCE_PCT:
            return row.date, previous, current
    return None


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

    # The ML cross-section is now stale — new bars mean new latest rows and
    # new cross-sectional ranks. invalidate_feature_cache() had no callers at
    # all before this, so a served frame could outlive the data by weeks.
    from app.ml.predict import invalidate_feature_cache

    invalidate_feature_cache()

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

                # Bounded to what compute_indicators actually reads (longest
                # window is ANNUALIZATION_DAYS=252 for volatility/beta/drawdown).
                # Loading unbounded full history here, once per stock across a
                # ~500-stock universe, is what pushed a genuine incremental run
                # to 412MB peak RSS against Render's 512MB limit.
                price_df = load_price_history_df(db, stock.id, since=_indicator_lookback_start(date_type.today()))
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

        trim_every(batch_num, every=1)

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
    """Incremental pulls: fetch bars after each stock's latest stored date, plus
    an overlap window used only to check that the past has not been restated."""
    # Group into batches. All symbols in a batch share the same start date
    # (the oldest start date in the batch) — this over-fetches slightly for
    # recently-updated stocks but keeps the download batched.
    symbols_and_starts = [(s.symbol, start) for s, start in stocks_with_start]
    symbol_to_stock = {s.symbol: s for s, _ in stocks_with_start}
    # Each symbol's OWN needed start date, not the batch's shared (oldest)
    # fetch-window start — a symbol further ahead in the batch must be judged
    # against what it actually needed, not another symbol's earlier gap.
    symbol_to_start = {s.symbol: start for s, start in stocks_with_start}
    # Symbols whose stored history disagreed with the provider. Collected
    # across all batches and re-pulled in full at the end, because appending to
    # a restated series splices two price bases together.
    needs_repull: list[Stock] = []

    for batch_idx, batch_start_idx in enumerate(range(0, len(symbols_and_starts), DOWNLOAD_BATCH_SIZE)):
        batch = symbols_and_starts[batch_start_idx : batch_start_idx + DOWNLOAD_BATCH_SIZE]
        batch_num = batch_idx + 1
        total_batches = (len(symbols_and_starts) + DOWNLOAD_BATCH_SIZE - 1) // DOWNLOAD_BATCH_SIZE

        batch_symbols = [sym for sym, _ in batch]
        # Oldest start in the batch so no symbol misses bars, minus the overlap
        # window. The overlap costs nothing — same request count, a handful of
        # extra daily rows — and is the only way to notice that the provider
        # has rewritten dates we already hold.
        batch_start_date = min(start for _, start in batch) - timedelta(days=RESTATEMENT_OVERLAP_DAYS)

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
        stale_in_batch = 0

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
                start_date = symbol_to_start[sym]
                normalised = _normalise_df(df)

                # Compare the overlap against what is on disk BEFORE writing
                # anything. A mismatch means the provider restated this
                # symbol's past; upserting now would leave the pre-event bars
                # on the old basis and the new ones on the new basis.
                restated = _detect_restatement(
                    _stored_closes(db, stock.id, batch_start_date), normalised
                )
                if restated is not None:
                    when, was, now = restated
                    ratio = was / now if now else float("inf")
                    logger.warning(
                        "pipeline.incremental.restated symbol=%s date=%s stored=%.2f fetched=%.2f "
                        "ratio=%.4f — full re-pull queued",
                        sym, when, was, now, ratio,
                    )
                    result.restated += 1
                    result.results.append(SymbolResult(
                        sym, IngestionStatus.CORPORATE_ACTION,
                        error=(f"history restated at {when}: stored {was:.2f} vs fetched {now:.2f} "
                               f"(x{ratio:.4f}) — re-pulling full history"),
                    ))
                    needs_repull.append(stock)
                    continue

                bars = upsert_price_history(db, stock.id, normalised)
                latest = normalised["date"].max() if not normalised.empty else None

                # bars > 0 does NOT mean the stock's history actually advanced:
                # ON CONFLICT DO UPDATE re-touches an already-stored date just as
                # "successfully" as it inserts a new one, so a batch whose window
                # only re-returned an old bar (because the target session's bar
                # is not published on Yahoo yet) still reports bars > 0. The only
                # signal that real progress happened is whether the newest date
                # we got back reaches at least as far as what we asked for.
                # Confirmed live: 500/501 stocks hit exactly this the night this
                # was found, all counted as SUCCESS, none of them actually new.
                if latest is None or latest < start_date:
                    result.stale += 1
                    stale_in_batch += 1
                    result.results.append(SymbolResult(
                        sym, IngestionStatus.STALE_DATA, bars_added=bars, latest_date=latest,
                        error="fetch returned no bar at or after the requested start date "
                              "— source has not published this session yet",
                    ))
                    logger.warning(
                        "pipeline.incremental.stale symbol=%s requested_from=%s got_latest=%s",
                        sym, start_date, latest,
                    )
                else:
                    result.succeeded += 1
                    succeeded_in_batch += 1
                    result.results.append(SymbolResult(sym, IngestionStatus.SUCCESS, bars_added=bars, latest_date=latest))

                # Bounded — same reasoning as the full-pull path above.
                price_df = load_price_history_df(db, stock.id, since=_indicator_lookback_start(end_date))
                if not price_df.empty:
                    upsert_indicators(db, stock.id, compute_indicators(price_df, benchmark_df))

                db.commit()
            except Exception as exc:
                db.rollback()
                result.failed += 1
                failed_in_batch += 1
                result.results.append(SymbolResult(sym, IngestionStatus.PROVIDER_ERROR, error=str(exc)))
                logger.error("pipeline.incremental.store_failed symbol=%s error=%s", sym, exc)

        logger.info(
            "pipeline.incremental.batch batch=%d/%d symbols=%d succeeded=%d stale=%d failed=%d",
            batch_num, total_batches, len(batch_symbols), succeeded_in_batch, stale_in_batch, failed_in_batch,
        )

        trim_every(batch_num, every=1)

    # Re-pull restated symbols in full. _process_full_pulls upserts the entire
    # series, so ON CONFLICT DO UPDATE rewrites every stored bar onto the
    # provider's current basis — which is precisely the repair needed.
    if needs_repull:
        logger.warning(
            "pipeline.incremental.repull count=%d symbols=%s",
            len(needs_repull), ", ".join(s.symbol for s in needs_repull),
        )
        _process_full_pulls(db, needs_repull, benchmark_df, result)
