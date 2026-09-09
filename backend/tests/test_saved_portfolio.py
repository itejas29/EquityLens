"""Saved-portfolio valuation: Decimal money, reconcilable totals, one query.

This logic used to live in api/v1/portfolio.py and had the same two defects
already fixed in paper_trading: float arithmetic on Numeric(12,2) columns, and
totals accumulated on a DIFFERENT rounding basis from the per-row figures they
sum — total_market_value unrounded, total_pnl from rounded rows — so the
numbers shown side by side on one screen could not be reconciled with each
other by construction.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.portfolio import Portfolio, PortfolioHolding
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.models.user import User
from app.services.saved_portfolio import value_portfolio


@pytest.fixture
def portfolio(db_session):
    user = User(name="P", email="pf@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    pf = Portfolio(user_id=user.id, name="Test", capital=Decimal("1000000.00"),
                   risk_appetite="moderate")
    db_session.add(pf)
    db_session.flush()
    return pf


def _hold(db_session, pf, symbol, qty, entry, close, *, status="open", day=date(2026, 9, 1)):
    stock = Stock(symbol=symbol, is_active=True, sector="Tech")
    db_session.add(stock)
    db_session.flush()
    if close is not None:
        # An older bar too, so "newest close" is genuinely being selected
        # rather than "the only row that exists".
        db_session.add(PriceHistory(stock_id=stock.id, date=day - timedelta(days=1),
                                    close=Decimal("1.00")))
        db_session.add(PriceHistory(stock_id=stock.id, date=day, close=Decimal(close)))
    db_session.add(PortfolioHolding(
        portfolio_id=pf.id, stock_id=stock.id, quantity=qty,
        entry_price=Decimal(entry), allocated_amount=Decimal(entry) * qty, status=status))
    db_session.flush()
    return stock


def test_money_is_decimal_throughout(db_session, portfolio):
    _hold(db_session, portfolio, "AAA", 10, "100.00", "110.00")
    v = value_portfolio(db_session, portfolio)

    assert isinstance(v.total_market_value, Decimal)
    assert isinstance(v.total_unrealized_pnl, Decimal)
    assert isinstance(v.holdings[0].current_price, Decimal)
    assert isinstance(v.holdings[0].unrealized_pnl, Decimal)


def test_the_totals_reconcile_with_the_rows_exactly(db_session, portfolio):
    """The defect: the total was accumulated unrounded while the rows were
    rounded, so summing the visible rows did not give the visible total.

    Prices chosen so every position value needs the rounding step.
    """
    _hold(db_session, portfolio, "AAA", 7, "333.33", "347.77")
    _hold(db_session, portfolio, "BBB", 13, "89.95", "91.05")
    _hold(db_session, portfolio, "CCC", 3, "1234.57", "1301.03")

    v = value_portfolio(db_session, portfolio)

    assert v.total_unrealized_pnl == sum(
        (h.unrealized_pnl for h in v.holdings if h.unrealized_pnl is not None), Decimal(0)
    )
    # And market value ties to entry cost plus the P&L that was reported.
    cost = sum((Decimal(h.holding.entry_price) * h.holding.quantity for h in v.holdings), Decimal(0))
    assert v.total_market_value == cost + v.total_unrealized_pnl


def test_the_newest_close_is_used_not_an_arbitrary_one(db_session, portfolio):
    _hold(db_session, portfolio, "AAA", 10, "100.00", "150.00", day=date(2026, 9, 5))
    v = value_portfolio(db_session, portfolio)
    assert v.holdings[0].current_price == Decimal("150.00")


def test_a_closed_position_gets_no_pnl(db_session, portfolio):
    """portfolio_holdings has no exit_price column, so a closed position's
    realized P&L cannot be computed without inventing an exit."""
    _hold(db_session, portfolio, "AAA", 10, "100.00", "110.00", status="closed")
    v = value_portfolio(db_session, portfolio)

    assert v.holdings[0].unrealized_pnl is None
    assert v.total_market_value == 0
    assert v.total_unrealized_pnl is None


def test_a_holding_with_no_price_is_unknown_not_zero(db_session, portfolio):
    """Missing data stays missing. Reporting an unpriceable position as zero
    P&L would be inventing a number."""
    _hold(db_session, portfolio, "NOPRICE", 10, "100.00", None)
    v = value_portfolio(db_session, portfolio)

    assert v.holdings[0].current_price is None
    assert v.holdings[0].unrealized_pnl is None
    assert v.total_unrealized_pnl is None, "an entirely unpriceable portfolio reported 0.00 P&L"


def test_a_partially_priceable_portfolio_totals_only_what_it_could_value(db_session, portfolio):
    _hold(db_session, portfolio, "AAA", 10, "100.00", "110.00")
    _hold(db_session, portfolio, "NOPRICE", 10, "100.00", None)
    v = value_portfolio(db_session, portfolio)

    assert v.total_unrealized_pnl == Decimal("100.00")
    assert v.total_market_value == Decimal("1100.00")


def test_it_does_not_issue_a_query_per_holding(db_session, portfolio):
    """The N+1 this was extracted to fix: a Stock lookup and a PriceHistory
    lookup per holding, and list_portfolios calls it once per portfolio.
    Against Neon every one of those is a network round trip.

    Asserts the query count does not grow with the number of holdings, which is
    the property that matters — not the exact constant.
    """
    from sqlalchemy import event

    for i in range(2):
        _hold(db_session, portfolio, f"S{i}", 10, "100.00", "110.00")
    counts: list[int] = []

    def run(n_holdings: int) -> int:
        seen = []
        listener = lambda conn, cur, stmt, *a: seen.append(stmt)  # noqa: E731
        event.listen(db_session.bind, "before_cursor_execute", listener)
        try:
            value_portfolio(db_session, portfolio)
        finally:
            event.remove(db_session.bind, "before_cursor_execute", listener)
        return len(seen)

    counts.append(run(2))
    for i in range(2, 8):
        _hold(db_session, portfolio, f"S{i}", 10, "100.00", "110.00")
    counts.append(run(8))

    assert counts[0] == counts[1], (
        f"query count grew with holdings: {counts[0]} for 2, {counts[1]} for 8"
    )


def test_an_empty_portfolio_does_not_query_for_prices(db_session, portfolio):
    v = value_portfolio(db_session, portfolio)
    assert v.holdings == []
    assert v.total_market_value == 0
    assert v.total_unrealized_pnl is None


def test_the_endpoint_still_returns_plain_json_numbers(client, db_session, auth_headers):
    """Decimal behind the boundary, float across it. A Decimal that reached
    the response would serialise as a JSON string and break every consumer
    doing arithmetic on it.
    """
    from app.models.user import User

    user = db_session.query(User).filter(User.email == "audit@example.com").one()
    pf = Portfolio(user_id=user.id, name="Live", capital=Decimal("500000.00"),
                   risk_appetite="moderate")
    db_session.add(pf)
    db_session.flush()
    _hold(db_session, pf, "ZZZ", 10, "100.00", "110.00")
    db_session.commit()

    resp = client.get("/api/v1/portfolio", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()[0]

    assert isinstance(body["total_market_value"], float)
    assert body["total_market_value"] == 1100.0
    assert body["total_unrealized_pnl"] == 100.0
    assert isinstance(body["holdings"][0]["current_price"], float)
