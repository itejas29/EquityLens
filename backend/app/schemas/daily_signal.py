from datetime import date as date_type
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class RationaleClause(BaseModel):
    kind: Literal["support", "caution"]
    label: str
    text: str


class DailySignalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rank: int
    stock_id: int
    symbol: str
    company_name: str | None
    sector: str | None

    overall_score: float
    technical_score: float | None
    fundamental_score: float | None
    valuation_score: float | None
    risk_score: float | None
    signal: str

    reference_close: float
    reference_date: date_type
    entry_low: float
    entry_high: float
    stop_loss: float
    target_price: float
    risk_reward: float
    stop_method: str

    # Live comparison against the frozen zone, computed per request.
    latest_price: float | None
    trigger_state: Literal["IN_ZONE", "ABOVE_ZONE", "BELOW_ZONE", "UNKNOWN"]
    # Move from the top of the entry zone to target / stop, as percentages —
    # the two numbers a reader needs to size the trade-off between them.
    upside_pct: float
    downside_pct: float

    rationale: list[RationaleClause]


class DailySignalsResponse(BaseModel):
    date: date_type | None
    generated_at: datetime | None
    count: int
    # --- provenance / freshness (so the UI never implies stale data is today) ---
    # Which strategy produced this snapshot.
    strategy_version: str
    strategy_description: str
    # Last completed market session actually stored. Signals are always derived
    # from this, never from a later date.
    market_data_through: date_type | None
    # True only when `date` is today AND it was built on the newest market data.
    is_current: bool
    # "complete" when signals exist for the latest market data, else "incomplete".
    pipeline_status: str
    # NIFTY 200DMA state driving exposure at generation time.
    regime: str | None = None
    regime_exposure: float | None = None
    nifty_close: float | None = None
    nifty_200dma: float | None = None
    # Transparency: how much of the universe was considered vs. published, so
    # a short list never looks like the whole universe was thin.
    universe_size: int
    signals: list[DailySignalResponse]


class GenerateDailySignalsResponse(BaseModel):
    date: date_type
    published: int
    symbols: list[str]


class MomentumLeader(BaseModel):
    symbol: str
    company_name: str | None
    sector: str | None
    price: float | None
    momentum_percentile: float
    rank: int


class MomentumLeadersResponse(BaseModel):
    date: date_type | None
    universe_size: int
    leaders: list[MomentumLeader]
