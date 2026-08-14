from datetime import datetime

from pydantic import BaseModel


class WatchlistAddRequest(BaseModel):
    symbol: str


class WatchlistItemResponse(BaseModel):
    stock_id: int
    symbol: str
    sector: str | None
    added_at: datetime
    latest_price: float | None
    overall_score: float | None
    signal: str | None
