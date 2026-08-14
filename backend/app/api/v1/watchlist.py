from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import AppError
from app.core.security import get_current_user
from app.models.price_history import PriceHistory
from app.models.recommendation import Recommendation
from app.models.stock import Stock
from app.models.user import User
from app.models.watchlist import Watchlist
from app.schemas.watchlist import WatchlistAddRequest, WatchlistItemResponse

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


def _to_response(db: Session, entry: Watchlist, stock: Stock) -> WatchlistItemResponse:
    latest_price = (
        db.query(PriceHistory).filter(PriceHistory.stock_id == stock.id).order_by(PriceHistory.date.desc()).first()
    )
    latest_rec = (
        db.query(Recommendation)
        .filter(Recommendation.stock_id == stock.id)
        .order_by(Recommendation.generated_at.desc())
        .first()
    )
    return WatchlistItemResponse(
        stock_id=stock.id,
        symbol=stock.symbol,
        sector=stock.sector,
        added_at=entry.added_at,
        latest_price=float(latest_price.close) if latest_price and latest_price.close is not None else None,
        overall_score=float(latest_rec.overall_score) if latest_rec and latest_rec.overall_score is not None else None,
        signal=latest_rec.signal if latest_rec else None,
    )


@router.get("", response_model=list[WatchlistItemResponse])
def list_watchlist(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[WatchlistItemResponse]:
    entries = (
        db.query(Watchlist, Stock)
        .join(Stock, Stock.id == Watchlist.stock_id)
        .filter(Watchlist.user_id == current_user.id)
        .order_by(Watchlist.added_at.desc())
        .all()
    )
    return [_to_response(db, entry, stock) for entry, stock in entries]


@router.post("", response_model=WatchlistItemResponse, status_code=status.HTTP_201_CREATED)
def add_to_watchlist(
    payload: WatchlistAddRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> WatchlistItemResponse:
    stock = db.query(Stock).filter(Stock.symbol == payload.symbol.upper()).first()
    if stock is None:
        raise AppError(f"Stock '{payload.symbol}' not found", status_code=status.HTTP_404_NOT_FOUND)

    existing = db.query(Watchlist).filter(Watchlist.user_id == current_user.id, Watchlist.stock_id == stock.id).first()
    if existing is not None:
        raise AppError(f"'{stock.symbol}' is already on your watchlist", status_code=status.HTTP_409_CONFLICT)

    entry = Watchlist(user_id=current_user.id, stock_id=stock.id)
    db.add(entry)
    db.commit()
    db.refresh(entry)

    return _to_response(db, entry, stock)


@router.delete("/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
def remove_from_watchlist(
    symbol: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> None:
    stock = db.query(Stock).filter(Stock.symbol == symbol.upper()).first()
    if stock is None:
        raise AppError(f"Stock '{symbol}' not found", status_code=status.HTTP_404_NOT_FOUND)

    entry = db.query(Watchlist).filter(Watchlist.user_id == current_user.id, Watchlist.stock_id == stock.id).first()
    if entry is None:
        raise AppError(f"'{symbol}' is not on your watchlist", status_code=status.HTTP_404_NOT_FOUND)

    db.delete(entry)
    db.commit()
