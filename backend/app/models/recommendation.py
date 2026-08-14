from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    technical_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    fundamental_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    valuation_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    overall_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    signal: Mapped[str | None] = mapped_column(String(30), nullable=True)
    entry_low: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    entry_high: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    target_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    risk_reward: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    # Per-sub-score list of metric names that were missing/NULL and excluded
    # from that sub-score's weighted average, e.g. {"fundamental": ["roce"]}.
    missing_inputs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # "atr" or "support" — which method produced stop_loss.
    stop_method: Mapped[str | None] = mapped_column(String(10), nullable=True)
