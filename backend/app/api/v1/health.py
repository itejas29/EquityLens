from datetime import date, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.redis_client import redis_client
from app.core.security import get_current_user
from app.models.daily_signal import DailySignal
from app.models.pipeline_run import PipelineRun
from app.models.price_history import PriceHistory
from app.models.stock import Stock

router = APIRouter(tags=["health"])

# The most symbols /health/pipeline will name in one response. Unbounded, this
# list is the entire active universe — ~500 tickers — on any day the pipeline
# is behind, which is both a large response and a free readout of exactly which
# stocks this system tracks. The count above it is the operational signal; the
# names are a debugging convenience and a sample is enough of one.
MAX_MISSING_SYMBOLS_REPORTED = 25


@router.get("/ping")
def ping() -> dict:
    """Fast health check for Render that doesn't hit the DB (avoids Neon cold-start timeouts)."""
    return {"status": "ok"}


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    try:
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "unreachable"

    try:
        redis_client.ping()
        redis_status = "ok"
    except Exception:
        redis_status = "unreachable"

    return {
        "status": "ok" if db_status == "ok" and redis_status == "ok" else "degraded",
        "database": db_status,
        "redis": redis_status,
    }


@router.get("/health/pipeline", dependencies=[Depends(get_current_user)])
def pipeline_health(db: Session = Depends(get_db)) -> dict:
    """Operational detail: pipeline runs, data coverage, signal freshness,
    scheduler heartbeats.

    Authenticated, unlike /ping and /health. This response names the stocks in
    the universe and describes the internals of every scheduler loop — useful
    to an operator, and a free readout of the system's composition to anyone
    else. The two unauthenticated endpoints above remain the ones a container
    or uptime probe should use; docker-compose's healthcheck points at /health
    for that reason. It was pointed here, which was both the heaviest query in
    the app on a 30-second timer and the reason this could not require auth.
    """
    try:
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "unreachable"

    try:
        redis_client.ping()
        redis_status = "ok"
    except Exception:
        redis_status = "unreachable"

    today = date.today()
    
    # 1. Pipeline runs
    last_run = (
        db.query(PipelineRun)
        .filter(PipelineRun.run_type.in_(["incremental", "full_rebuild"]))
        .order_by(PipelineRun.started_at.desc())
        .first()
    )
    
    last_fundamentals = (
        db.query(PipelineRun)
        .filter(PipelineRun.run_type == "fundamentals")
        .order_by(PipelineRun.started_at.desc())
        .first()
    )

    # 2. Data coverage for active stocks.
    #
    # Counted in the database rather than by loading every Stock row and every
    # price_history row for the session and diffing them in Python. The old
    # version pulled ~1,000 rows into memory on every call — on a 30-second
    # container healthcheck, permanently, on a 2GB box with a history of
    # memory incidents.
    active_count = db.query(func.count(Stock.id)).filter(Stock.is_active == True).scalar() or 0  # noqa: E712

    coverage = {"stocks_active": active_count, "stocks_with_today_data": 0, "stocks_missing_today": []}
    if active_count > 0:
        latest_date = db.query(func.max(PriceHistory.date)).scalar()
        coverage["latest_price_date"] = latest_date.isoformat() if latest_date else None

        if latest_date:
            have_data_subq = (
                db.query(PriceHistory.stock_id)
                .filter(PriceHistory.date == latest_date, PriceHistory.stock_id == Stock.id)
                .exists()
            )
            with_data = (
                db.query(func.count(Stock.id))
                .filter(Stock.is_active == True, have_data_subq)  # noqa: E712
                .scalar()
            ) or 0
            missing_symbols = [
                row[0]
                for row in db.query(Stock.symbol)
                .filter(Stock.is_active == True, ~have_data_subq)  # noqa: E712
                .order_by(Stock.symbol)
                .limit(MAX_MISSING_SYMBOLS_REPORTED)
                .all()
            ]

            coverage["stocks_with_today_data"] = with_data
            coverage["stocks_missing_today"] = missing_symbols
            coverage["stocks_missing_today_count"] = active_count - with_data
            # Says so explicitly rather than letting a truncated list read as
            # the whole story.
            coverage["stocks_missing_today_truncated"] = (
                active_count - with_data
            ) > len(missing_symbols)
            coverage["coverage_pct"] = round((with_data / active_count) * 100, 1)

    # 3. Signals
    latest_signal_date = db.query(func.max(DailySignal.date)).scalar()
    signals = {
        "latest_date": latest_signal_date.isoformat() if latest_signal_date else None,
        "count": 0,
        "generated_from_complete_data": False,
    }
    
    if latest_signal_date:
        signals["count"] = db.query(DailySignal).filter(DailySignal.date == latest_signal_date).count()
        # Check if the run that generated these signals was complete
        signal_run = (
            db.query(PipelineRun)
            .filter(PipelineRun.run_date == latest_signal_date)
            .order_by(PipelineRun.started_at.desc())
            .first()
        )
        if signal_run:
            signals["generated_from_complete_data"] = signal_run.status == "complete"

    # Status derivation
    status = "healthy"
    if db_status != "ok" or redis_status != "ok":
        status = "unhealthy"
    elif coverage.get("coverage_pct", 0) < 95.0:
        status = "degraded"
    elif latest_signal_date and (today - latest_signal_date) > timedelta(days=3):
        # Allow weekend gap, but >3 days means we missed signals
        status = "degraded"
    elif last_run and last_run.status == "failed":
        status = "degraded"

    # A stopped scheduler loop used to be invisible here: the app keeps serving
    # HTTP and every data field below stays plausible for days while nothing is
    # actually running. On 2026-09-04 that hid a ~30-hour outage. A stale
    # heartbeat is a degraded system even when the stored data still looks fine.
    from app.core.scheduler import heartbeat_report
    scheduler = heartbeat_report()
    if scheduler["stale"]:
        status = "degraded"

    from datetime import datetime
    return {
        "status": status,
        "scheduler": scheduler,
        "database": db_status,
        "redis": redis_status,
        "data": coverage,
        "signals": signals,
        "pipeline": {
            "last_run_date": last_run.run_date.isoformat() if last_run else None,
            "last_run_type": last_run.run_type if last_run else None,
            "last_run_status": last_run.status if last_run else None,
            "last_run_duration_seconds": last_run.duration_seconds if last_run else None,
            "last_fundamentals_refresh": last_fundamentals.run_date.isoformat() if last_fundamentals else None,
        },
        "timestamp": datetime.now().isoformat() + "Z",
    }
