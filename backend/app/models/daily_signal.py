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


class SignalOutcome(Base):
    """Outcome measurements of a daily signal over 1D, 5D, 10D, and 20D horizons."""

    __tablename__ = "signal_outcomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("daily_signals.id", ondelete="CASCADE"), unique=True, nullable=False)
    
    # What it was measured against
    evaluated_at: Mapped[date_type] = mapped_column(Date, nullable=False)
    current_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    
    # Returns (measured from the signal's reference_close)
    return_1d: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    return_5d: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    return_10d: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    return_20d: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    
    # Excursions measured from reference_close
    max_favorable_excursion: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    max_adverse_excursion: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    
    # Flags
    target_hit: Mapped[bool] = mapped_column(nullable=False, default=False)
    stop_hit: Mapped[bool] = mapped_column(nullable=False, default=False)
    
    # NIFTY benchmark returns over the exact same timeframes
    nifty_return_1d: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    nifty_return_5d: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    nifty_return_10d: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    nifty_return_20d: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
