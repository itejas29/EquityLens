from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import AppError
from app.ml.predict import predict_probability
from app.models.stock import Stock
from app.services.outlook import get_outlook

router = APIRouter(prefix="/analysis", tags=["analysis"])


class ExpectedMoveResponse(BaseModel):
    horizon_days: int
    annualised_vol_pct: float
    sigma_1_pct: float
    sigma_1_low: float
    sigma_1_high: float
    sigma_2_low: float
    sigma_2_high: float
    atr: float | None
    atr_pct: float | None
    atr_days_to_target: float | None
    atr_days_to_stop: float | None


class HistoricalOutcomeResponse(BaseModel):
    samples: int
    hit_target_first: int
    hit_stop_first: int
    resolved_neither: int
    hit_rate_pct: float | None
    avg_days_to_target: float | None
    # False when the sample is too small to lean on. The client must show this,
    # not quietly present a 4-sample rate as though it were 400.
    reliable: bool
    horizon_days: int
    expectancy_pct: float | None
    breakeven_hit_rate_pct: float | None


class OutlookResponse(BaseModel):
    symbol: str
    reference_close: float
    expected_move: ExpectedMoveResponse | None
    historical: HistoricalOutcomeResponse | None
    # Secondary ML signal, never folded into the rule-based score.
    ml_probability: float | None
    note: str


@router.get("/{symbol}/outlook", response_model=OutlookResponse)
def stock_outlook(
    symbol: str,
    horizon_days: int = Query(30, ge=5, le=180),
    target: float | None = Query(None, gt=0),
    stop: float | None = Query(None, gt=0),
    db: Session = Depends(get_db),
) -> OutlookResponse:
    """Dispersion and historical-frequency analysis for one symbol.

    `target`/`stop` default to the stock's current ATR levels; pass them
    explicitly to evaluate a different setup.
    """
    result = get_outlook(db, symbol, horizon_days=horizon_days, target=target, stop=stop)
    if result is None:
        raise AppError(
            f"No price history stored for '{symbol.upper()}'.", status_code=status.HTTP_404_NOT_FOUND
        )

    stock = db.query(Stock).filter(Stock.symbol == symbol.upper()).first()

    return OutlookResponse(
        symbol=result.symbol,
        reference_close=result.reference_close,
        expected_move=ExpectedMoveResponse(**result.expected_move.__dict__) if result.expected_move else None,
        historical=HistoricalOutcomeResponse(**result.historical.__dict__) if result.historical else None,
        ml_probability=predict_probability(db, stock.id) if stock else None,
        note=result.note,
    )
