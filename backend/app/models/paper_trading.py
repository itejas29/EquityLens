from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PaperAccount(Base):
    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    virtual_capital: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    cash: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    # Highest equity (cash + mark-to-market of open positions) observed at
    # any trade/query event — not a continuous daily curve, so drawdown
    # derived from it is checked-at-events, not truly continuous.
    peak_equity: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PaperTrade(Base):
    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), nullable=False)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)  # "buy" (long only, no shorting)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)  # cost-loaded entry fill price, per share
    # Exact cash debited from the account to open this position (notional +
    # entry commission), stored because `price` is rounded to the paisa and
    # `price * quantity` therefore drifts from what the ledger actually paid.
    # All P&L is measured against this so realized/unrealized always reconcile
    # with cash — see the P&L identity documented in services/paper_trading.py.
    cost_basis: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")  # "open" | "closed"
    exit_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    exit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pnl: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
