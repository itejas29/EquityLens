from datetime import date as date_type

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
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
    MomentumLeadersResponse,
)
from app.core.v1_strategy import V1_DESCRIPTION, V1_VERSION
from app.models.price_history import PriceHistory
from app.services.daily_signals import (
    compute_market_regime,
    available_dates,
    generate_daily_signals,
    get_daily_signals,
    trigger_state,
    get_momentum_leaders,
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

    # Freshness is computed from the data, never assumed. `market_data_through`
    # is the last stored session; signals are "current" only when they were
    # published for today AND that session is the newest one available.
    market_through = db.query(func.max(PriceHistory.date)).scalar()
    today = date_type.today()
    is_current = bool(as_of == today and market_through is not None)
    pipeline_status = "complete" if (as_of is not None and market_through is not None
                                     and as_of >= market_through) else "incomplete"
    regime = compute_market_regime(db, as_of) if as_of else {}

    common = dict(
        strategy_version=V1_VERSION,
        strategy_description=V1_DESCRIPTION,
        market_data_through=market_through,
        is_current=is_current,
        pipeline_status=pipeline_status,
        regime=regime.get("regime"),
        regime_exposure=regime.get("exposure"),
        nifty_close=regime.get("nifty_close"),
        nifty_200dma=regime.get("nifty_200dma"),
    )

    if not rows:
        return DailySignalsResponse(
            date=as_of, generated_at=None, count=0, universe_size=universe_size, signals=[], **common
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
        **common,
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


@router.get("/momentum-leaders", response_model=MomentumLeadersResponse)
def get_momentum_leaders_endpoint(db: Session = Depends(get_db)) -> MomentumLeadersResponse:
    date, universe_size, leaders = get_momentum_leaders(db)
    return MomentumLeadersResponse(
        date=date,
        universe_size=universe_size,
        leaders=leaders
    )


@router.get("/{symbol}")
def get_daily_signal_for_symbol(
    symbol: str, 
    date: date_type | None = Query(None, description="Defaults to the most recently published shortlist"),
    db: Session = Depends(get_db)
):
    from app.models.daily_signal import SignalOutcome
    from app.models.stock import Stock
    as_of, rows = get_daily_signals(db, date)
    if not rows:
        return None
        
    stocks = {s.id: s for s in db.query(Stock).filter(Stock.id.in_([r.stock_id for r in rows])).all()}
    
    signal = None
    stock_obj = None
    for r in rows:
        st = stocks.get(r.stock_id)
        if st and st.symbol == symbol.upper():
            signal = r
            stock_obj = st
            break
            
    if not signal or not stock_obj:
        return None
        
    # Get outcome if any
    outcome = db.query(SignalOutcome).filter(SignalOutcome.signal_id == signal.id).first()
    
    live = get_live_prices() or {}
    quote = live.get(symbol.upper())
    latest_price = quote["price"] if quote else float(signal.reference_close)
    
    base_res = _to_response(signal, stock_obj, latest_price)
    
    # Return as dict with outcome
    res_dict = base_res.model_dump()
    if outcome:
        res_dict["outcome"] = {
            "evaluated_at": outcome.evaluated_at,
            "current_price": float(outcome.current_price),
            "return_1d": float(outcome.return_1d) if outcome.return_1d else None,
            "return_5d": float(outcome.return_5d) if outcome.return_5d else None,
            "return_10d": float(outcome.return_10d) if outcome.return_10d else None,
            "return_20d": float(outcome.return_20d) if outcome.return_20d else None,
            "max_favorable_excursion": float(outcome.max_favorable_excursion) if outcome.max_favorable_excursion else None,
            "max_adverse_excursion": float(outcome.max_adverse_excursion) if outcome.max_adverse_excursion else None,
            "target_hit": outcome.target_hit,
            "stop_hit": outcome.stop_hit,
            "nifty_return_1d": float(outcome.nifty_return_1d) if outcome.nifty_return_1d else None,
            "nifty_return_5d": float(outcome.nifty_return_5d) if outcome.nifty_return_5d else None,
            "nifty_return_10d": float(outcome.nifty_return_10d) if outcome.nifty_return_10d else None,
            "nifty_return_20d": float(outcome.nifty_return_20d) if outcome.nifty_return_20d else None,
        }
    else:
        res_dict["outcome"] = None
        
    return res_dict
