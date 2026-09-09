"""Mark-to-market view of a saved portfolio.

Lifted out of api/v1/portfolio.py, where it had grown into the largest piece of
business logic in the API layer — against the project's own convention that
logic lives in app/services/ and endpoints translate.

Two defects came with it, both the same ones already fixed in
services/paper_trading.py:

MONEY WAS FLOAT. Every column involved is Numeric(12,2) and every value was
read out with float() before any arithmetic. Worse than the arithmetic itself,
the two totals were accumulated on DIFFERENT rounding bases —
`total_market_value += current_price * quantity` unrounded, while
`total_pnl += round(unrealized_pnl, 2)` — so the two figures shown side by side
on the same screen could not be reconciled with each other by construction.

IT WAS AN N+1, TWICE OVER. A Stock lookup and a PriceHistory lookup per
holding, inside the loop, and list_portfolios calls this once per portfolio.
Against Neon each of those is a network round trip; this is the same cost that
made /daily-signals/run hang past a two-minute timeout and is documented at
length in daily_signals._momentum_scores. Both are now one query for the whole
portfolio.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, tuple_
from sqlalchemy.orm import Session

from app.models.portfolio import Portfolio, PortfolioHolding
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.services.paper_trading import quantise, to_money


@dataclass
class HoldingValuation:
    holding: PortfolioHolding
    symbol: str
    sector: str | None
    current_price: Decimal | None
    unrealized_pnl: Decimal | None
    unrealized_pnl_pct: Decimal | None


@dataclass
class PortfolioValuation:
    portfolio: Portfolio
    holdings: list[HoldingValuation]
    total_market_value: Decimal
    # None, not 0, when no holding could be valued — a portfolio whose prices
    # are all missing has an UNKNOWN P&L, and reporting that as zero would be
    # inventing a number. Same reasoning as the NULL rule for missing data.
    total_unrealized_pnl: Decimal | None


def _latest_closes(db: Session, stock_ids: list[int]) -> dict[int, Decimal]:
    """Newest stored close per stock, in one query.

    A correlated max(date) per stock rather than loading the series: the join
    keeps this to a single round trip regardless of how many holdings there
    are.
    """
    if not stock_ids:
        return {}

    newest = (
        db.query(PriceHistory.stock_id, func.max(PriceHistory.date).label("date"))
        .filter(PriceHistory.stock_id.in_(stock_ids), PriceHistory.close.isnot(None))
        .group_by(PriceHistory.stock_id)
        .subquery()
    )
    rows = (
        db.query(PriceHistory.stock_id, PriceHistory.close)
        .join(newest, tuple_(PriceHistory.stock_id, PriceHistory.date)
              == tuple_(newest.c.stock_id, newest.c.date))
        .filter(PriceHistory.close.isnot(None))
        .all()
    )
    return {stock_id: to_money(close) for stock_id, close in rows}


def value_portfolio(db: Session, portfolio: Portfolio) -> PortfolioValuation:
    holdings = db.query(PortfolioHolding).filter(
        PortfolioHolding.portfolio_id == portfolio.id).all()
    stock_ids = [h.stock_id for h in holdings]

    stocks = {s.id: s for s in db.query(Stock).filter(Stock.id.in_(stock_ids)).all()} if stock_ids else {}
    closes = _latest_closes(db, stock_ids)

    valued: list[HoldingValuation] = []
    total_market_value = Decimal("0")
    total_pnl = Decimal("0")
    any_valued = False

    for h in holdings:
        stock = stocks.get(h.stock_id)
        current_price = closes.get(h.stock_id)

        unrealized_pnl = None
        unrealized_pnl_pct = None
        # Only open positions get a P&L — portfolio_holdings has no exit_price
        # column, so a closed position's realized P&L genuinely can't be
        # computed without fabricating an exit price.
        if h.status == "open" and current_price is not None:
            entry_price = to_money(h.entry_price)
            quantity = Decimal(h.quantity)
            # Quantised once, and the SAME figure feeds both the per-holding
            # P&L and the running total, so the totals reconcile with the rows
            # above them instead of being accumulated on a different basis.
            position_value = quantise(current_price * quantity)
            cost = quantise(entry_price * quantity)
            unrealized_pnl = position_value - cost
            if entry_price > 0:
                unrealized_pnl_pct = quantise(
                    (current_price - entry_price) / entry_price * Decimal(100))
            total_market_value += position_value
            total_pnl += unrealized_pnl
            any_valued = True

        valued.append(HoldingValuation(
            holding=h,
            symbol=stock.symbol if stock else "?",
            sector=stock.sector if stock else None,
            current_price=current_price,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_pct=unrealized_pnl_pct,
        ))

    return PortfolioValuation(
        portfolio=portfolio,
        holdings=valued,
        total_market_value=total_market_value,
        total_unrealized_pnl=total_pnl if any_valued else None,
    )
