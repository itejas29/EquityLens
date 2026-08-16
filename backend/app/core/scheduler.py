"""Background schedulers — pure asyncio, no extra dependencies.

All run as background asyncio Tasks inside the FastAPI/uvicorn event loop,
with their blocking work pushed to a thread-pool executor so the network and
DB calls never block the loop.

LOOPS:

`price_refresh_loop` — every 60s during NSE market hours (Mon-Fri
09:10-15:40 IST): fetch latest 1-min bars via yfinance, cache in Redis
(TTL 90s), broadcast to connected WebSocket clients.

`daily_price_update_loop` — once per trading day at 20:00 IST: incremental
price update (only new bars since each stock's last stored date), then
indicator recompute. Records a PipelineRun for completeness tracking.

`daily_signals_loop` — once per trading day at 09:15 IST: publish the frozen
pre-market shortlist (app/services/daily_signals.py). GATED on PipelineRun
status — will not publish signals if the price pipeline has not run.

`weekly_universe_rebuild_loop` — once per week (Saturday by default): full
liquidity screen + membership rebuild. Uses the existing build_universe()
path which includes fundamentals for newly-added stocks.

`fundamentals_refresh_loop` — once per month (1st by default): re-fetches
fundamentals for all active stocks. Isolated from the daily pipeline.
"""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date as date_type, datetime, timedelta
from zoneinfo import ZoneInfo

import yfinance as yf
from sqlalchemy import func

from app.core.cache import get_live_prices, get_session_snapshot, set_live_prices, set_session_snapshot
from app.core.daily_signals_config import GENERATION_HOUR, GENERATION_MINUTE
from app.core.database import SessionLocal
from app.core.experiment_lock import is_locked, lock_info
from app.core.fundamentals_config import (
    FUNDAMENTALS_REFRESH_DAY,
    FUNDAMENTALS_REFRESH_HOUR,
    FUNDAMENTALS_REFRESH_MINUTE,
)
from app.core.universe_config import REBUILD_HOUR, REBUILD_MINUTE
from app.models.daily_signal import DailySignal
from app.models.pipeline_run import PipelineRun
from app.models.price_history import PriceHistory
from app.models.stock import Stock

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN  = (9, 10)   # HH, MM
MARKET_CLOSE = (15, 40)

# Saturday — the full universe rebuild runs here (no trading, no interference).
FULL_REBUILD_DAY = 5

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pipeline")


# --- Helpers ---


def _is_market_hours() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    curr = now.hour * 60 + now.minute
    return (MARKET_OPEN[0] * 60 + MARKET_OPEN[1]) <= curr <= (MARKET_CLOSE[0] * 60 + MARKET_CLOSE[1])


def _get_active_symbols() -> list[str]:
    db = SessionLocal()
    try:
        rows = db.query(Stock.symbol).filter(Stock.is_active == True).all()
        return [r.symbol for r in rows]
    finally:
        db.close()


# --- Live price refresh (unchanged) ---


def _download_prices(symbols: list[str]) -> dict:
    """Blocking yfinance call — run in executor thread."""
    if not symbols:
        return {}

    nse = [f"{s}.NS" for s in symbols]
    result: dict = {}

    try:
        raw = yf.download(
            tickers=nse,
            period="1d",
            interval="1m",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=True,
        )
    except Exception as exc:
        logger.warning("yfinance batch download failed: %s", exc)
        return {}

    for sym in symbols:
        ticker = f"{sym}.NS"
        try:
            df = raw[ticker] if len(nse) > 1 else raw
            if df is None or df.empty:
                continue
            df = df.dropna(subset=["Close"])
            if df.empty:
                continue
            last      = df.iloc[-1]
            first_cls = float(df["Close"].iloc[0])
            price     = float(last["Close"])
            change    = price - first_cls
            pct       = (change / first_cls * 100) if first_cls else 0.0
            vol       = int(last["Volume"]) if last["Volume"] == last["Volume"] else 0
            result[sym] = {
                "price":      round(price, 2),
                "change":     round(change, 2),
                "change_pct": round(pct, 2),
                "volume":     vol,
                "timestamp":  datetime.now(IST).isoformat(),
            }
        except Exception as exc:
            logger.debug("Price parse error for %s: %s", sym, exc)

    return result


async def price_refresh_loop() -> None:
    """Infinite asyncio loop — runs for the lifetime of the application."""
    logger.info("Live price refresh loop started")
    while True:
        await asyncio.sleep(60)
        if not _is_market_hours():
            continue

        symbols = await asyncio.get_event_loop().run_in_executor(
            _executor, _get_active_symbols
        )
        if not symbols:
            continue

        prices = await asyncio.get_event_loop().run_in_executor(
            _executor, _download_prices, symbols
        )
        if not prices:
            continue

        # Merge with the previous tick so symbols that returned nothing this
        # round keep their last known price instead of dropping off the tape.
        # Falls back to the frozen snapshot, which is what makes the first tick
        # after the open continue from Friday's close rather than starting bare.
        previous = get_live_prices()
        if previous is None:
            snapshot = get_session_snapshot()
            previous = (snapshot or {}).get("prices", {})
        existing = {**previous, **prices}

        set_live_prices(existing)
        # Written every tick, so whatever the last tick before the close saw
        # becomes the frozen snapshot automatically — no separate "market is
        # closing now" trigger to get wrong around holidays or early closes.
        now_ist = datetime.now(IST)
        set_session_snapshot(existing, now_ist.isoformat(), now_ist.date().isoformat())

        # Broadcast to WebSocket clients
        from app.core.ws_manager import ws_manager  # avoid circular at module load
        if ws_manager.connection_count > 0:
            # Broadcasts only ever happen inside market hours, so this is
            # always genuinely live — the frozen case is served on connect.
            payload = json.dumps({"type": "prices", "status": "live", "data": existing})
            await ws_manager.broadcast(payload)

        logger.info(
            "Prices refreshed — %d symbols, %d WS clients",
            len(existing), ws_manager.connection_count,
        )


# --- Daily pre-market shortlist (signal generation) ---


def _has_signals_for(as_of: date_type) -> bool:
    """Checked against the DB rather than tracked in memory, so a restart part
    way through the day does not republish a shortlist that already exists.
    """
    db = SessionLocal()
    try:
        return db.query(DailySignal).filter(DailySignal.date == as_of).first() is not None
    finally:
        db.close()


def _get_latest_pipeline_run(as_of: date_type) -> PipelineRun | None:
    """Find the most recent pipeline run for the given date."""
    db = SessionLocal()
    try:
        return (
            db.query(PipelineRun)
            .filter(PipelineRun.run_date == as_of, PipelineRun.run_type.in_(["incremental", "full_rebuild"]))
            .order_by(PipelineRun.started_at.desc())
            .first()
        )
    finally:
        db.close()


def _generate_signals(as_of: date_type) -> int:
    # Imported here rather than at module load: daily_signals pulls in the
    # scoring stack, which imports models that import this module's siblings.
    from app.services.daily_signals import generate_daily_signals

    db = SessionLocal()
    try:
        rows = generate_daily_signals(db, as_of)
        db.commit()

        # Update the pipeline run to record that signals were generated.
        pipeline_run = (
            db.query(PipelineRun)
            .filter(PipelineRun.run_date == as_of, PipelineRun.run_type.in_(["incremental", "full_rebuild"]))
            .order_by(PipelineRun.started_at.desc())
            .first()
        )
        if pipeline_run:
            pipeline_run.signals_generated = True
            pipeline_run.signal_count = len(rows)
            db.commit()

        return len(rows)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def daily_signals_loop() -> None:
    """Publishes the pre-market shortlist once per trading day at 09:15 IST.

    Catch-up by design: the condition is "it is past 09:15 on a weekday and
    today has no shortlist yet", not "it is exactly 09:15". A process started
    at 11:00 still publishes the day's list rather than skipping it, and a
    process running across midnight picks the next day up on its own.

    GATED on PipelineRun: signals will not be published if the price pipeline
    has not run completely for the previous trading day. If data is missing,
    it attempts a bounded incremental recovery.
    """
    logger.info("Daily signal loop started — publishes at %02d:%02d IST", GENERATION_HOUR, GENERATION_MINUTE)
    recovery_attempted_for_date = None
    last_skip_logged_date = None

    while True:
        await asyncio.sleep(60)
        try:
            now = datetime.now(IST)
            if now.weekday() >= 5:  # NSE is shut; the page falls back to Friday's list
                continue
            if (now.hour, now.minute) < (GENERATION_HOUR, GENERATION_MINUTE):
                continue

            today = now.date()
            already = await asyncio.get_event_loop().run_in_executor(_executor, _has_signals_for, today)
            if already:
                continue

            yesterday = today - timedelta(days=1)
            while yesterday.weekday() >= 5:
                yesterday -= timedelta(days=1)

            # Gate: check that we have a reasonably fresh and complete pipeline run.
            pipeline_run = await asyncio.get_event_loop().run_in_executor(
                _executor, _get_latest_pipeline_run, yesterday
            )
            # Also check if there's a complete run for 'today' (edge case manual trigger)
            if pipeline_run is None or pipeline_run.status != "complete":
                today_run = await asyncio.get_event_loop().run_in_executor(
                    _executor, _get_latest_pipeline_run, today
                )
                if today_run and today_run.status == "complete":
                    pipeline_run = today_run

            # Bounded recovery attempt
            if pipeline_run is None or pipeline_run.status != "complete":
                if recovery_attempted_for_date != today:
                    logger.warning("pipeline.signals.data_missing date=%s — attempting bounded recovery", yesterday)
                    await asyncio.get_event_loop().run_in_executor(_executor, _run_incremental_update, yesterday)
                    recovery_attempted_for_date = today
                    
                    pipeline_run = await asyncio.get_event_loop().run_in_executor(
                        _executor, _get_latest_pipeline_run, yesterday
                    )

            if pipeline_run is None or pipeline_run.status != "complete":
                if last_skip_logged_date != today:
                    logger.error(
                        "pipeline.signals.skipped date=%s reason=data_incomplete run_date=%s status=%s",
                        today, 
                        pipeline_run.run_date if pipeline_run else None,
                        pipeline_run.status if pipeline_run else None
                    )
                    last_skip_logged_date = today
                continue

            count = await asyncio.get_event_loop().run_in_executor(_executor, _generate_signals, today)
            logger.info("pipeline.signals.published date=%s count=%d", today, count)
        except Exception as exc:
            # Never let one bad day kill the loop; it retries on the next tick.
            logger.exception("Daily signal generation failed: %s", exc)


# --- Nightly incremental price update ---


def _incremental_done_today(as_of: date_type) -> bool:
    """True if an incremental or full_rebuild PipelineRun for today exists
    with status != 'running'."""
    db = SessionLocal()
    try:
        run = (
            db.query(PipelineRun)
            .filter(
                PipelineRun.run_date == as_of,
                PipelineRun.run_type.in_(["incremental", "full_rebuild"]),
                PipelineRun.status != "running",
            )
            .first()
        )
        return run is not None
    finally:
        db.close()


def _run_incremental_update(as_of: date_type) -> int:
    """Run the incremental price update and record a PipelineRun."""
    from app.services.incremental import incremental_price_update

    db = SessionLocal()
    try:
        # Create a PipelineRun record at start
        pipeline_run = PipelineRun(
            run_date=as_of,
            run_type="incremental",
            started_at=datetime.now(IST),
            status="running",
            stocks_requested=0,
        )
        db.add(pipeline_run)
        db.commit()

        result = incremental_price_update(db, today=as_of)

        try:
            from app.services.forward_testing import evaluate_signal_outcomes, record_paper_snapshot
            from app.models.paper_trading import PaperAccount
            
            evaluate_signal_outcomes(db, as_of)
            accounts = db.query(PaperAccount).all()
            for account in accounts:
                try:
                    record_paper_snapshot(db, account.id, as_of)
                except Exception as e:
                    logger.error("Failed to record snapshot for account %s: %s", account.id, e)
            db.commit()
        except Exception as e:
            logger.error("Failed to evaluate forward testing outcomes: %s", e)
            db.rollback()

        # Update the PipelineRun with results
        pipeline_run.finished_at = datetime.now(IST)
        pipeline_run.status = result.status
        pipeline_run.stocks_requested = result.requested
        pipeline_run.stocks_succeeded = result.succeeded
        pipeline_run.stocks_failed = result.failed
        pipeline_run.duration_seconds = result.seconds
        pipeline_run.failed_symbols = [
            r.symbol for r in result.results if r.status not in ("SUCCESS", "ALREADY_CURRENT")
        ]
        pipeline_run.failure_details = [
            {"symbol": r.symbol, "status": r.status.value, "error": r.error}
            for r in result.results
            if r.status not in ("SUCCESS", "ALREADY_CURRENT")
        ]
        db.commit()

        return result.succeeded
    except Exception:
        db.rollback()
        # Try to mark the pipeline run as failed
        try:
            if pipeline_run.id:
                pipeline_run.status = "failed"
                pipeline_run.finished_at = datetime.now(IST)
                db.commit()
        except Exception:
            db.rollback()
        raise
    finally:
        db.close()


async def daily_price_update_loop() -> None:
    """Incremental price update — once per trading day at 20:00 IST.

    Replaces the old full-rebuild nightly job for prices. Only fetches bars
    since each stock's last stored date, then recomputes indicators.
    """
    logger.info(
        "Daily incremental price update loop started — runs at %02d:%02d IST",
        REBUILD_HOUR, REBUILD_MINUTE,
    )
    while True:
        await asyncio.sleep(300)  # coarse tick; this job runs once a day
        try:
            now = datetime.now(IST)
            if now.weekday() >= 5:
                continue
            if (now.hour, now.minute) < (REBUILD_HOUR, REBUILD_MINUTE):
                continue

            today = now.date()
            if await asyncio.get_event_loop().run_in_executor(_executor, _incremental_done_today, today):
                continue

            logger.info("pipeline.incremental.starting date=%s", today)
            count = await asyncio.get_event_loop().run_in_executor(_executor, _run_incremental_update, today)
            logger.info("pipeline.incremental.complete date=%s stocks=%d", today, count)
        except Exception as exc:
            logger.exception("Incremental price update failed: %s", exc)


# --- Weekly universe rebuild ---


def _full_rebuild_done_this_week(as_of: date_type) -> bool:
    """Check if a full_rebuild PipelineRun exists for this week."""
    # Look back up to 7 days for a full_rebuild run
    week_start = as_of - timedelta(days=7)
    db = SessionLocal()
    try:
        run = (
            db.query(PipelineRun)
            .filter(
                PipelineRun.run_date >= week_start,
                PipelineRun.run_type == "full_rebuild",
                PipelineRun.status.in_(["complete", "incomplete"]),
            )
            .first()
        )
        return run is not None
    finally:
        db.close()


def _run_full_rebuild(as_of: date_type) -> int:
    # Research runs hold an advisory lock. Rebuilding the universe under a
    # long walk-forward changes membership mid-experiment, which corrupted a
    # previous Phase 15 run; skip rather than silently mutate.
    if is_locked():
        logger.warning("Universe rebuild SKIPPED — experiment lock held: %s", lock_info())
        return 0

    """Run the full universe rebuild and record a PipelineRun."""
    from app.services.universe import build_universe

    db = SessionLocal()
    try:
        pipeline_run = PipelineRun(
            run_date=as_of,
            run_type="full_rebuild",
            started_at=datetime.now(IST),
            status="running",
            stocks_requested=0,
        )
        db.add(pipeline_run)
        db.commit()

        result = build_universe(db)

        pipeline_run.finished_at = datetime.now(IST)
        pipeline_run.stocks_requested = result.screen.passed
        pipeline_run.stocks_succeeded = result.ingested
        pipeline_run.stocks_failed = len(result.failed)
        pipeline_run.duration_seconds = result.seconds
        pipeline_run.failed_symbols = [sym for sym, _ in result.failed]
        pipeline_run.failure_details = [
            {"symbol": sym, "status": "PROVIDER_ERROR", "error": reason}
            for sym, reason in result.failed
        ]
        pipeline_run.status = "complete" if not result.failed else "incomplete"
        db.commit()

        return result.ingested
    except Exception:
        db.rollback()
        try:
            if pipeline_run.id:
                pipeline_run.status = "failed"
                pipeline_run.finished_at = datetime.now(IST)
                db.commit()
        except Exception:
            db.rollback()
        raise
    finally:
        db.close()


async def weekly_universe_rebuild_loop() -> None:
    """Full universe rebuild — once per week on Saturday.

    This is the only time new stocks enter or old stocks leave the universe.
    Includes the liquidity screen and fundamentals for newly-added stocks.
    """
    logger.info("Weekly universe rebuild loop started — runs on day %d at %02d:%02d IST", FULL_REBUILD_DAY, REBUILD_HOUR, REBUILD_MINUTE)
    while True:
        await asyncio.sleep(300)
        try:
            now = datetime.now(IST)
            if now.weekday() != FULL_REBUILD_DAY:
                continue
            if (now.hour, now.minute) < (REBUILD_HOUR, REBUILD_MINUTE):
                continue

            today = now.date()
            if await asyncio.get_event_loop().run_in_executor(_executor, _full_rebuild_done_this_week, today):
                continue

            logger.info("pipeline.full_rebuild.starting date=%s", today)
            count = await asyncio.get_event_loop().run_in_executor(_executor, _run_full_rebuild, today)
            logger.info("pipeline.full_rebuild.complete date=%s stocks=%d", today, count)
        except Exception as exc:
            logger.exception("Weekly universe rebuild failed: %s", exc)


# --- Monthly fundamentals refresh ---


def _fundamentals_done_this_month(as_of: date_type) -> bool:
    """Check if a fundamentals PipelineRun exists for this month."""
    month_start = as_of.replace(day=1)
    db = SessionLocal()
    try:
        run = (
            db.query(PipelineRun)
            .filter(
                PipelineRun.run_date >= month_start,
                PipelineRun.run_type == "fundamentals",
                PipelineRun.status.in_(["complete", "incomplete"]),
            )
            .first()
        )
        return run is not None
    finally:
        db.close()


def _run_fundamentals_refresh(as_of: date_type) -> int:
    """Run the fundamentals refresh and record a PipelineRun."""
    from app.services.fundamentals_refresh import refresh_fundamentals

    db = SessionLocal()
    try:
        pipeline_run = PipelineRun(
            run_date=as_of,
            run_type="fundamentals",
            started_at=datetime.now(IST),
            status="running",
            stocks_requested=0,
        )
        db.add(pipeline_run)
        db.commit()

        result = refresh_fundamentals(db)

        pipeline_run.finished_at = datetime.now(IST)
        pipeline_run.stocks_requested = result.requested
        pipeline_run.stocks_succeeded = result.succeeded
        pipeline_run.stocks_failed = result.failed
        pipeline_run.duration_seconds = result.seconds
        pipeline_run.failed_symbols = [r.symbol for r in result.results if r.status != "SUCCESS"]
        pipeline_run.failure_details = [
            {"symbol": r.symbol, "status": r.status.value, "error": r.error}
            for r in result.results
            if r.status != "SUCCESS"
        ]
        pipeline_run.status = "complete" if result.failed == 0 else "incomplete"
        db.commit()

        return result.succeeded
    except Exception:
        db.rollback()
        try:
            if pipeline_run.id:
                pipeline_run.status = "failed"
                pipeline_run.finished_at = datetime.now(IST)
                db.commit()
        except Exception:
            db.rollback()
        raise
    finally:
        db.close()


async def fundamentals_refresh_loop() -> None:
    """Monthly fundamentals refresh — runs on a configurable day of the month."""
    logger.info(
        "Fundamentals refresh loop started — runs on day %d at %02d:%02d IST",
        FUNDAMENTALS_REFRESH_DAY, FUNDAMENTALS_REFRESH_HOUR, FUNDAMENTALS_REFRESH_MINUTE,
    )
    while True:
        await asyncio.sleep(300)
        try:
            now = datetime.now(IST)
            if now.day != FUNDAMENTALS_REFRESH_DAY:
                continue
            if (now.hour, now.minute) < (FUNDAMENTALS_REFRESH_HOUR, FUNDAMENTALS_REFRESH_MINUTE):
                continue

            today = now.date()
            if await asyncio.get_event_loop().run_in_executor(_executor, _fundamentals_done_this_month, today):
                continue

            logger.info("pipeline.fundamentals.starting date=%s", today)
            count = await asyncio.get_event_loop().run_in_executor(_executor, _run_fundamentals_refresh, today)
            logger.info("pipeline.fundamentals.complete date=%s stocks=%d", today, count)
        except Exception as exc:
            logger.exception("Fundamentals refresh failed: %s", exc)
