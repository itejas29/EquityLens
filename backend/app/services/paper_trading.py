"""Paper trading: virtual buy/sell against real latest close prices, same
transaction cost model as backtesting. Long-only, one open position per
stock per account (no pyramiding) to keep P&L per trade unambiguous.

P&L IDENTITY: `equity == virtual_capital + realized_pnl + unrealized_pnl`
holds exactly, at every point. That requires every P&L figure to be measured
against `trade.cost_basis` — the cash actually debited to open the position —
rather than against `trade.price * quantity`. `price` is a per-share figure
rounded to the paisa, so multiplying it back out drifts from the ledger by up
to half a paisa per share per leg, in one systematic direction, accumulating
across trades. Cash is rounded exactly once per leg (at `cost_basis` and at
`proceeds`) and everything downstream reads those rounded figures.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.backtest_config import DEFAULT_TRANSACTION_COST_PCT
from app.core.paper_trading_config import DEFAULT_VIRTUAL_CAPITAL
from app.models.paper_trading import PaperAccount, PaperTrade
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.services.market import get_price_feed


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


def _mark_prices(db: Session, stock_ids: list[int]) -> dict[int, float]:
    """Valuation price per stock_id: the tape when it carries the symbol,
    otherwise the newest stored daily bar.

    Marking deliberately goes through get_price_feed() instead of
    _latest_close(). During a session the newest stored bar is the PREVIOUS
    close — today's bar is not written until the 20:00 incremental — so
    valuing holdings off it freezes every position at yesterday's price for
    the whole trading day, showing only the transaction cost as P&L.

    Fills are a separate decision and still use _latest_close(): the backtest
    this account is benchmarked against fills on daily closes, so moving marks
    to intraday must not silently move fills there too.
    """
    if not stock_ids:
        return {}

    feed = get_price_feed()
    marks: dict[int, float] = {}
    for stock_id, symbol in db.query(Stock.id, Stock.symbol).filter(Stock.id.in_(stock_ids)).all():
        quote = feed.prices.get(symbol)
        price = quote.get("price") if quote else None
        if price is None:
            price = _latest_close(db, stock_id)
        if price is not None:
            marks[stock_id] = float(price)
    return marks


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
    # Rounded once, here. This single figure is both what leaves the cash
    # balance and what all later P&L on this position is measured against.
    cost_basis = round(fill_price * quantity + cost, 2)
    if cost_basis > float(account.cash):
        raise PaperTradingError("Insufficient cash for this trade")

    # Reported per-share entry: display/reference only, never a P&L input.
    effective_entry = fill_price + cost / quantity

    trade = PaperTrade(
        account_id=account.id,
        stock_id=stock.id,
        side="buy",
        quantity=quantity,
        price=round(effective_entry, 2),
        cost_basis=cost_basis,
        status="open",
    )
    db.add(trade)

    account.cash = round(float(account.cash) - cost_basis, 2)
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
    # Rounded once, mirroring cost_basis on the buy leg.
    proceeds = round(fill_price * trade.quantity - cost, 2)
    effective_exit = fill_price - cost / trade.quantity
    # Difference of the two actual cash movements, so realized P&L ties out to
    # the ledger exactly rather than to the rounded per-share prices.
    pnl = proceeds - float(trade.cost_basis)

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
        # Stored closes, NOT the live mark: peak equity is persisted, so pricing
        # it intraday would ratchet the peak higher every time someone happened
        # to open the page during a spike, permanently deepening every drawdown
        # reported afterwards. Drawdown has to be a function of the data, not of
        # when it was looked at — so the peak moves close-to-close.
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
    marks = _mark_prices(db, [t.stock_id for t in open_trades])
    for t in open_trades:
        stock = db.query(Stock).filter(Stock.id == t.stock_id).first()
        mark = marks.get(t.stock_id)
        unrealized_pnl = None
        unrealized_pnl_pct = None
        if mark is not None:
            basis = float(t.cost_basis)
            position_value = mark * t.quantity
            # Against cash paid, same basis as realized P&L on the sell leg.
            unrealized_pnl = round(position_value - basis, 2)
            unrealized_pnl_pct = round((position_value - basis) / basis * 100, 2)
            market_value += position_value
            unrealized_pnl_total += unrealized_pnl
        holdings.append(
            HoldingView(
                trade=t,
                symbol=stock.symbol if stock else "?",
                current_price=mark,
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
