from datetime import date as date_type

from sqlalchemy import Date, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Fundamentals(Base):
    __tablename__ = "fundamentals"
    __table_args__ = (UniqueConstraint("stock_id", "as_of_date", name="uq_fundamentals_stock_as_of_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    as_of_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    pe_ratio: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    pb_ratio: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    roe: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    roce: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    debt_to_equity: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    revenue_growth: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    eps_growth: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    profit_growth: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    operating_margin: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
