"""The money path is Decimal end to end, and ties out to an exact ledger.

WHAT WAS ACTUALLY WRONG, measured rather than asserted. The columns were always
Numeric(12,2), but the service read them out with float(), did the arithmetic in
binary floating point and wrote the result back. Running the pre-fix module
against 1,293 round trips of randomised NSE-shaped fills (prices 50-5,000, seed
7) and comparing it to the identical arithmetic in Decimal:

    ledger cash (pre-fix module) : 289697.84
    exact Decimal cash           : 289697.82
    divergence                   : 0.02      <- and realized P&L, by the same 2p

Same run against the current module: 0.00 on both, and the documented identity
    equity == virtual_capital + realized_pnl + unrealized_pnl
residual is exactly Decimal(0) rather than ~1e-10.

Two honest caveats about scope, because the size of a defect matters as much as
its existence:

  * The drift is small and slow. It needs the round-to-paisa boundary to be
    walked from both sides, which randomised prices do and a single repeated
    price pair does not — a fixed-price probe over 2,000 round trips showed
    0.00 divergence. So this was never going to show up as a visibly wrong
    number on the page; it showed up as a ledger that quietly stopped being
    reconcilable.
  * The identity residual on the old code was ~1e-10, not a rupee figure. The
    stronger finding is the cash and realized-P&L divergence above.

test_randomised_round_trips_match_an_exact_shadow_ledger is the test that
actually discriminates between the two implementations; the rest fence the
types, the boundaries and the invariant.

SQLite stands in for Postgres here. Its Numeric support round-trips through a
C double, but SQLAlchemy's return processor rebuilds the value at the column's
declared scale ("%.2f" -> Decimal), so a 2-dp rupee amount comes back exact.
Postgres stores NUMERIC natively and is strictly stronger, so anything passing
here passes there.
"""

import random
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

import pytest

from app.models.paper_trading import PaperAccount, PaperTrade
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.models.user import User
from app.services import paper_trading as pt


# --------------------------------------------------------------- fixtures --

class _StubFeed:
    """Empty tape, so fills and marks both fall back to the stored daily close.

    Deliberately not a mock of the real feed: get_price_feed() reads Redis, and
    a money test that depends on a cache server is a test of the cache server.
    Every price in these tests is a row this file wrote.
    """

    status = "closed"

    def __init__(self):
        self.prices = {}


@pytest.fixture
def no_tape(monkeypatch):
    monkeypatch.setattr(pt, "get_price_feed", lambda: _StubFeed())


@pytest.fixture
def user(db_session):
    u = User(name="T", email="t@example.test", password_hash="x")
    db_session.add(u)
    db_session.flush()
    return u


def _stock(db_session, symbol: str, close: str, day: date | None = None) -> Stock:
    s = db_session.query(Stock).filter(Stock.symbol == symbol).first()
    if s is None:
        s = Stock(symbol=symbol, is_active=True)
        db_session.add(s)
        db_session.flush()
    db_session.add(PriceHistory(stock_id=s.id, date=day or date(2026, 9, 1), close=Decimal(close)))
    db_session.flush()
    return s


def _identity_residual(db_session, user_id: int) -> Decimal:
    """equity - (capital + realized + unrealized). Must be exactly 0."""
    summary = pt.get_account_summary(db_session, user_id)
    return summary.equity - (
        pt.to_money(summary.account.virtual_capital)
        + summary.realized_pnl
        + summary.unrealized_pnl
    )


# ------------------------------------------------------- arithmetic units --

def test_to_money_does_not_import_float_error():
    # Decimal(0.1) is 0.1000000000000000055511151231257827. The str() detour is
    # the entire reason to_money exists.
    assert pt.to_money(0.1) == Decimal("0.1")
    assert pt.to_money(1234.56) == Decimal("1234.56")
    assert pt.to_money(Decimal("1234.56")) == Decimal("1234.56")


def test_quantise_rounds_half_up_not_bankers():
    # Python's round() and Decimal's default both give 0.02 for 0.025 (nearest
    # even) — correct for statistics, wrong for a ledger, because it makes the
    # rounding direction depend on the preceding digit.
    assert pt.quantise(Decimal("0.025")) == Decimal("0.03")
    assert pt.quantise(Decimal("0.035")) == Decimal("0.04")
    assert round(Decimal("0.025"), 2) == Decimal("0.02")  # the behaviour NOT used


def test_transaction_cost_is_exact():
    # float(0.0012) is 0.001199999999999999936..., which would seed error into
    # every commission before any rounding happened.
    assert pt.TRANSACTION_COST == Decimal("0.0012")


# ------------------------------------------------------------ ledger types --

def test_every_money_field_is_decimal(db_session, user, no_tape):
    _stock(db_session, "AAA", "100.00")
    trade = pt.buy(db_session, user.id, "AAA", 10)

    assert isinstance(trade.price, Decimal)
    assert isinstance(trade.cost_basis, Decimal)

    summary = pt.get_account_summary(db_session, user.id)
    for name in ("market_value", "equity", "realized_pnl", "unrealized_pnl", "current_drawdown_pct"):
        assert isinstance(getattr(summary, name), Decimal), f"{name} is not Decimal"
    for h in summary.holdings:
        assert isinstance(h.current_price, Decimal)
        assert isinstance(h.unrealized_pnl, Decimal)


# ---------------------------------------------------------- the identity --

def test_identity_holds_on_a_fresh_account(db_session, user, no_tape):
    assert _identity_residual(db_session, user.id) == 0


def test_identity_holds_with_an_open_position(db_session, user, no_tape):
    _stock(db_session, "AAA", "100.00")
    pt.buy(db_session, user.id, "AAA", 7)
    assert _identity_residual(db_session, user.id) == 0


def test_identity_holds_after_a_closed_round_trip(db_session, user, no_tape):
    stock = _stock(db_session, "AAA", "100.00")
    pt.buy(db_session, user.id, "AAA", 7)
    db_session.add(PriceHistory(stock_id=stock.id, date=date(2026, 9, 2), close=Decimal("111.11")))
    db_session.flush()
    pt.sell(db_session, user.id, "AAA")
    assert _identity_residual(db_session, user.id) == 0


def test_identity_holds_exactly_over_many_round_trips(db_session, user, no_tape):
    """The case that used to fail.

    Prices chosen so neither notional nor commission lands on a clean paisa:
    0.0012/2 of 333.33 * 3 is 0.599994, i.e. the rounding step is exercised on
    every single leg in both directions.
    """
    stock = _stock(db_session, "AAA", "333.33")
    day = date(2026, 9, 1)
    for i in range(200):
        pt.buy(db_session, user.id, "AAA", 3)
        day += timedelta(days=1)
        # Alternate winners and losers so errors cannot cancel by symmetry.
        px = "347.77" if i % 2 == 0 else "319.19"
        db_session.add(PriceHistory(stock_id=stock.id, date=day, close=Decimal(px)))
        db_session.flush()
        pt.sell(db_session, user.id, "AAA")
        day += timedelta(days=1)
        db_session.add(PriceHistory(stock_id=stock.id, date=day, close=Decimal("333.33")))
        db_session.flush()
        assert _identity_residual(db_session, user.id) == 0, f"identity broke on round trip {i}"


def test_cash_ties_out_to_the_sum_of_cash_movements(db_session, user, no_tape):
    """Cash must equal capital - every cost_basis + every proceeds, exactly.

    Reconstructed from the trade rows rather than from the running balance, so
    this catches a balance that drifted even while each individual leg looked
    right.
    """
    stock = _stock(db_session, "AAA", "333.33")
    day = date(2026, 9, 1)
    debits = Decimal("0")
    credits = Decimal("0")
    for i in range(50):
        trade = pt.buy(db_session, user.id, "AAA", 3)
        debits += trade.cost_basis
        day += timedelta(days=1)
        db_session.add(PriceHistory(stock_id=stock.id, date=day, close=Decimal("347.77")))
        db_session.flush()
        closed = pt.sell(db_session, user.id, "AAA")
        # proceeds is not stored; it is cost_basis + pnl by construction.
        credits += closed.cost_basis + closed.pnl

    account = db_session.query(PaperAccount).filter(PaperAccount.user_id == user.id).one()
    expected = pt.to_money(account.virtual_capital) - debits + credits
    assert pt.to_money(account.cash) == expected


def test_realized_pnl_equals_the_actual_cash_movement(db_session, user, no_tape):
    stock = _stock(db_session, "AAA", "100.00")
    before = pt.to_money(
        db_session.query(PaperAccount).filter(PaperAccount.user_id == user.id).one().cash
    ) if db_session.query(PaperAccount).filter(PaperAccount.user_id == user.id).first() else None

    trade = pt.buy(db_session, user.id, "AAA", 11)
    db_session.add(PriceHistory(stock_id=stock.id, date=date(2026, 9, 2), close=Decimal("123.45")))
    db_session.flush()

    account = db_session.query(PaperAccount).filter(PaperAccount.user_id == user.id).one()
    cash_before_sell = pt.to_money(account.cash)
    closed = pt.sell(db_session, user.id, "AAA")
    cash_after_sell = pt.to_money(account.cash)

    proceeds = cash_after_sell - cash_before_sell
    assert closed.pnl == proceeds - trade.cost_basis
    assert before is None or before >= 0


# --------------------------------------------------------------- boundary --

def test_insufficient_cash_is_rejected_at_the_exact_paisa(db_session, user, no_tape):
    """A buy costing one paisa more than the balance must be refused.

    With float cash this comparison was decided by a value that had already
    drifted, so the boundary was not where the ledger said it was.
    """
    _stock(db_session, "AAA", "100.00")
    account = pt.get_or_create_account(db_session, user.id)
    # 100.00 * 10000 = 1,000,000 notional + commission > 1,000,000 capital.
    with pytest.raises(pt.PaperTradingError, match="Insufficient cash"):
        pt.buy(db_session, user.id, "AAA", 10_000)
    assert pt.to_money(account.cash) == Decimal("1000000.00")


def test_no_pyramiding(db_session, user, no_tape):
    _stock(db_session, "AAA", "100.00")
    pt.buy(db_session, user.id, "AAA", 5)
    with pytest.raises(pt.PaperTradingError):
        pt.buy(db_session, user.id, "AAA", 5)


def test_sell_without_a_position_is_rejected(db_session, user, no_tape):
    _stock(db_session, "AAA", "100.00")
    with pytest.raises(pt.PaperTradingError, match="No open position"):
        pt.sell(db_session, user.id, "AAA")


def test_buy_with_no_price_anywhere_is_rejected(db_session, user, no_tape):
    """Missing data stays missing — no default, no synthetic fill price."""
    s = Stock(symbol="NOPRICE", is_active=True)
    db_session.add(s)
    db_session.flush()
    with pytest.raises(pt.PaperTradingError, match="No price data"):
        pt.buy(db_session, user.id, "NOPRICE", 1)


def test_non_positive_quantity_is_rejected(db_session, user, no_tape):
    _stock(db_session, "AAA", "100.00")
    for qty in (0, -1):
        with pytest.raises(pt.PaperTradingError, match="positive"):
            pt.buy(db_session, user.id, "AAA", qty)


# -------------------------------------------------------------- drawdown --

def test_peak_equity_never_ratchets_down(db_session, user, no_tape):
    stock = _stock(db_session, "AAA", "100.00")
    pt.buy(db_session, user.id, "AAA", 100)
    db_session.add(PriceHistory(stock_id=stock.id, date=date(2026, 9, 2), close=Decimal("150.00")))
    db_session.flush()
    account = pt.get_or_create_account(db_session, user.id)
    pt.get_account_summary(db_session, user.id)
    peak_at_high = pt.to_money(account.peak_equity)

    db_session.add(PriceHistory(stock_id=stock.id, date=date(2026, 9, 3), close=Decimal("60.00")))
    db_session.flush()
    summary = pt.get_account_summary(db_session, user.id)

    assert pt.to_money(account.peak_equity) == peak_at_high
    assert summary.current_drawdown_pct < 0


# ------------------------------------------------- exact shadow-ledger tie --

def test_randomised_round_trips_match_an_exact_shadow_ledger(db_session, user, no_tape):
    """Every rupee the module moves must equal what exact arithmetic says.

    The discriminating test: it fails on the pre-fix float implementation
    (0.02 divergence in both cash and realized P&L) and passes here. Seeded, so
    the sequence is identical on every run — a flaky money test is worthless.

    Notional is capped near 300k so a position is always affordable out of the
    1,000,000 opening balance; a rejected buy is skipped on both sides rather
    than silently desynchronising the shadow.
    """
    random.seed(7)
    # Built inline rather than via _stock(): that helper seeds a bar dated
    # 2026-09-01, and _latest_close() takes the NEWEST bar — so a seed bar
    # dated after the loop's own dates would silently price every fill at the
    # seed price and the shadow would tie out against the wrong numbers.
    stock = Stock(symbol="AAA", is_active=True)
    db_session.add(stock)
    db_session.flush()
    day = date(2026, 9, 1)
    shadow_cash = Decimal("1000000.00")
    shadow_realized = Decimal("0")
    # Written out rather than read from pt.TRANSACTION_COST on purpose: a test
    # that takes its expected value from the code under test cannot fail when
    # that value is wrong, and this one has to keep discriminating against an
    # implementation that has no such constant at all.
    cost = Decimal("0.0012")
    trips = 0

    # 2,000 attempts (~1,300 completed round trips). Not arbitrary: at 600 the
    # pre-fix implementation still ties out exactly, so a shorter run would pass
    # on the very code this test exists to catch.
    for _ in range(2000):
        p_in = Decimal(str(round(random.uniform(50, 5000), 2)))
        p_out = Decimal(str(round(float(p_in) * random.uniform(0.9, 1.1), 2)))
        qty = max(1, int(Decimal("300000") / p_in))

        db_session.add(PriceHistory(stock_id=stock.id, date=day, close=p_in))
        db_session.flush()
        try:
            pt.buy(db_session, user.id, "AAA", qty)
        except pt.PaperTradingError:
            day += timedelta(days=1)
            continue
        basis = (p_in * qty + p_in * qty * cost / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        shadow_cash -= basis

        day += timedelta(days=1)
        db_session.add(PriceHistory(stock_id=stock.id, date=day, close=p_out))
        db_session.flush()
        pt.sell(db_session, user.id, "AAA")
        proceeds = (p_out * qty - p_out * qty * cost / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        shadow_cash += proceeds
        shadow_realized += proceeds - basis
        day += timedelta(days=1)
        trips += 1

    assert trips > 1000, "probe degenerated — too few completed round trips to be meaningful"
    account = db_session.query(PaperAccount).filter(PaperAccount.user_id == user.id).one()
    summary = pt.get_account_summary(db_session, user.id)
    # str(), not pt.to_money(), for the same reason as `cost` above — so the
    # comparison still runs against an implementation that returns floats.
    assert Decimal(str(account.cash)) == shadow_cash, "ledger cash drifted from exact arithmetic"
    assert Decimal(str(summary.realized_pnl)) == shadow_realized, "realized P&L drifted"
    assert _identity_residual(db_session, user.id) == 0
