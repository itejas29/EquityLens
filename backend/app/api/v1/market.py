from datetime import date as date_type

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.market import get_market_overview, get_price_feed, get_sparkline
from app.services.market_ext import get_discover_sections, get_sectors

router = APIRouter(prefix="/market", tags=["market"])


class MoverResponse(BaseModel):
    symbol: str
    company_name: str | None
    sector: str | None
    close: float
    prev_close: float
    change: float
    change_pct: float
    volume: int | None
    traded_value: float | None


class MarketOverviewResponse(BaseModel):
    as_of: date_type | None
    # "live" while the refresher is ticking, "closed" when showing the frozen
    # last-session snapshot, "stale" when there is nothing to stand behind.
    # The client must never render "closed" prices as though they are moving.
    source: str
    # When source == "closed": when those prices were last captured, so the UI
    # can say "closed 15:39" rather than implying they are current.
    captured_at: str | None = None
    session_date: str | None = None
    universe_size: int
    advancing: int
    declining: int
    unchanged: int
    gainers: list[MoverResponse]
    losers: list[MoverResponse]
    most_traded: list[MoverResponse]


class SparklineResponse(BaseModel):
    symbol: str
    closes: list[float]


@router.get("/overview", response_model=MarketOverviewResponse)
def market_overview(
    limit: int = Query(6, ge=1, le=20),
    db: Session = Depends(get_db),
) -> MarketOverviewResponse:
    overview = get_market_overview(db, limit=limit)
    feed = get_price_feed()

    return MarketOverviewResponse(
        as_of=overview.as_of,
        source=feed.status,
        captured_at=feed.captured_at,
        session_date=feed.session_date,
        universe_size=overview.universe_size,
        advancing=overview.advancing,
        declining=overview.declining,
        unchanged=overview.unchanged,
        gainers=[MoverResponse(**m.__dict__) for m in overview.gainers],
        losers=[MoverResponse(**m.__dict__) for m in overview.losers],
        most_traded=[MoverResponse(**m.__dict__) for m in overview.most_traded],
    )


@router.get("/sparkline/{symbol}", response_model=SparklineResponse)
def sparkline(symbol: str, days: int = Query(30, ge=5, le=120), db: Session = Depends(get_db)) -> SparklineResponse:
    return SparklineResponse(symbol=symbol.upper(), closes=get_sparkline(db, symbol, days))

@router.get("/sectors")
def market_sectors(db: Session = Depends(get_db)):
    return get_sectors(db)

@router.get("/discover")
def market_discover(limit: int = Query(20, ge=1, le=50), db: Session = Depends(get_db)):
    return get_discover_sections(db, limit)
