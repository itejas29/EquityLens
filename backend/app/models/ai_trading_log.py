"""The AI paper account's decision log and its on/off switch.

AITradingLog: one row per decision the cycle takes — a buy, a sell, a held
position it checked, an entry it skipped and why — so "nothing happened today"
can be told apart from "nothing could be checked". Rows are written inside the
cycle's own transaction, so a cycle that rolls back leaves no log of trades
that did not happen.

AITradingSetting: a single row (id 1). The switch lives in the database, not
in config, because it is changed from the UI and must survive a restart.
"""

from datetime import date as date_type, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AITradingLog(Base):
    __tablename__ = "ai_trading_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    run_date: Mapped[date_type] = mapped_column(Date, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(10), nullable=False)  # buy | sell | hold | skip | regime | toggle
    symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)


class AITradingSetting(Base):
    __tablename__ = "ai_trading_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
