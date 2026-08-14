from datetime import date as date_type

from pydantic import BaseModel, ConfigDict


class IndicatorPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date_type
    dma_50: float | None
    dma_200: float | None
    rsi_14: float | None
    macd: float | None
    macd_signal: float | None
    macd_hist: float | None
    volume_ratio: float | None
    volatility: float | None
    beta: float | None
    max_drawdown: float | None


class ComputeIndicatorsResponse(BaseModel):
    symbol: str
    rows_upserted: int
