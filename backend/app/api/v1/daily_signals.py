from datetime import date as date_type

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.cache import get_live_prices
from app.core.database import get_db
from app.core.rate_limit import rate_limit_analysis
from app.models.daily_signal import DailySignal
from app.models.stock import Stock
from app.schemas.daily_signal import (
    DailySignalResponse,
    DailySignalsResponse,
    GenerateDailySignalsResponse,
)
from app.services.daily_signals import (
    available_dates,
    generate_daily_signals,
    get_daily_signals,
    trigger_state,
)

router = APIRouter(prefix="/daily-signals", tags=["daily-signals"])


def _to_response(row: DailySignal, stock: Stock, latest_price: float | None) -> DailySignalResponse:
    entry_high = float(row.entry_high)
    target = float(row.target_price)
    stop = float(row.stop_loss)

    return DailySignalResponse(
        rank=row.rank,
        stock_id=row.stock_id,
        symbol=stock.symbol,
        company_name=stock.company_name,
        sector=stock.sector,
        overall_score=float(row.overall_score),
        technical_score=float(row.technical_score) if row.technical_score is not None else None,
        fundamental_score=float(row.fundamental_score) if row.fundamental_score is not None else None,
        valuation_score=float(row.valuation_score) if row.valuation_score is not None else None,
        risk_score=float(row.risk_score) if row.risk_score is not None else None,
        signal=row.signal,
        reference_close=float(row.reference_close),
        reference_date=row.reference_date,
        entry_low=float(row.entry_low),
        entry_high=entry_high,
        stop_loss=stop,
        target_price=target,
        risk_reward=float(row.risk_reward),
        stop_method=row.stop_method,
        latest_price=latest_price,
        trigger_state=trigger_state(latest_price, float(row.entry_low), entry_high),
        # Both measured from the top of the entry zone, so they are directly
        # comparable to each other and to the stated risk:reward.
        upside_pct=round((target - entry_high) / entry_high * 100, 2),
        downside_pct=round((stop - entry_high) / entry_high * 100, 2),
        rationale=row.rationale,
    )


@router.get("", response_model=DailySignalsResponse)
def list_daily_signals(
    date: date_type | None = Query(None, description="Defaults to the most recently published shortlist"),
    db: Session = Depends(get_db),
) -> DailySignalsResponse:
    as_of, rows = get_daily_signals(db, date)
    universe_size = db.query(Stock).filter(Stock.is_active == True).count()  # noqa: E712

    if not rows:
        return DailySignalsResponse(
            date=as_of, generated_at=None, count=0, universe_size=universe_size, signals=[]
        )

    stocks = {s.id: s for s in db.query(Stock).filter(Stock.id.in_([r.stock_id for r in rows])).all()}
    # Intraday prices when the refresher has them; otherwise the reference
    # close, which is what the zone was drawn against anyway.
    live = get_live_prices() or {}

    signals = []
    for row in rows:
        stock = stocks.get(row.stock_id)
        if stock is None:
            continue
        quote = live.get(stock.symbol)
        latest_price = quote["price"] if quote else float(row.reference_close)
        signals.append(_to_response(row, stock, latest_price))

    return DailySignalsResponse(
        date=as_of,
        generated_at=rows[0].generated_at,
        count=len(signals),
        universe_size=universe_size,
        signals=signals,
    )


@router.get("/dates", response_model=list[date_type])
def list_dates(db: Session = Depends(get_db)) -> list[date_type]:
    return available_dates(db)


@router.post("/run", response_model=GenerateDailySignalsResponse, dependencies=[Depends(rate_limit_analysis)])
def run_generation(
    date: date_type | None = Query(None, description="Defaults to today"),
    db: Session = Depends(get_db),
) -> GenerateDailySignalsResponse:
    """Manual trigger for the same job the 09:15 IST scheduler runs. Useful
    after a data refresh, and the only way to publish outside market days.
    """
    rows = generate_daily_signals(db, date)
    stocks = {s.id: s for s in db.query(Stock).filter(Stock.id.in_([r.stock_id for r in rows])).all()}
    published_date = rows[0].date if rows else (date or date_type.today())
    symbols = [stocks[r.stock_id].symbol for r in rows if r.stock_id in stocks]
    db.commit()

    return GenerateDailySignalsResponse(date=published_date, published=len(rows), symbols=symbols)
