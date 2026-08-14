from datetime import date as date_type
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.core.backtest_config import (
    DEFAULT_HORIZON_DAYS,
    DEFAULT_REBALANCE_FREQUENCY,
    DEFAULT_RISK_FREE_RATE,
    DEFAULT_SLIPPAGE_PCT,
    DEFAULT_TRANSACTION_COST_PCT,
)
from app.schemas.portfolio import DISCLAIMER, RiskAppetite


class BacktestRequest(BaseModel):
    start_date: date_type
    end_date: date_type
    initial_capital: float = Field(gt=0)
    risk_appetite: RiskAppetite
    rebalance_frequency: Literal["monthly", "quarterly"] = DEFAULT_REBALANCE_FREQUENCY
    horizon_days: int = Field(default=DEFAULT_HORIZON_DAYS, gt=0)
    transaction_cost_pct: float = Field(default=DEFAULT_TRANSACTION_COST_PCT, ge=0)
    slippage_pct: float = Field(default=DEFAULT_SLIPPAGE_PCT, ge=0)
    risk_free_rate: float = Field(default=DEFAULT_RISK_FREE_RATE, ge=0)

    @model_validator(mode="after")
    def _check_dates(self) -> "BacktestRequest":
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self


class TradeResponse(BaseModel):
    stock_id: int
    symbol: str
    entry_date: date_type
    exit_date: date_type
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    exit_reason: str
    holding_days: int


class EquityPoint(BaseModel):
    date: date_type
    equity: float


class BacktestResponse(BaseModel):
    id: int
    config: BacktestRequest
    metrics: dict
    benchmark_metrics: dict
    equity_curve: list[EquityPoint]
    benchmark_equity_curve: list[EquityPoint]
    trade_log: list[TradeResponse]
    methodology_note: str = (
        "Backtest scores use only technical + risk sub-scores (renormalized). "
        "Fundamental/valuation scores are excluded because the database holds a single "
        "fundamentals snapshot per stock, not a historical time series, so applying them "
        "to past rebalance dates would leak future information into past decisions."
    )
    disclaimer: str = DISCLAIMER
