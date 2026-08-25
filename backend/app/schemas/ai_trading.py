from datetime import datetime

from pydantic import BaseModel


class AITradeResponse(BaseModel):
    id: int
    symbol: str
    quantity: int
    price: float
    executed_at: datetime
    status: str
    exit_price: float | None
    exit_at: datetime | None
    pnl: float | None
    stop_loss: float | None
    target_price: float | None
    entry_score: float | None
    exit_reason: str | None


class AIHoldingResponse(BaseModel):
    trade_id: int
    symbol: str
    quantity: int
    entry_price: float
    current_price: float | None
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None
    stop_loss: float | None
    target_price: float | None


class AIAccountResponse(BaseModel):
    virtual_capital: float
    cash: float
    equity: float
    market_value: float
    realized_pnl: float
    unrealized_pnl: float
    win_rate: float | None
    current_drawdown_pct: float
    holdings: list[AIHoldingResponse]
