from datetime import datetime

from pydantic import BaseModel, Field


class PaperBuyRequest(BaseModel):
    symbol: str
    quantity: int = Field(gt=0)


class PaperSellRequest(BaseModel):
    symbol: str


class PaperTradeResponse(BaseModel):
    id: int
    symbol: str
    side: str
    quantity: int
    price: float
    executed_at: datetime
    status: str
    exit_price: float | None
    exit_at: datetime | None
    pnl: float | None


class PaperHoldingResponse(BaseModel):
    trade_id: int
    symbol: str
    quantity: int
    entry_price: float
    current_price: float | None
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None


class PaperAccountResponse(BaseModel):
    virtual_capital: float
    cash: float
    equity: float
    market_value: float
    realized_pnl: float
    unrealized_pnl: float
    win_rate: float | None
    current_drawdown_pct: float
    holdings: list[PaperHoldingResponse]
