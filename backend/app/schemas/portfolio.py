from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RiskAppetite = Literal["low", "moderate", "high"]

DISCLAIMER = (
    "This is automated analysis based on historical price and fundamental data. "
    "It is not investment advice. Past performance does not indicate future results."
)


class PortfolioAnalyzeRequest(BaseModel):
    capital: float = Field(gt=0)
    risk_appetite: RiskAppetite
    horizon: str | None = None
    sectors: list[str] | None = None
    max_stocks: int | None = Field(None, ge=1)
    max_allocation_pct: float | None = Field(None, gt=0, le=1)


class PortfolioHoldingResponse(BaseModel):
    stock_id: int
    symbol: str
    sector: str | None
    overall_score: float | None
    signal: str | None
    shares: int
    entry_low: float
    entry_high: float
    stop_loss: float
    target_price: float
    risk_reward: float
    allocated_amount: float
    risk_amount: float
    allocation_capped: bool


class PortfolioAnalyzeResponse(BaseModel):
    holdings: list[PortfolioHoldingResponse]
    excluded: list[dict]
    capital: float
    deployed_capital: float
    cash: float
    cash_pct: float
    weighted_risk_score: float | None
    sector_breakdown: dict[str, float]
    disclaimer: str = DISCLAIMER


class SavePortfolioRequest(PortfolioAnalyzeRequest):
    name: str = Field(min_length=1, max_length=255)


class HoldingWithPnL(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stock_id: int
    symbol: str
    sector: str | None
    quantity: int
    entry_price: float
    allocated_amount: float
    stop_loss: float | None
    target_price: float | None
    status: str
    opened_at: datetime
    current_price: float | None
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None


class SavedPortfolioResponse(BaseModel):
    id: int
    name: str
    capital: float
    risk_appetite: str
    horizon: str | None
    created_at: datetime
    holdings: list[HoldingWithPnL]
    total_market_value: float
    total_unrealized_pnl: float | None
