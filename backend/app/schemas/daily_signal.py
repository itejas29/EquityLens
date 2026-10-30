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


# --- Realistic forward record (services/forward_scoring.py) ------------------


class BenchmarkComparison(BaseModel):
    sample: int
    mean_excess_pct: float | None = None
    beat_rate_pct: float | None = None


class ReturnStats(BaseModel):
    sample: int
    mean_net_return_pct: float | None = None
    median_net_return_pct: float | None = None
    win_rate_pct: float | None = None
    # None when there was no losing trade to divide by — undefined, not infinite.
    profit_factor: float | None = None


class HorizonStats(ReturnStats):
    horizon_sessions: int
    vs: dict[str, BenchmarkComparison]


class ManagedStats(ReturnStats):
    exits: dict[str, int]
    ambiguous_same_bar: int
    still_open: int
    mean_holding_sessions: float | None = None
    vs: dict[str, BenchmarkComparison]


class ExcursionStats(BaseModel):
    sample: int
    median_mae_pct: float | None = None
    median_mfe_pct: float | None = None
    reached_stop_pct: float | None = None
    reached_target_pct: float | None = None


class ExcessInterval(BaseModel):
    positions: int
    horizon_sessions: int | None = None
    mean_excess_pct: float | None = None
    ci95_low: float | None = None
    ci95_high: float | None = None
    weeks: int | None = None
    excludes_zero: bool | None = None


class PositionCounts(BaseModel):
    opened: int
    unique_stocks: int
    calls_absorbed_into_open_positions: int
    unfilled: dict[str, int]
    fill_rate_pct: float | None = None
    filled_at_open: int
    filled_at_entry_high: int


class RecordScore(BaseModel):
    positions: PositionCounts
    horizons: list[HorizonStats]
    managed: ManagedStats
    excursions: ExcursionStats
    uncertainty: dict[str, ExcessInterval]


class ExcludedDay(BaseModel):
    date: date_type
    flags: list[str]


class RealisticTrackRecordResponse(BaseModel):
    exit_rule: str
    round_trip_cost_pct: float
    max_hold_sessions: int
    benchmarks: dict[str, str]
    primary_benchmark: str
    selection_control: str
    first_call_date: date_type | None = None
    last_call_date: date_type | None = None
    published_days: int
    published_calls: int
    clean_days: int
    clean_calls: int
    excluded_days: list[ExcludedDay]
    flag_counts: dict[str, int]
    stale_reference_days: list[date_type]
    clean: RecordScore
    all_published: RecordScore
