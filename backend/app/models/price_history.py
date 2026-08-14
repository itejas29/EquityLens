from datetime import date as date_type

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Numeric, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PriceHistory(Base):
    __tablename__ = "price_history"
    __table_args__ = (
        UniqueConstraint("stock_id", "date", name="uq_price_history_stock_date"),
        Index("ix_price_history_stock_date_desc", "stock_id", text("date DESC")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    date: Mapped[date_type] = mapped_column(Date, nullable=False)
    open: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    high: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    low: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    close: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
