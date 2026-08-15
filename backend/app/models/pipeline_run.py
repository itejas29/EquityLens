"""Pipeline run tracking — records the outcome of every data pipeline execution
so that signal generation can gate on data completeness, and the health endpoint
can report exactly what happened.

Each row is one pipeline execution (incremental price update, full rebuild, or
fundamentals refresh). The row is created at run start (status='running') and
updated on completion.
"""

from datetime import date as date_type, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_date: Mapped[date_type] = mapped_column(Date, nullable=False, index=True)
    run_type: Mapped[str] = mapped_column(String(32), nullable=False)  # incremental | full_rebuild | fundamentals
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")  # running | complete | incomplete | failed
    stocks_requested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stocks_succeeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stocks_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_symbols: Mapped[list | None] = mapped_column(JSON, nullable=True)
    failure_details: Mapped[list | None] = mapped_column(JSON, nullable=True)
    signals_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    signal_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(nullable=True)
