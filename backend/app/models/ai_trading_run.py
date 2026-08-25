"""Tracks each execution of the AI trading loop (app/services/ai_trading.py).

One row per calendar day, created once the cycle runs — the same "check the DB,
not memory" idempotency idiom PipelineRun uses for the price pipeline, so a
process restart never re-triggers the day's cycle. Also doubles as the audit
log for what the cycle actually did, since "bought 2, sold 1" isn't otherwise
visible anywhere but the resulting PaperTrade rows.
"""

from datetime import date as date_type, datetime

from sqlalchemy import Date, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AITradingRun(Base):
    __tablename__ = "ai_trading_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_date: Mapped[date_type] = mapped_column(Date, nullable=False, unique=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")  # running | complete | failed
    bought_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sold_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    regime: Mapped[str | None] = mapped_column(String(10), nullable=True)  # bull | bear | unknown, as seen this run
