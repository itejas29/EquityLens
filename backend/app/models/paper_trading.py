"""The paper-trading ledger.

Mapped[Decimal], not Mapped[float]. SQLAlchemy returns Decimal for a Numeric
column regardless of the annotation, so `Mapped[float]` here was a type lie —
and it is the lie the service layer believed: it read these columns out with
float(), did every calculation in binary floating point, and wrote the result
back, which is how cash drifted from the exact ledger. See the
money-is-Decimal section of services/paper_trading.py.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Date, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PaperAccount(Base):
    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    virtual_capital: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # Highest equity (cash + mark-to-market of open positions) observed at
    # any trade/query event — not a continuous daily curve, so drawdown
    # derived from it is checked-at-events, not truly continuous.
    peak_equity: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PaperTrade(Base):
    __tablename__ = "paper_trades"
    # At most ONE open position per account per stock, enforced by the
    # database rather than only by the check in paper_trading.buy().
    #
    # A partial index, because the rule applies to open rows only: an account
    # may close a position and re-enter the same stock any number of times,
    # and every one of those closed rows would collide under a plain unique
    # constraint.
    #
    # Belt and braces with the SELECT ... FOR UPDATE in get_or_create_account,
    # deliberately. The lock is what makes concurrent buys correct; this index
    # is what makes a future code path that forgets the lock fail loudly
    # instead of quietly pyramiding. Measured before the lock existed: two
    # concurrent buys produced two open positions in one stock.
    __table_args__ = (
        Index(
            "uq_paper_trade_open_position",
            "account_id",
            "stock_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
            sqlite_where=text("status = 'open'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), nullable=False)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)  # "buy" (long only, no shorting)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)  # cost-loaded entry fill price, per share
    # Exact cash debited from the account to open this position (notional +
    # entry commission), stored because `price` is rounded to the paisa and
    # `price * quantity` therefore drifts from what the ledger actually paid.
    # All P&L is measured against this so realized/unrealized always reconcile
    # with cash — see the P&L identity documented in services/paper_trading.py.
    cost_basis: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")  # "open" | "closed"
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    exit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pnl: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    # Populated only for trades opened by the AI trading loop (services/ai_trading.py),
    # from the DailySignal it bought against — a human manual buy leaves these NULL,
    # per the "missing data stays NULL" rule, since a manual position has no strategy
    # stop/target to hold it to. Stored at entry time rather than re-derived, because
    # DailySignal is a frozen daily snapshot and the row that produced this trade may
    # no longer be "today's" shortlist by the time a sell check runs.
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    entry_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # Why the AI loop closed this position: "stop" | "target" | "horizon" | "regime".
    # NULL for manual trades and for still-open ones.
    exit_reason: Mapped[str | None] = mapped_column(String(10), nullable=True)


class PaperEquitySnapshot(Base):
    """Daily snapshot of the paper trading portfolio to construct equity curves."""

    __tablename__ = "paper_equity_snapshots"
    # One row per account per session. Without this a re-run of the 20:00
    # incremental for the same date silently appends a second point to the
    # equity curve instead of correcting the first.
    __table_args__ = (
        UniqueConstraint("account_id", "date", name="uq_paper_equity_snapshot_account_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), nullable=False)
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    
    cash: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    portfolio_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_equity: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    
    daily_return: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    cumulative_return: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    nifty_return: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    drawdown: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
