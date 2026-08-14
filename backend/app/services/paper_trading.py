"""Paper trading: virtual buy/sell against real latest close prices, same
transaction cost model as backtesting. Long-only, one open position per
stock per account (no pyramiding) to keep P&L per trade unambiguous.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.backtest_config import DEFAULT_TRANSACTION_COST_PCT
from app.core.paper_trading_config import DEFAULT_VIRTUAL_CAPITAL
from app.models.paper_trading import PaperAccount, PaperTrade
from app.models.price_history import PriceHistory
from app.models.stock import Stock


class PaperTradingError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _half_cost(fill_price: float, quantity: int) -> float:
    return fill_price * quantity * (DEFAULT_TRANSACTION_COST_PCT / 2)


def get_or_create_account(db: Session, user_id: int) -> PaperAccount:
    account = db.query(PaperAccount).filter(PaperAccount.user_id == user_id).first()
    if account is not None:
        return account

    account = PaperAccount(
        user_id=user_id,
        virtual_capital=DEFAULT_VIRTUAL_CAPITAL,
        cash=DEFAULT_VIRTUAL_CAPITAL,
        peak_equity=DEFAULT_VIRTUAL_CAPITAL,
    )
    db.add(account)
    db.flush()
    return account


def _latest_close(db: Session, stock_id: int) -> float | None:
    row = db.query(PriceHistory).filter(PriceHistory.stock_id == stock_id).order_by(PriceHistory.date.desc()).first()
    if row is None or row.close is None:
        return None
    return float(row.close)


def buy(db: Session, user_id: int, symbol: str, quantity: int) -> PaperTrade:
    if quantity <= 0:
        raise PaperTradingError("quantity must be positive")

    stock = db.query(Stock).filter(Stock.symbol == symbol.upper()).first()
    if stock is None:
        raise PaperTradingError(f"Stock '{symbol}' not found")

    account = get_or_create_account(db, user_id)

    existing_open = (
        db.query(PaperTrade)
        .filter(PaperTrade.account_id == account.id, PaperTrade.stock_id == stock.id, PaperTrade.status == "open")
        .first()
    )
    if existing_open is not None:
        raise PaperTradingError(f"Already holding an open position in '{stock.symbol}' — sell it before buying more")

    close = _latest_close(db, stock.id)
    if close is None:
        raise PaperTradingError(f"No price data available for '{stock.symbol}'")

    fill_price = close
    cost = _half_cost(fill_price, quantity)
    total_cash_out = fill_price * quantity + cost
    if total_cash_out > float(account.cash):
        raise PaperTradingError("Insufficient cash for this trade")

    effective_entry = fill_price + cost / quantity

    trade = PaperTrade(
        account_id=account.id,
        stock_id=stock.id,
        side="buy",
        quantity=quantity,
        price=round(effective_entry, 2),
        status="open",
    )
    db.add(trade)

    account.cash = round(float(account.cash) - total_cash_out, 2)
    # Flush before ratcheting: the peak-equity query below reads open
    # positions straight from the DB (autoflush is off on this session), so
    # the just-added trade must be persisted first or it won't be counted.
    db.flush()
    _ratchet_peak_equity(db, account)
    db.flush()
    return trade


def sell(db: Session, user_id: int, symbol: str) -> PaperTrade:
    stock = db.query(Stock).filter(Stock.symbol == symbol.upper()).first()
    if stock is None:
        raise PaperTradingError(f"Stock '{symbol}' not found")

    account = get_or_create_account(db, user_id)

    trade = (
        db.query(PaperTrade)
        .filter(PaperTrade.account_id == account.id, PaperTrade.stock_id == stock.id, PaperTrade.status == "open")
        .first()
    )
    if trade is None:
        raise PaperTradingError(f"No open position in '{stock.symbol}'")

    close = _latest_close(db, stock.id)
    if close is None:
        raise PaperTradingError(f"No price data available for '{stock.symbol}'")

    fill_price = close
    cost = _half_cost(fill_price, trade.quantity)
    proceeds = fill_price * trade.quantity - cost
    effective_exit = fill_price - cost / trade.quantity
    pnl = (effective_exit - float(trade.price)) * trade.quantity

    trade.exit_price = round(effective_exit, 2)
    trade.exit_at = datetime.now(timezone.utc)
    trade.status = "closed"
    trade.pnl = round(pnl, 2)

    account.cash = round(float(account.cash) + proceeds, 2)
    # Same reasoning as buy(): flush the status="closed" change first so the
    # ratchet's open-positions query doesn't still see this trade as open
    # (which would double-count it — once via the cash just credited, once
    # via its stale "open" market value).
    db.flush()
    _ratchet_peak_equity(db, account)
    db.flush()
    return trade


@dataclass
class HoldingView:
    trade: PaperTrade
    symbol: str
    current_price: float | None
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None


@dataclass
class AccountSummary:
    account: PaperAccount
    holdings: list[HoldingView]
    market_value: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    win_rate: float | None
    current_drawdown_pct: float


def _ratchet_peak_equity(db: Session, account: PaperAccount) -> None:
    """Recompute equity now and raise peak_equity if it's a new high —
    called on every trade so drawdown has an up-to-date reference point."""
    open_trades = db.query(PaperTrade).filter(PaperTrade.account_id == account.id, PaperTrade.status == "open").all()
    market_value = 0.0
    for t in open_trades:
        close = _latest_close(db, t.stock_id)
        if close is not None:
            market_value += close * t.quantity
    equity = float(account.cash) + market_value
    if equity > float(account.peak_equity):
        account.peak_equity = round(equity, 2)


def get_account_summary(db: Session, user_id: int) -> AccountSummary:
    account = get_or_create_account(db, user_id)
    _ratchet_peak_equity(db, account)
    db.flush()

    all_trades = db.query(PaperTrade).filter(PaperTrade.account_id == account.id).all()
    open_trades = [t for t in all_trades if t.status == "open"]
    closed_trades = [t for t in all_trades if t.status == "closed"]

    holdings: list[HoldingView] = []
    market_value = 0.0
    unrealized_pnl_total = 0.0
    for t in open_trades:
        stock = db.query(Stock).filter(Stock.id == t.stock_id).first()
        close = _latest_close(db, t.stock_id)
        unrealized_pnl = None
        unrealized_pnl_pct = None
        if close is not None:
            entry = float(t.price)
            unrealized_pnl = round((close - entry) * t.quantity, 2)
            unrealized_pnl_pct = round((close - entry) / entry * 100, 2)
            market_value += close * t.quantity
            unrealized_pnl_total += unrealized_pnl
        holdings.append(
            HoldingView(
                trade=t,
                symbol=stock.symbol if stock else "?",
                current_price=close,
                unrealized_pnl=unrealized_pnl,
                unrealized_pnl_pct=unrealized_pnl_pct,
            )
        )

    realized_pnl = round(sum(float(t.pnl) for t in closed_trades if t.pnl is not None), 2)
    equity = round(float(account.cash) + market_value, 2)

    win_rate = None
    if closed_trades:
        wins = sum(1 for t in closed_trades if t.pnl is not None and float(t.pnl) > 0)
        win_rate = round(wins / len(closed_trades) * 100, 2)

    peak = float(account.peak_equity)
    current_drawdown_pct = round((equity - peak) / peak * 100, 2) if peak > 0 else 0.0

    return AccountSummary(
        account=account,
        holdings=holdings,
        market_value=round(market_value, 2),
        equity=equity,
        realized_pnl=realized_pnl,
        unrealized_pnl=round(unrealized_pnl_total, 2),
        win_rate=win_rate,
        current_drawdown_pct=current_drawdown_pct,
    )
