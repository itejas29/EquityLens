from datetime import date as date_type, datetime

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


class AIRegimeResponse(BaseModel):
    label: str | None          # NORMAL | CAUTION | None when it cannot be computed
    nifty_close: float | None
    nifty_200dma: float | None
    distance_pct: float | None  # NIFTY close vs its 200-day average


class AILastRunResponse(BaseModel):
    run_date: date_type
    status: str
    finished_at: datetime | None
    bought_count: int
    sold_count: int


class AIStatusResponse(BaseModel):
    enabled: bool
    paper_only: bool            # always true: there is no broker connection
    regime: AIRegimeResponse
    last_run: AILastRunResponse | None
    next_run_at: datetime       # next weekday 09:20 IST; exchange holidays are not known
    max_positions: int
    open_positions: int


class AIToggleRequest(BaseModel):
    enabled: bool


class AILogEntryResponse(BaseModel):
    id: int
    created_at: datetime
    run_date: date_type
    action: str
    symbol: str | None
    quantity: int | None
    price: float | None
    reason: str


class AILogResponse(BaseModel):
    total: int
    entries: list[AILogEntryResponse]


class AIPositionResponse(BaseModel):
    symbol: str
    quantity: int
    entry_date: date_type
    entry_price: float
    current_price: float | None
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None
    days_held: int
    stop_loss: float | None
    target_price: float | None
    stop_distance_pct: float | None    # how far the price is above the stop
    target_distance_pct: float | None  # how far the target is above the price


class AIClosedTradeResponse(BaseModel):
    symbol: str
    entry_date: date_type
    exit_date: date_type
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float | None
    holding_days: int
    exit_reason: str | None


class AIHistoryStats(BaseModel):
    total_trades: int
    win_rate_pct: float | None
    avg_win_pct: float | None
    avg_loss_pct: float | None
    profit_factor: float | None
    avg_holding_days: float | None


class AIHistoryResponse(BaseModel):
    stats: AIHistoryStats
    total: int
    trades: list[AIClosedTradeResponse]
