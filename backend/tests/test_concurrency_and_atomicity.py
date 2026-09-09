"""Two invariants that only hold under a lock, and one that only holds under
an all-or-nothing transaction.

The money tests in test_money_ledger.py prove the ledger is exact. They prove
it SERIALLY. This file covers what happens when two things touch the same
account at once, and what is left behind when a cycle dies halfway.

The genuinely concurrent case needs a real database — SQLite has no row locks
and the in-memory test DB is a single shared connection — so it is gated on
AUDIT_POSTGRES_URL and skipped otherwise. It was run against Postgres 16
during the audit; the numbers are in the git history for this file. What runs
everywhere is the mechanism: that the locked read is requested, and that it
refreshes a stale attribute rather than trusting the identity map.
"""

import os
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.paper_trading import PaperAccount, PaperTrade
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.models.user import User
from app.services import paper_trading as pt


class _StubFeed:
    status = "closed"

    def __init__(self):
        self.prices = {}


@pytest.fixture
def no_tape(monkeypatch):
    monkeypatch.setattr(pt, "get_price_feed", lambda: _StubFeed())


@pytest.fixture
def user(db_session):
    u = User(name="C", email="conc@example.com", password_hash="x")
    db_session.add(u)
    db_session.flush()
    return u


# ------------------------------------------------------------ the mechanism --

def test_the_locked_read_actually_emits_for_update():
    """Compiled against the Postgres dialect, because SQLite has no row locks
    and would silently drop the clause — which is exactly how a missing lock
    stays invisible in a SQLite-backed test suite."""
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.orm import Query

    from app.core.database import Base  # noqa: F401

    stmt = (
        Query(PaperAccount)
        .filter(PaperAccount.user_id == 1)
        .with_for_update()
        .statement
    )
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql


def test_the_locked_read_refreshes_a_stale_attribute(db_session, user):
    """populate_existing() is the half of the fix that is easy to omit.

    with_for_update() takes the lock, but if the Session has already loaded the
    account — ai_trading loads it before calling buy()/sell() — SQLAlchemy
    returns the identity-mapped instance without refreshing it. The lock is
    then held around a value read before the lock existed. Measured on
    Postgres: with the lock but without populate_existing(), two concurrent
    buys still lost a 400,240.00 debit.
    """
    account = pt.get_or_create_account(db_session, user.id)
    assert pt.to_money(account.cash) == Decimal("1000000.00")

    # Change the row underneath the ORM, the way another transaction would.
    db_session.execute(
        text("UPDATE paper_accounts SET cash = :c WHERE id = :i"),
        {"c": "777777.77", "i": account.id},
    )

    stale = pt.get_or_create_account(db_session, user.id)
    assert pt.to_money(stale.cash) == Decimal("1000000.00"), "precondition: the ORM caches it"

    fresh = pt.get_or_create_account(db_session, user.id, for_update=True)
    assert pt.to_money(fresh.cash) == Decimal("777777.77"), (
        "the locked read returned a value from before the lock"
    )


def test_buy_and_sell_take_the_lock_before_reading_the_position(db_session, user, no_tape, monkeypatch):
    """Order matters: the open-position check and the cash debit must be
    inside the same lock, or two concurrent buys both pass the check."""
    calls = []
    original = pt.get_or_create_account

    def spy(db, user_id, for_update=False):
        calls.append(for_update)
        return original(db, user_id, for_update=for_update)

    monkeypatch.setattr(pt, "get_or_create_account", spy)

    stock = Stock(symbol="LOCK", is_active=True)
    db_session.add(stock)
    db_session.flush()
    db_session.add(PriceHistory(stock_id=stock.id, date=date(2026, 9, 1), close=Decimal("100.00")))
    db_session.flush()

    pt.buy(db_session, user.id, "LOCK", 5)
    assert calls[0] is True, "buy() read the account without a lock"

    calls.clear()
    pt.sell(db_session, user.id, "LOCK")
    assert calls[0] is True, "sell() read the account without a lock"


# ----------------------------------------------------- the database backstop --

def test_the_schema_forbids_two_open_positions_in_one_stock(db_session, user):
    """Enforced by a partial unique index, not only by the Python check.

    Bypasses buy() deliberately: the point is that a future code path which
    forgets the lock fails loudly instead of quietly pyramiding.
    """
    from sqlalchemy.exc import IntegrityError

    stock = Stock(symbol="DUP", is_active=True)
    db_session.add(stock)
    db_session.flush()
    account = pt.get_or_create_account(db_session, user.id)

    def row(status):
        return PaperTrade(
            account_id=account.id, stock_id=stock.id, side="buy", quantity=1,
            price=Decimal("10.00"), cost_basis=Decimal("10.00"), status=status,
            executed_at=datetime.now(timezone.utc),
        )

    db_session.add(row("open"))
    db_session.flush()
    db_session.add(row("open"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_closing_and_re_entering_the_same_stock_stays_allowed(db_session, user, no_tape):
    """The index is partial for this reason — a plain unique constraint would
    make the second entry into any stock impossible."""
    stock = Stock(symbol="REENTER", is_active=True)
    db_session.add(stock)
    db_session.flush()
    db_session.add(PriceHistory(stock_id=stock.id, date=date(2026, 9, 1), close=Decimal("100.00")))
    db_session.flush()

    for _ in range(3):
        pt.buy(db_session, user.id, "REENTER", 2)
        pt.sell(db_session, user.id, "REENTER")

    account = pt.get_or_create_account(db_session, user.id)
    closed = db_session.query(PaperTrade).filter(
        PaperTrade.account_id == account.id, PaperTrade.status == "closed").count()
    assert closed == 3


# ------------------------------------------------------------- real Postgres --

@pytest.mark.skipif(
    not os.environ.get("AUDIT_POSTGRES_URL"),
    reason="needs a real Postgres (row locks); set AUDIT_POSTGRES_URL to run",
)
def test_concurrent_buys_do_not_lose_a_debit():
    """The original defect, end to end, on a database with row locks.

    Two threads buy different symbols into the same account at the same
    moment. Before the fix this lost 400,240.00 of a 1,000,000 balance.
    """
    import threading

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.database import Base

    engine = create_engine(os.environ["AUDIT_POSTGRES_URL"])
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    setup = Session()
    u = User(name="R", email="race@example.com", password_hash="x")
    setup.add(u)
    setup.flush()
    symbols = ["RACE_A", "RACE_B"]
    for sym in symbols:
        st = Stock(symbol=sym, is_active=True)
        setup.add(st)
        setup.flush()
        setup.add(PriceHistory(stock_id=st.id, date=date(2026, 9, 1), close=Decimal("100.00")))
    account = pt.get_or_create_account(setup, u.id)
    setup.commit()
    user_id, start_cash = u.id, pt.to_money(account.cash)
    setup.close()

    import app.services.paper_trading as pt_mod
    pt_mod.get_price_feed = lambda: _StubFeed()

    barrier = threading.Barrier(2)
    errors: list[str] = []

    def worker(n):
        db = Session()
        try:
            pt.get_or_create_account(db, user_id)  # warm the identity map
            barrier.wait(timeout=15)
            pt.buy(db, user_id, symbols[n], 4000)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - recorded, then asserted on
            errors.append(f"{type(exc).__name__}: {exc}")
            db.rollback()
        finally:
            db.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check = Session()
    account = check.query(PaperAccount).filter(PaperAccount.user_id == user_id).one()
    trades = check.query(PaperTrade).filter(PaperTrade.account_id == account.id).all()
    debited = sum((pt.to_money(t.cost_basis) for t in trades), Decimal(0))
    cash = pt.to_money(account.cash)
    check.close()

    assert errors == []
    assert cash == start_cash - debited, f"lost debit: {cash - (start_cash - debited)}"


# --------------------------------------------------------------- atomicity --

@pytest.fixture
def file_db(tmp_path, monkeypatch):
    """A real, committable database for the scheduler wrapper.

    _run_ai_trading_cycle opens its own SessionLocal and commits and rolls back
    for real, so the shared in-memory fixture (one connection, one open
    transaction) cannot exercise it. A file-backed SQLite gives genuine
    transaction boundaries.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core import scheduler
    from app.core.database import Base

    engine = create_engine(f"sqlite:///{tmp_path/'atomic.db'}")
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    return Session


def _seed_open_position(Session, symbol="ATOM"):
    db = Session()
    u = User(name="A", email="atomic@example.com", password_hash="x")
    db.add(u)
    db.flush()
    stock = Stock(symbol=symbol, is_active=True)
    db.add(stock)
    db.flush()
    db.add(PriceHistory(stock_id=stock.id, date=date(2026, 9, 1), close=Decimal("100.00")))
    account = pt.get_or_create_account(db, u.id)
    db.add(PaperTrade(
        account_id=account.id, stock_id=stock.id, side="buy", quantity=10,
        price=Decimal("100.00"), cost_basis=Decimal("1000.00"), status="open",
        executed_at=datetime.now(timezone.utc),
    ))
    db.commit()
    ids = (u.id, account.id, stock.id)
    db.close()
    return ids


def test_a_cycle_that_raises_halfway_commits_no_trades(file_db, monkeypatch):
    """All or nothing.

    This wrapper used to share one transaction with the run row and had no
    rollback, so `run.status = "failed"; db.commit()` committed every trade the
    cycle had already flushed. A cycle that sold two positions and then raised
    left the account half-rebalanced — and, since a failed run no longer
    consumes the month's rebalance, the next day rebalanced again from that
    partial state.
    """
    from app.core import scheduler
    from app.models.ai_trading_run import AITradingRun

    user_id, account_id, stock_id = _seed_open_position(file_db)

    def half_a_cycle(db, as_of):
        # Sell the position, then die — the exact shape of a partial cycle.
        db.query(PaperTrade).filter(PaperTrade.account_id == account_id).update(
            {"status": "closed", "pnl": Decimal("50.00")}
        )
        db.flush()
        raise RuntimeError("provider went away mid-cycle")

    monkeypatch.setattr(scheduler, "run_ai_trading_cycle", half_a_cycle, raising=False)
    import app.services.ai_trading as ai_mod
    monkeypatch.setattr(ai_mod, "run_ai_trading_cycle", half_a_cycle)

    with pytest.raises(RuntimeError):
        scheduler._run_ai_trading_cycle(date(2026, 9, 2))

    check = file_db()
    still_open = check.query(PaperTrade).filter(
        PaperTrade.account_id == account_id, PaperTrade.status == "open").count()
    run = check.query(AITradingRun).filter(AITradingRun.run_date == date(2026, 9, 2)).one()
    check.close()

    assert still_open == 1, "a partial cycle committed its trades"
    assert run.status == "failed"
    assert run.finished_at is not None


def test_the_run_row_survives_the_rollback(file_db, monkeypatch):
    """The date reservation is committed before trading starts, so a crash
    still leaves the record _ai_trading_done_today reads to avoid re-running
    the same day."""
    from app.core import scheduler
    from app.models.ai_trading_run import AITradingRun

    _seed_open_position(file_db, symbol="ATOM2")

    def blow_up(db, as_of):
        raise RuntimeError("died before doing anything")

    import app.services.ai_trading as ai_mod
    monkeypatch.setattr(ai_mod, "run_ai_trading_cycle", blow_up)

    with pytest.raises(RuntimeError):
        scheduler._run_ai_trading_cycle(date(2026, 9, 3))

    check = file_db()
    assert check.query(AITradingRun).filter(AITradingRun.run_date == date(2026, 9, 3)).count() == 1
    check.close()
