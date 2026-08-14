from datetime import date as date_type

from sqlalchemy import Date, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Indicator(Base):
    __tablename__ = "indicators"
    __table_args__ = (UniqueConstraint("stock_id", "date", name="uq_indicators_stock_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    date: Mapped[date_type] = mapped_column(Date, nullable=False)
    dma_50: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    dma_200: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    rsi_14: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    macd: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    macd_signal: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    macd_hist: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    volume_ratio: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    volatility: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    beta: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
