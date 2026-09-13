"""One side of the determinism gate. Not a test module (pytest collects test_*.py).

    python tests/determinism_runner.py <snapshot.sqlite> <benchmark.json> <out.json>

Runs the frozen V1 strategy's backtest over a read-only SQLite snapshot and
writes every output the engine produces, canonically serialized, to <out.json>.
test_determinism.py runs this TWICE, in separate processes with different
PYTHONHASHSEED values, and requires the two files to be byte-identical.

Why separate processes, not two calls in one. Python randomizes str hashing per
process. Anything whose output order leaks from iterating a set or dict keyed by
symbol strings is identical within one process and different across two, so a
same-process double run cannot see exactly the nondeterminism most likely to
exist in a codebase that ranks by symbol. The `iteration_order_leak` mode injects
that bug on purpose so a test can prove the harness catches it through the real
backtest, rather than asserting it.

Why the benchmark is a file. run_backtest fetches ^NSEI from yfinance on every
call (backtest.py). Two real-data reruns are therefore NOT reruns on the same
snapshot unless the benchmark is pinned; here it is part of the snapshot and
its hash is part of the output.
"""

import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path

# Before any app import: Settings has no defaults for these and would raise.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-not-used-outside-tests")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BACKTEST_START = date(2025, 7, 1)
BACKTEST_END = date(2026, 6, 30)
CAPITAL = 500_000.0


def main(snapshot: Path, benchmark_path: Path, out_path: Path) -> None:
    leak = os.environ.get("DETERMINISM_MODE") == "iteration_order_leak"

    import pandas as pd
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.models  # noqa: F401  register every mapper
    from app.core.canonical import canonical_json_bytes, sha256_hex
    from app.core.v1_strategy import V1
    from app.models.price_history import PriceHistory
    from app.models.stock import Stock
    from app.services import backtest as bt

    # Read-only: a run that tried to write would change the snapshot the other
    # run reads. mode=ro turns that into an error instead.
    engine = create_engine(f"sqlite:///file:{snapshot}?mode=ro&uri=true")
    db = sessionmaker(bind=engine)()

    bench_rows = json.loads(benchmark_path.read_text())
    bench = pd.DataFrame({
        "date": [date.fromisoformat(r["date"]) for r in bench_rows],
        "close": [float(r["close"]) for r in bench_rows],
    })
    bt.fetch_price_history = lambda *a, **k: bench.copy()

    # Record every point-in-time snapshot the engine scores — the signal
    # records. Trades alone would miss a ranking that changed without changing
    # which names were bought.
    signals: dict[str, object] = {}
    real_score = bt.compute_point_in_time_universe

    def recording_score(*args, **kwargs):
        result = real_score(*args, **kwargs)
        as_of = kwargs.get("as_of", args[4] if len(args) > 4 else None)
        signals[str(as_of)] = {str(sid): snap for sid, snap in result.items()}
        if leak:
            # The injected bug: snapshot order derived from str hashing. The
            # engine iterates the snapshot to build candidates and stable-sorts
            # by score, so tied names are then chosen in hash order — which
            # differs per process. Test-only; never reachable from app code.
            result = dict(sorted(result.items(), key=lambda kv: hash(str(kv[0]))))
        return result

    bt.compute_point_in_time_universe = recording_score

    # Entry ORDER per rebalance. The trade log is ordered by exit, so the order
    # names entered in — which decides who gets cash when cash runs short —
    # would otherwise never reach any compared output. The first negative
    # control of this gate passed for exactly that reason.
    entries: list[list[int]] = []
    real_select = bt._select_new_entries

    def recording_select(*args, **kwargs):
        chosen = real_select(*args, **kwargs)
        entries.append([e["stock_id"] for e in chosen])
        return chosen

    bt._select_new_entries = recording_select

    result = bt.run_backtest(db, bt.BacktestConfig(
        start_date=BACKTEST_START, end_date=BACKTEST_END, initial_capital=CAPITAL,
        risk_appetite="moderate", horizon_days=V1.horizon_days,
        rebalance_frequency=V1.rebalance_frequency, params=V1,
    ))

    # The inputs this run actually consumed, so the comparison proves both runs
    # were fed the same snapshot rather than assuming it.
    stocks = sorted((s.symbol, bool(s.is_active), s.sector) for s in db.query(Stock).all())
    prices = sorted(
        (sym, str(d), str(o), str(h), str(lo), str(c), int(v or 0))
        for sym, d, o, h, lo, c, v in db.query(
            Stock.symbol, PriceHistory.date, PriceHistory.open, PriceHistory.high,
            PriceHistory.low, PriceHistory.close, PriceHistory.volume,
        ).join(Stock, Stock.id == PriceHistory.stock_id).all()
    )

    artifacts = {
        "snapshot": {
            "stocks_sha256": sha256_hex(stocks),
            "prices_sha256": sha256_hex(prices),
            "benchmark_sha256": hashlib.sha256(benchmark_path.read_bytes()).hexdigest(),
            "price_rows": len(prices),
        },
        "signals": signals,
        "entries": entries,
        "trade_log": result.trade_log,
        "equity_curve": result.equity_curve,
        "benchmark_equity_curve": result.benchmark_equity_curve,
        "metrics": result.metrics,
        "benchmark_metrics": result.benchmark_metrics,
    }
    out_path.write_bytes(canonical_json_bytes(artifacts))
    db.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
