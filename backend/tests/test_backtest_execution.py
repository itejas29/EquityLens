"""Execution realism of the backtest engine: what each measurement check rests on.

Two of these are strict expected failures. They are not skipped tests and not
aspirations — they are the engine's KNOWN measurement defects, encoded so that:

- the suite stays green while the defect is documented,
- the verdict generator reads an xfail as that check FAILING, and
- the moment someone fixes the engine, `strict=True` turns the unexpected pass
  into a suite failure, forcing the marker off and the check to PASS.

Fixing either defect changes every backtest number the engine has produced, so
it is a research decision with a re-run attached, not a drive-by edit.
"""

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from app.core.backtest_config import DEFAULT_SLIPPAGE_PCT, DEFAULT_TRANSACTION_COST_PCT
from app.core.canonical import first_difference
from app.core.v1_strategy import V1
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.services import backtest as bt

START, END = date(2026, 1, 1), date(2026, 3, 31)
GAP_DAY = date(2026, 1, 20)


def _days(start=date(2024, 6, 3), end=END):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _add_stock(db, symbol, growth, *, gap_day=None, gap=1.0, after_cutoff=None, sector="Tech"):
    """A smooth uptrend with a small daily range, so ATR (and the 4-ATR stop) is
    tight and predictable. Optionally: a gap-down on `gap_day`, or a different
    path after a cutoff date (for the lookahead test)."""
    stock = Stock(symbol=symbol, is_active=True, sector=sector)
    db.add(stock)
    db.flush()
    close = 100.0
    for d in _days():
        prev = close
        g = growth
        if after_cutoff and d > after_cutoff[0]:
            g = after_cutoff[1]
        close = prev * (1 + g)
        open_ = prev
        if gap_day and d == gap_day:
            open_ = prev * gap
            close = open_ * 1.002
        high, low = max(open_, close) * 1.004, min(open_, close) * 0.996
        db.add(PriceHistory(stock_id=stock.id, date=d, open=Decimal(f"{open_:.2f}"),
                            high=Decimal(f"{high:.2f}"), low=Decimal(f"{low:.2f}"),
                            close=Decimal(f"{close:.2f}"), volume=500_000))
    db.flush()
    return stock


def _bench():
    days = _days()
    return pd.DataFrame({"date": days, "close": [20000 * (1.0005 ** i) for i in range(len(days))]})


def _run(db, monkeypatch, **overrides):
    bench = _bench()
    monkeypatch.setattr(bt, "fetch_price_history", lambda *a, **k: bench.copy())
    cfg = dict(start_date=START, end_date=END, initial_capital=1_000_000.0, risk_appetite="moderate",
               horizon_days=V1.horizon_days, rebalance_frequency=V1.rebalance_frequency, params=V1)
    cfg.update(overrides)
    return bt.run_backtest(db, bt.BacktestConfig(**cfg))


def _universe(db, **gapper):
    for i, (sym, g) in enumerate([("AAA", 0.0010), ("BBB", 0.0012), ("CCC", 0.0014), ("DDD", 0.0016)]):
        _add_stock(db, sym, g, sector=["Tech", "Banks"][i % 2])
    return _add_stock(db, "GAPPER", 0.0025, sector="Energy", **gapper)


# ------------------------------------------------------------------ costs --

def test_costs_and_slippage_are_charged_on_both_legs_exactly(db_session, monkeypatch):
    _universe(db_session)
    free = _run(db_session, monkeypatch, transaction_cost_pct=0.0, slippage_pct=0.0)
    paid = _run(db_session, monkeypatch, transaction_cost_pct=DEFAULT_TRANSACTION_COST_PCT,
                slippage_pct=DEFAULT_SLIPPAGE_PCT)

    assert free.trade_log and paid.trade_log, "no trades — nothing was charged"
    assert free.metrics["total_transaction_costs"] == 0
    assert paid.metrics["total_transaction_costs"] > 0

    # Per share, independent of quantity: entry = ref*(1+slip)*(1+cost/2),
    # exit = ref*(1-slip)*(1-cost/2). The zero-cost run's prices ARE the refs.
    free_by_key = {(t.symbol, t.entry_date): t for t in free.trade_log}
    matched = 0
    for t in paid.trade_log:
        ref = free_by_key.get((t.symbol, t.entry_date))
        if ref is None or ref.exit_date != t.exit_date:
            continue
        matched += 1
        want_entry = ref.entry_price * (1 + DEFAULT_SLIPPAGE_PCT) * (1 + DEFAULT_TRANSACTION_COST_PCT / 2)
        want_exit = ref.exit_price * (1 - DEFAULT_SLIPPAGE_PCT) * (1 - DEFAULT_TRANSACTION_COST_PCT / 2)
        assert abs(t.entry_price - want_entry) <= 0.011, (t.symbol, t.entry_price, want_entry)
        assert abs(t.exit_price - want_exit) <= 0.011, (t.symbol, t.exit_price, want_exit)
    assert matched, "no trade matched across the two runs, so the per-share charge was never checked"


# -------------------------------------------------------------- lookahead --

def test_scoring_and_entries_do_not_see_bars_after_the_rebalance_date(db_session, monkeypatch):
    """Two histories identical up to a cutoff and wildly different after it
    must produce identical signals and entries on every rebalance before it."""
    cutoff = date(2026, 2, 10)
    recorded: dict[str, dict] = {"a": {}, "b": {}}
    real = bt.compute_point_in_time_universe

    def recorder(bucket):
        def wrapped(*args, **kwargs):
            out = real(*args, **kwargs)
            as_of = kwargs.get("as_of", args[4] if len(args) > 4 else None)
            recorded[bucket][str(as_of)] = {str(k): v for k, v in out.items()}
            return out
        return wrapped

    # History A: everything keeps rising after the cutoff.
    for sym, g in [("AAA", 0.0010), ("BBB", 0.0012), ("CCC", 0.0014), ("DDD", 0.0016), ("EEE", 0.0025)]:
        _add_stock(db_session, sym, g)
    monkeypatch.setattr(bt, "compute_point_in_time_universe", recorder("a"))
    a = _run(db_session, monkeypatch)

    # History B: same rows up to the cutoff; after it, the leaders collapse.
    for ph in db_session.query(PriceHistory).filter(PriceHistory.date > cutoff).all():
        ph.close = ph.close * Decimal("0.5")
        ph.open = ph.open * Decimal("0.5")
        ph.high = ph.high * Decimal("0.5")
        ph.low = ph.low * Decimal("0.5")
    db_session.flush()
    monkeypatch.setattr(bt, "compute_point_in_time_universe", recorder("b"))
    b = _run(db_session, monkeypatch)

    before = [d for d in recorded["a"] if date.fromisoformat(d) <= cutoff]
    assert before, "no rebalance fell before the cutoff — the test checked nothing"
    for d in before:
        diff = first_difference(recorded["a"][d], recorded["b"][d])
        assert diff is None, f"signal on {d} changed when only bars AFTER {cutoff} changed:\n{diff.describe()}"

    entries_a = sorted((t.symbol, t.entry_date, t.entry_price) for t in a.trade_log if t.entry_date <= cutoff)
    entries_b = sorted((t.symbol, t.entry_date, t.entry_price) for t in b.trade_log if t.entry_date <= cutoff)
    assert entries_a == entries_b


# ------------------------------------------- known defects (strict xfail) --

@pytest.mark.xfail(strict=True, reason=(
    "KNOWN DEFECT: a stop is filled at the stop price even when the session "
    "opened below it (backtest.py: `if low <= pos.stop_loss: exit_price = "
    "pos.stop_loss`). A gap-down fills at or below the open. Overstates "
    "backtest returns. Fixing it changes every published backtest."
))
def test_a_gap_down_through_the_stop_fills_at_the_open_not_the_stop(db_session, monkeypatch):
    _universe(db_session, gap_day=GAP_DAY, gap=0.70)
    result = _run(db_session, monkeypatch)

    gapper = [t for t in result.trade_log if t.symbol == "GAPPER"]
    assert gapper, "GAPPER was never bought — the gap-down was never tested"
    stop = next((t for t in gapper if t.exit_reason == "stop"), None)
    assert stop is not None and stop.exit_date == GAP_DAY, [(t.exit_reason, t.exit_date) for t in gapper]

    gap_open = float(db_session.query(PriceHistory.open).join(Stock).filter(
        Stock.symbol == "GAPPER", PriceHistory.date == GAP_DAY).scalar())
    # No fill can be better than the open once the open is already through the stop.
    assert stop.exit_price <= gap_open, (
        f"stopped out at {stop.exit_price} on a day that opened at {gap_open}"
    )


@pytest.mark.xfail(strict=True, reason=(
    "KNOWN DEFECT: entries fill on the signal bar itself. On a rebalance day "
    "the snapshot is scored on bars up to and including that day's close, and "
    "the position is opened the same day at entry_high derived from that "
    "close. A signal computed from a close cannot be traded at that close; "
    "the live system publishes at 09:15 IST and fills next session. Direction "
    "of bias is not established: the timing is unexecutable but entry_high "
    "sits above the close, which is pessimistic on price."
))
def test_entries_fill_in_the_session_after_the_signal(db_session, monkeypatch):
    _universe(db_session)
    rebalances: list[date] = []
    real = bt.compute_point_in_time_universe

    def wrapped(*args, **kwargs):
        rebalances.append(kwargs.get("as_of", args[4] if len(args) > 4 else None))
        return real(*args, **kwargs)

    monkeypatch.setattr(bt, "compute_point_in_time_universe", wrapped)
    result = _run(db_session, monkeypatch)

    assert result.trade_log and rebalances
    signal_days = set(rebalances)
    same_bar = [(t.symbol, t.entry_date) for t in result.trade_log if t.entry_date in signal_days]
    assert not same_bar, f"entries filled on the signal bar itself: {same_bar[:5]}"
