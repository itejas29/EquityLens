from datetime import date as date_type
from datetime import datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DailySignal(Base):
    """One stock's entry on one trading day's shortlist.

    Frozen at generation time rather than recomputed on read: the whole point
    of a dated call is that it does not move after the fact. Levels and scores
    here are exactly what was published that morning, which is what makes a
    later "was this call right?" review meaningful.
    """

    __tablename__ = "daily_signals"
    __table_args__ = (UniqueConstraint("date", "stock_id", name="uq_daily_signals_date_stock"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date_type] = mapped_column(Date, nullable=False, index=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 = highest conviction that day

    technical_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    fundamental_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    valuation_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    overall_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    signal: Mapped[str] = mapped_column(String(30), nullable=False)

    # Bar the levels below were derived from — pins the snapshot to a known
    # candle so a later review can tell what the levels were measured against.
    # Usually the prior session's close, since 09:15 IST is the open.
    reference_close: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    reference_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    entry_low: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    entry_high: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    stop_loss: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    target_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    risk_reward: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    stop_method: Mapped[str] = mapped_column(String(10), nullable=False)  # "atr" | "support"

    # Ordered list of plain-English statements, each derived from a metric that
    # actually fed the score. Generated from computed values, never templated
    # text with the numbers left out.
    rationale: Mapped[list] = mapped_column(JSON, nullable=False)

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
