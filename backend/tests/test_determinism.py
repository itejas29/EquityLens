"""Determinism gate: the same snapshot must produce byte-identical outputs.

Not `final_value == final_value`. That passes while signals rank in a different
order, a different name fills the last slot, or an intermediate fill moves and
nets out. Every artifact the engine produces is compared: point-in-time signal
records, the per-rebalance entry order, the trade ledger, both equity curves,
and both metric sets — plus the fingerprint of the inputs, so "same snapshot" is
proven rather than assumed.

The two runs are separate processes with DIFFERENT hash seeds. See
determinism_runner.py for why a same-process double run is the weak version of
this test, and test_the_gate_catches_an_injected_iteration_order_leak for the
proof that it catches that class through the real backtest.

The fixture is deliberately untidy. Twice in this audit a fixture cleaner than
production made a check pass vacuously (a positional split landing on a date
boundary; identical momentum making a filter look like a no-op), and this gate's
own first negative control passed vacuously too. So: ragged per-stock drift and
volatility, four IDENTICAL names so the ranking has to break a real tie, a
delisted name, a stock with a missing week, a symbol with '&', and a benchmark
drawdown deep enough to flip the 200-day regime filter. The test also asserts
the run actually did those things — a determinism check over a backtest that
never traded would prove nothing.
"""

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.canonical import first_difference
from app.core.database import Base
from app.models.price_history import PriceHistory
from app.models.stock import Stock

BACKEND = Path(__file__).resolve().parents[1]
RUNNER = BACKEND / "tests" / "determinism_runner.py"

# Chosen, not arbitrary. The first pair used here (0 and 12345) happened to hash
# the tied names into the SAME relative order, so an injected tie-order leak
# never fired and the negative control passed vacuously. 0 and 3 order the four
# tied stock ids completely differently; test_the_seeds_actually_disagree_on_the_tie
# checks that on the fixture's real ids, so a Python hash change cannot quietly
# turn this back into a coin flip.
SEED_ONE, SEED_TWO = "0", "3"
QUADS = ("QUADA", "QUADB", "QUADC", "QUADD")


def _business_days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _money(x: float) -> Decimal:
    return Decimal(f"{x:.2f}")


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("determinism")
    db_path, bench_path = root / "snapshot.sqlite", root / "benchmark.json"

    days = _business_days(date(2024, 5, 1), date(2026, 6, 30))
    rng = np.random.default_rng(20260913)

    # Benchmark: steady rise, then a ~20% slide through the regime MA, then
    # recovery — so bear exposure and regime trims are exercised.
    bench, level = [], 20000.0
    for d in days:
        drift = -0.0022 if date(2025, 9, 15) <= d <= date(2026, 1, 15) else 0.0006
        level *= float(np.exp(drift + 0.008 * rng.standard_normal()))
        bench.append({"date": d.isoformat(), "close": round(level, 2)})
    bench_path.write_text(json.dumps(bench))

    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()

    sectors = ["Tech", "Banks", "Energy", "Pharma"]
    symbols = ["ALPHA", "BRAVO", "CHARLIE", "DELTA", "ECHO", "FOXTROT", "GOLF", "HOTEL",
               "INDIA", "JULIET", "KILO", "LIMA", "M&M", "NOVEMBER"]
    params = [(sym, float(rng.uniform(-0.0006, 0.0014)), float(rng.uniform(0.010, 0.035))) for sym in symbols]
    # Four identical names: same drift, volatility and shocks -> same momentum,
    # a genuine four-way tie the engine has to break. Four rather than two
    # because two seeds order a pair identically half the time; a four-way tie
    # coincides 1 time in 24. Drift high enough that they rank into the book —
    # a tie between names that never get bought tests nothing, and the first
    # version of this fixture did exactly that.
    params += [(q, 0.0022, 0.016) for q in QUADS]

    def write_series(sym, mu, sigma, sector, active=True, last=None, gap=None, shocks=None):
        stock = Stock(symbol=sym, is_active=active, sector=sector)
        db.add(stock)
        db.flush()
        close = 100.0
        for i, d in enumerate(days):
            if last and d > last:
                break
            if gap and gap[0] <= d <= gap[1]:
                continue
            z = shocks[i] if shocks is not None else rng.standard_normal()
            prev = close
            close = prev * float(np.exp(mu + sigma * z))
            open_ = prev * float(np.exp(0.3 * sigma * rng.standard_normal()))
            span = abs(float(rng.standard_normal())) * sigma
            db.add(PriceHistory(
                stock_id=stock.id, date=d,
                open=_money(open_), high=_money(max(open_, close) * (1 + span)),
                low=_money(min(open_, close) * (1 - span)), close=_money(close),
                volume=int(rng.integers(50_000, 5_000_000)),
            ))

    shared_shocks = rng.standard_normal(len(days))
    for n, (sym, mu, sigma) in enumerate(params):
        write_series(
            sym, mu, sigma, sectors[n % len(sectors)],
            gap=(date(2025, 11, 3), date(2025, 11, 10)) if sym == "DELTA" else None,
            shocks=shared_shocks if sym in QUADS else None,
        )
    write_series("GONEX", 0.0015, 0.02, "Tech", active=False, last=date(2025, 12, 15))
    db.commit()
    db.close()
    engine.dispose()
    return db_path, bench_path


def _run(snapshot: tuple[Path, Path], out: Path, seed: str, mode: str | None = None) -> dict:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    env.pop("DETERMINISM_MODE", None)
    if mode:
        env["DETERMINISM_MODE"] = mode
    proc = subprocess.run(
        [sys.executable, str(RUNNER), str(snapshot[0]), str(snapshot[1]), str(out)],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, f"runner failed (seed {seed}):\n{proc.stderr[-4000:]}"
    return json.loads(out.read_bytes())


def test_same_snapshot_produces_identical_outputs(snapshot, tmp_path):
    one_path, two_path = tmp_path / "run_one.json", tmp_path / "run_two.json"
    one = _run(snapshot, one_path, SEED_ONE)
    two = _run(snapshot, two_path, SEED_TWO)

    # Guard against a vacuous pass: the run must have exercised the engine.
    trades = one["trade_log"]
    assert len(one["signals"]) >= 10, "fewer than 10 rebalances were scored"
    assert len(trades) >= 10, f"only {len(trades)} trades — too little activity to test determinism"
    reasons = {t["exit_reason"] for t in trades}
    assert "regime" in reasons, f"regime filter never trimmed a position: {reasons}"
    assert set(QUADS) & {t["symbol"] for t in trades}, "the tied names were never bought — the tie never mattered"
    assert any(one["entries"]), "no rebalance selected anything"
    assert one["snapshot"] == two["snapshot"], "the two runs did not read the same inputs"

    if one_path.read_bytes() != two_path.read_bytes():
        diff = first_difference(one, two)
        pytest.fail(
            "the same snapshot produced different outputs in two processes "
            f"(PYTHONHASHSEED {SEED_ONE} vs {SEED_TWO})\n{diff.describe() if diff else 'bytes differ'}"
        )


def _quad_ids(snapshot) -> list[str]:
    engine = create_engine(f"sqlite:///{snapshot[0]}")
    with engine.connect() as conn:
        rows = conn.execute(Stock.__table__.select().where(Stock.symbol.in_(QUADS))).fetchall()
    engine.dispose()
    return sorted(str(r.id) for r in rows)


def test_the_seeds_actually_disagree_on_the_tie(snapshot):
    """Precondition for the leak test: the two seeds must order the tied ids
    differently, or an order leak could not show up and its test would be a
    coin flip that happened to land."""
    ids = _quad_ids(snapshot)
    assert len(ids) == 4
    orders = []
    for seed in (SEED_ONE, SEED_TWO):
        out = subprocess.run(
            [sys.executable, "-c", f"print(sorted({ids!r}, key=hash))"],
            env={**os.environ, "PYTHONHASHSEED": seed}, capture_output=True, text=True, check=True,
        ).stdout.strip()
        orders.append(out)
    assert orders[0] != orders[1], f"seeds {SEED_ONE} and {SEED_TWO} order {ids} identically: {orders[0]}"


def test_the_gate_catches_an_injected_iteration_order_leak(snapshot, tmp_path):
    """The proof, through the real backtest. The runner's leak mode feeds the
    engine a snapshot ordered by str hash; ties are then broken in a
    per-process order. The gate must see it."""
    one = _run(snapshot, tmp_path / "leak_one.json", SEED_ONE, "iteration_order_leak")
    two = _run(snapshot, tmp_path / "leak_two.json", SEED_TWO, "iteration_order_leak")
    diff = first_difference(one, two)
    assert diff is not None, (
        "an injected hash-order leak produced identical outputs under different seeds — "
        "the gate is blind to the class of bug it exists to catch"
    )
    # Signals are recorded before the leak reorders them, so they must match;
    # the leak has to surface in what the engine DID with the order.
    assert diff.artifact in {"entries", "trade_log", "equity_curve", "metrics"}, diff.describe()
    print("\n" + diff.describe())
