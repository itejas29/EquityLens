from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RecommendationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stock_id: int
    symbol: str
    sector: str | None
    generated_at: datetime
    technical_score: float | None
    fundamental_score: float | None
    valuation_score: float | None
    risk_score: float | None
    overall_score: float | None
    signal: str | None
    entry_low: float | None
    entry_high: float | None
    target_price: float | None
    stop_loss: float | None
    risk_reward: float | None
    stop_method: str | None
    missing_inputs: dict[str, list[str]] | None
    # Secondary ML signal — see docs/ml_results.md. Additive only, never
    # folded into overall_score. None if no model is trained yet or the
    # stock is missing a required feature (e.g. no fundamentals snapshot).
    ml_probability: float | None = None


class RunUniverseResponse(BaseModel):
    scored: int
    signal_distribution: dict[str, int]
