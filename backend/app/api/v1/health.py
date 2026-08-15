from datetime import date, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.redis_client import redis_client
from app.models.daily_signal import DailySignal
from app.models.pipeline_run import PipelineRun
from app.models.price_history import PriceHistory
from app.models.stock import Stock

router = APIRouter(tags=["health"])


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


@router.get("/health/pipeline")
def pipeline_health(db: Session = Depends(get_db)) -> dict:
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

    # 2. Data coverage for active stocks
    active_stocks = db.query(Stock).filter(Stock.is_active == True).all()
    active_count = len(active_stocks)
    
    coverage = {"stocks_active": active_count, "stocks_with_today_data": 0, "stocks_missing_today": []}
    if active_count > 0:
        latest_date = db.query(func.max(PriceHistory.date)).scalar()
        coverage["latest_price_date"] = latest_date.isoformat() if latest_date else None
        
        if latest_date:
            # Find which active stocks have data for the latest date
            have_data = (
                db.query(PriceHistory.stock_id)
                .filter(PriceHistory.date == latest_date)
                .all()
            )
            have_data_ids = {r[0] for r in have_data}
            
            coverage["stocks_with_today_data"] = len([s for s in active_stocks if s.id in have_data_ids])
            coverage["stocks_missing_today"] = [s.symbol for s in active_stocks if s.id not in have_data_ids]
            coverage["coverage_pct"] = round((coverage["stocks_with_today_data"] / active_count) * 100, 1)

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

    from datetime import datetime
    return {
        "status": status,
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
