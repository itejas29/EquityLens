"""Paper trading: virtual buy/sell against real latest prices, same
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

MONEY IS DECIMAL, NEVER FLOAT. The columns were always Numeric(12,2), but this
module used to read them out with float() and do every calculation in binary
floating point before writing back. That made the identity above false: over
2,000 simulated round trips the residual was 2.3e-09 rather than 0, and cash
diverged from the exact ledger by 2 paisa. Binary floating point cannot
represent 0.01, so no amount of round(x, 2) recovers it — the error is in the
accumulation, not the display.

Every figure that is or becomes money stays Decimal from the database through
the arithmetic and back. Prices arriving as float from the quote feed are
converted with Decimal(str(x)), not Decimal(x), so they do not carry the
float's own representation error in. Quantisation to the paisa happens exactly
where cash moves, with ROUND_HALF_UP — bankers' rounding (Python's default) is
wrong for an exchange ledger. Conversion to float happens only at the API
boundary, where Pydantic serialises the response.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.core.backtest_config import DEFAULT_TRANSACTION_COST_PCT
from app.core.paper_trading_config import DEFAULT_VIRTUAL_CAPITAL
from app.models.paper_trading import PaperAccount, PaperTrade
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.services.market import get_price_feed

PAISA = Decimal("0.01")
# Round-trip rate as an exact decimal. float(0.0012) is 0.001199999... which
# would seed error into every cost calculation before rounding ever happens.
TRANSACTION_COST = Decimal(str(DEFAULT_TRANSACTION_COST_PCT))


class PaperTradingError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def to_money(value) -> Decimal:
    """Coerce a price/amount to Decimal without importing float error.

    Decimal(0.1) is 0.1000000000000000055511151231257827; Decimal("0.1") is
    exactly 0.1. Quote-feed prices arrive as float, so the str() detour is what
    keeps a 2-decimal NSE price exact.
    """
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def quantise(amount: Decimal) -> Decimal:
    """Round to the paisa, half away from zero — how an exchange settles.

    Python's round() and Decimal's default ROUND_HALF_EVEN both round .005 to
    the nearest even digit, which is right for statistics and wrong for a
    ledger: it makes rounding direction depend on the preceding digit.
    """
    return amount.quantize(PAISA, rounding=ROUND_HALF_UP)


def _half_cost(fill_price: Decimal, quantity: int) -> Decimal:
    """Commission on one leg. Not quantised — the caller folds it into the
    single cash figure that does get quantised, so the paisa is rounded once
    per leg rather than twice."""
    return fill_price * Decimal(quantity) * (TRANSACTION_COST / 2)


def get_or_create_account(db: Session, user_id: int, for_update: bool = False) -> PaperAccount:
    """Fetch (or create) the account. Pass for_update=True before mutating it.

    CONCURRENCY. Postgres defaults to READ COMMITTED, under which

        cash = SELECT cash ...            # both transactions read 1,000,000
        if cost > cash: reject            # both pass
        UPDATE cash = cash - cost         # second write overwrites the first

    loses a debit outright. Reproduced on Postgres 16 with two threads buying
    4,000 shares at 100.00 into a 1,000,000 account: both succeeded, the trades
    recorded 800,480.00 of cost basis, and cash was left at 599,760.00 instead
    of 199,520.00 — 400,240.00 unaccounted for. The same interleaving also put
    TWO open positions in one stock, defeating the no-pyramiding rule this
    module documents.

    SELECT ... FOR UPDATE on the account row is the fix and the right
    granularity: every mutation of this account — cash, and the open-position
    check that reads it — is serialised behind one lock, while different
    accounts stay independent. It is taken FIRST in buy() and sell(), before
    the position query, so the check and the write are inside the same lock.

    SQLite has no row locks (it locks the database on write), and SQLAlchemy
    omits the clause there. The tests still exercise the ordering; the lock
    itself is verified separately by asserting the emitted SQL.
    """
    query = db.query(PaperAccount).filter(PaperAccount.user_id == user_id)
    if for_update:
        # populate_existing() is not optional here, and leaving it out is a
        # lock that does nothing. with_for_update() emits SELECT ... FOR UPDATE
        # and really does take the lock — but if this Session has already
        # loaded the account (ai_trading does exactly that, before calling
        # buy()/sell()), SQLAlchemy returns the identity-mapped instance
        # WITHOUT refreshing its attributes. The lock is then held around a
        # `cash` value read before the lock existed, and the lost update
        # survives untouched. Measured: with the lock but without this line,
        # two concurrent buys still lost a 400,240.00 debit.
        query = query.with_for_update().populate_existing()
    account = query.first()
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


def _latest_close(db: Session, stock_id: int) -> Decimal | None:
    row = db.query(PriceHistory).filter(PriceHistory.stock_id == stock_id).order_by(PriceHistory.date.desc()).first()
    if row is None or row.close is None:
        return None
    return to_money(row.close)


def _mark_prices(db: Session, stock_ids: list[int]) -> dict[int, Decimal]:
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
    return {stock_id: price for stock_id, (price, _) in _marks_with_source(db, stock_ids).items()}


# How a mark was arrived at. This is not decoration: the AI trading loop
# evaluates stop-loss and target against these prices, and the three cases are
# three different levels of confidence in that decision.
MARK_SOURCE_TAPE = "tape"          # a quote from the live/session feed
MARK_SOURCE_STORED_CLOSE = "close" # the newest stored daily bar — see below


def _marks_with_source(db: Session, stock_ids: list[int]) -> dict[int, tuple[Decimal, str]]:
    """_mark_prices, plus where each price came from.

    The distinction matters because of WHEN the AI cycle runs: ~09:20 IST,
    minutes after the open, and today's bar is not written until the 20:00
    incremental. So the newest stored close is YESTERDAY'S. When the tape is
    up, marks are live and a stop is judged against the real price. When the
    tape is down — the fast-quote loop died, Redis is unreachable, the market
    is shut — the fallback silently becomes "yesterday", and a stop that gapped
    through overnight is not seen.

    The fallback is still the right behaviour; a stale price beats no price and
    beats guessing. What was wrong is that it was indistinguishable from a live
    one at the call site, so a degraded risk check looked exactly like a
    healthy one. Callers that act on these prices can now say which they got.
    """
    if not stock_ids:
        return {}

    feed = get_price_feed()
    marks: dict[int, tuple[Decimal, str]] = {}
    for stock_id, symbol in db.query(Stock.id, Stock.symbol).filter(Stock.id.in_(stock_ids)).all():
        quote = feed.prices.get(symbol)
        price = quote.get("price") if quote else None
        if price is not None:
            marks[stock_id] = (to_money(price), MARK_SOURCE_TAPE)
            continue
        close = _latest_close(db, stock_id)
        if close is not None:
            marks[stock_id] = (close, MARK_SOURCE_STORED_CLOSE)
    return marks


def buy(db: Session, user_id: int, symbol: str, quantity: int) -> PaperTrade:
    if quantity <= 0:
        raise PaperTradingError("quantity must be positive")

    stock = db.query(Stock).filter(Stock.symbol == symbol.upper()).first()
    if stock is None:
        raise PaperTradingError(f"Stock '{symbol}' not found")

    # Locked before the open-position check, not after: the check and the cash
    # debit have to be inside the same lock or two concurrent buys can both
    # pass it. See get_or_create_account.
    account = get_or_create_account(db, user_id, for_update=True)

    existing_open = (
        db.query(PaperTrade)
        .filter(PaperTrade.account_id == account.id, PaperTrade.stock_id == stock.id, PaperTrade.status == "open")
        .first()
    )
    if existing_open is not None:
        raise PaperTradingError(f"Already holding an open position in '{stock.symbol}' — sell it before buying more")

    feed = get_price_feed()
    quote = feed.prices.get(stock.symbol)
    fill_price = None
    if quote and quote.get("price"):
        fill_price = to_money(quote["price"])
    else:
        close = _latest_close(db, stock.id)
        if close is not None:
            fill_price = close

    if fill_price is None:
        raise PaperTradingError(f"No price data available for '{stock.symbol}'")

    cost = _half_cost(fill_price, quantity)
    # Quantised once, here. This single figure is both what leaves the cash
    # balance and what all later P&L on this position is measured against.
    cost_basis = quantise(fill_price * Decimal(quantity) + cost)
    if cost_basis > to_money(account.cash):
        raise PaperTradingError("Insufficient cash for this trade")

    # Reported per-share entry: display/reference only, never a P&L input.
    effective_entry = fill_price + cost / Decimal(quantity)

    trade = PaperTrade(
        account_id=account.id,
        stock_id=stock.id,
        side="buy",
        quantity=quantity,
        price=quantise(effective_entry),
        cost_basis=cost_basis,
        status="open",
    )
    db.add(trade)

    account.cash = quantise(to_money(account.cash) - cost_basis)
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

    account = get_or_create_account(db, user_id, for_update=True)

    trade = (
        db.query(PaperTrade)
        .filter(PaperTrade.account_id == account.id, PaperTrade.stock_id == stock.id, PaperTrade.status == "open")
        .first()
    )
    if trade is None:
        raise PaperTradingError(f"No open position in '{stock.symbol}'")

    feed = get_price_feed()
    quote = feed.prices.get(stock.symbol)
    fill_price = None
    if quote and quote.get("price"):
        fill_price = to_money(quote["price"])
    else:
        close = _latest_close(db, stock.id)
        if close is not None:
            fill_price = close

    if fill_price is None:
        raise PaperTradingError(f"No price data available for '{stock.symbol}'")

    cost = _half_cost(fill_price, trade.quantity)
    # Quantised once, mirroring cost_basis on the buy leg.
    proceeds = quantise(fill_price * Decimal(trade.quantity) - cost)
    effective_exit = fill_price - cost / Decimal(trade.quantity)
    # Difference of the two actual cash movements, so realized P&L ties out to
    # the ledger exactly rather than to the rounded per-share prices. Both are
    # already quantised, so their difference is exact in Decimal and needs no
    # further rounding — quantise() here would be a no-op, not a correction.
    pnl = proceeds - to_money(trade.cost_basis)

    trade.exit_price = quantise(effective_exit)
    trade.exit_at = datetime.now(timezone.utc)
    trade.status = "closed"
    trade.pnl = pnl

    account.cash = to_money(account.cash) + proceeds
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
    current_price: Decimal | None
    unrealized_pnl: Decimal | None
    # A percentage, not money: it is a ratio of two exact amounts, reported to
    # two places for display. Kept Decimal so the whole view is one type.
    unrealized_pnl_pct: Decimal | None


@dataclass
class AccountSummary:
    account: PaperAccount
    holdings: list[HoldingView]
    market_value: Decimal
    equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    win_rate: Decimal | None
    current_drawdown_pct: Decimal


def _ratchet_peak_equity(db: Session, account: PaperAccount) -> None:
    """Recompute equity now and raise peak_equity if it's a new high —
    called on every trade so drawdown has an up-to-date reference point."""
    open_trades = db.query(PaperTrade).filter(PaperTrade.account_id == account.id, PaperTrade.status == "open").all()
    market_value = Decimal("0")
    for t in open_trades:
        # Stored closes, NOT the live mark: peak equity is persisted, so pricing
        # it intraday would ratchet the peak higher every time someone happened
        # to open the page during a spike, permanently deepening every drawdown
        # reported afterwards. Drawdown has to be a function of the data, not of
        # when it was looked at — so the peak moves close-to-close.
        close = _latest_close(db, t.stock_id)
        if close is not None:
            market_value += close * Decimal(t.quantity)
    equity = to_money(account.cash) + market_value
    if equity > to_money(account.peak_equity):
        account.peak_equity = quantise(equity)


def get_account_summary(db: Session, user_id: int) -> AccountSummary:
    account = get_or_create_account(db, user_id)
    _ratchet_peak_equity(db, account)
    db.flush()

    all_trades = db.query(PaperTrade).filter(PaperTrade.account_id == account.id).all()
    open_trades = [t for t in all_trades if t.status == "open"]
    closed_trades = [t for t in all_trades if t.status == "closed"]

    holdings: list[HoldingView] = []
    market_value = Decimal("0")
    unrealized_pnl_total = Decimal("0")
    marks = _mark_prices(db, [t.stock_id for t in open_trades])
    for t in open_trades:
        stock = db.query(Stock).filter(Stock.id == t.stock_id).first()
        mark = marks.get(t.stock_id)
        unrealized_pnl = None
        unrealized_pnl_pct = None
        if mark is not None:
            basis = to_money(t.cost_basis)
            position_value = quantise(mark * Decimal(t.quantity))
            # Against cash paid, same basis as realized P&L on the sell leg.
            unrealized_pnl = position_value - basis
            unrealized_pnl_pct = quantise((position_value - basis) / basis * Decimal(100))
            # market_value accumulates the SAME quantised figure that fed
            # unrealized_pnl, so equity == cash + market_value and
            # capital + realized + unrealized agree to the paisa rather than
            # to within a rounding step of each other.
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

    # sum() over exact Decimals: no start=0.0 float seed, no re-rounding. Each
    # t.pnl was quantised once when the trade closed.
    realized_pnl = sum((to_money(t.pnl) for t in closed_trades if t.pnl is not None), Decimal("0"))
    equity = to_money(account.cash) + market_value

    win_rate = None
    if closed_trades:
        wins = sum(1 for t in closed_trades if t.pnl is not None and to_money(t.pnl) > 0)
        win_rate = quantise(Decimal(wins) / Decimal(len(closed_trades)) * Decimal(100))

    peak = to_money(account.peak_equity)
    current_drawdown_pct = quantise((equity - peak) / peak * Decimal(100)) if peak > 0 else Decimal("0.00")

    return AccountSummary(
        account=account,
        holdings=holdings,
        market_value=market_value,
        equity=equity,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl_total,
        win_rate=win_rate,
        current_drawdown_pct=current_drawdown_pct,
    )
