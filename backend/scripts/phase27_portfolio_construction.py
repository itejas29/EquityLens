"""Phase 27 — positions and refill frequency for the AI paper account.

Pre-registration: docs/experiments/phase27_portfolio_construction/PREREGISTRATION.md
(committed before this script existed). Folds and each fold's universe B are
Phase 24's, unchanged. Every arm is frozen V1; only max_stocks, the sector cap
and the rebalance frequency differ.

Folds run in parallel worker processes. Each worker shares one indicator cache
across the arms of a fold (identical universe, so audit #18's condition holds)
and measures every arm against the same pinned NIFTY series.

Run against the lab only.
"""

import json
import logging
import math
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import date
from multiprocessing import get_context
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.experiment_lock import experiment_lock  # noqa: E402
from app.core.experiment_paths import experiment_dir  # noqa: E402

OUT = experiment_dir("phase27_portfolio_construction")
P24 = experiment_dir("phase24_survivorship")
CAPITAL = 500_000
REFERENCE = "N8_monthly"
# (positions, rebalance). Sector cap = 20% of positions rounded up — V1's 2 of 10.
ARMS = {f"N{n}_{freq}": (n, freq) for n in (8, 12, 15) for freq in ("monthly", "weekly")}
DRAWDOWN_TOLERANCE_PTS = 2.0
NO_COST_TOLERANCE_PP = 0.5
WORKERS = 3   # 8 GB machine: each worker holds one fold's price frames


def _params(n: int, freq: str):
    from app.core.v1_strategy import V1

    return replace(V1, max_stocks=n, max_stocks_per_sector=math.ceil(n * 0.2), rebalance_frequency=freq)


def _run_folds(folds: list[dict], bench) -> list[dict]:
    """One worker: every arm on each of its folds."""
    import app.services.backtest as backtest_module
    from app.core.backtest_config import DEFAULT_SLIPPAGE_PCT, DEFAULT_TRANSACTION_COST_PCT
    from app.core.database import SessionLocal
    from app.core.lab_guard import require_lab_database
    from app.services.backtest import BacktestConfig, run_backtest

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("p27")
    log.setLevel(logging.INFO)
    real = backtest_module.fetch_price_history

    def pinned(symbol, period="2y"):
        return bench.copy() if symbol == "^NSEI" else real(symbol, period=period)

    backtest_module.fetch_price_history = pinned
    db = SessionLocal()
    require_lab_database(db.get_bind().url)
    out = []
    for p in folds:
        te, tt = date.fromisoformat(p["test"][0]), date.fromisoformat(p["test"][1])
        cache: dict = {}
        fold = {"fold": p["fold"], "test": p["test"], "universe_size": len(p["B"]), "arms": {}}
        for arm, (n, freq) in ARMS.items():
            params = _params(n, freq)
            started = time.time()
            r = run_backtest(db, BacktestConfig(
                start_date=te, end_date=tt, initial_capital=CAPITAL, risk_appetite="moderate",
                horizon_days=params.horizon_days, rebalance_frequency=params.rebalance_frequency,
                transaction_cost_pct=DEFAULT_TRANSACTION_COST_PCT, slippage_pct=DEFAULT_SLIPPAGE_PCT,
                params=params, indicator_cache=cache, universe_stock_ids=p["B"]))
            fold["arms"][arm] = {**r.metrics, "exit_reasons": dict(Counter(t.exit_reason for t in r.trade_log))}
            fold["benchmark"] = r.benchmark_metrics
            log.info("fold %2d %-12s ret %7.2f  maxDD %7.2f  deployed %s  trades %s  (%.0fs)",
                     p["fold"], arm, r.metrics["total_return_pct"], r.metrics["max_drawdown_pct"],
                     r.metrics.get("avg_capital_deployed_pct"), r.metrics.get("num_trades"),
                     time.time() - started)
        out.append(fold)
    db.close()
    return out


def _stats(values: list[float]) -> dict:
    loo = [mean(values[:i] + values[i + 1:]) for i in range(len(values))]
    return {"mean": mean(values), "median": median(values), "folds_positive": sum(v > 0 for v in values),
            "folds": len(values), "leave_one_out_mean_min": min(loo)}


def _rounded(d: dict) -> dict:
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()}


def main() -> None:
    from app.core.universe_config import HISTORY_PERIOD
    from app.services.market_data import fetch_price_history

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("p27")
    started = time.time()
    plan = json.loads((P24 / "universes.json").read_text())
    folds = plan["folds"]
    bench = fetch_price_history("^NSEI", period=HISTORY_PERIOD)
    log.info("NIFTY pinned: %s → %s (%d sessions)", bench["date"].min(), bench["date"].max(), len(bench))

    # Round-robin, so each worker gets early and late folds alike.
    chunks = [folds[i::WORKERS] for i in range(WORKERS)]
    with ProcessPoolExecutor(max_workers=WORKERS, mp_context=get_context("spawn")) as pool:
        results = [f for part in pool.map(_run_folds, chunks, [bench] * WORKERS) for f in part]
    results.sort(key=lambda f: f["fold"])

    F = {f["fold"]: f for f in results}
    ks = sorted(F)

    def ret(arm, k):
        return F[k]["arms"][arm]["total_return_pct"]

    nifty = {k: F[k]["benchmark"]["total_return_pct"] for k in ks}
    ref_dd = mean(F[k]["arms"][REFERENCE]["max_drawdown_pct"] for k in ks)
    per_arm = {}
    for arm in ARMS:
        dd = mean(F[k]["arms"][arm]["max_drawdown_pct"] for k in ks)
        deployed = [F[k]["arms"][arm].get("avg_capital_deployed_pct") for k in ks]
        entry = {
            "vs_nifty": _stats([ret(arm, k) - nifty[k] for k in ks]),
            "mean_return_pct": mean(ret(arm, k) for k in ks),
            "mean_max_drawdown_pct": dd,
            "worst_max_drawdown_pct": min(F[k]["arms"][arm]["max_drawdown_pct"] for k in ks),
            "mean_capital_deployed_pct": mean(d for d in deployed if d is not None) if any(
                d is not None for d in deployed) else None,
            "mean_trades": mean(F[k]["arms"][arm].get("num_trades") or 0 for k in ks),
            "mean_costs": mean(F[k]["arms"][arm].get("total_transaction_costs") or 0 for k in ks),
        }
        if arm != REFERENCE:
            d = _stats([ret(arm, k) - ret(REFERENCE, k) for k in ks])
            # max_drawdown_pct is negative: "deeper" means more negative.
            dd_ok = dd >= ref_dd - DRAWDOWN_TOLERANCE_PTS
            better = d["median"] > 0 and d["mean"] > 0 and d["leave_one_out_mean_min"] > 0 and dd_ok
            no_cost = (not better and d["median"] >= -NO_COST_TOLERANCE_PP
                       and d["mean"] >= -NO_COST_TOLERANCE_PP and dd_ok)
            entry["vs_reference"] = d
            entry["drawdown_within_tolerance"] = dd_ok
            entry["class"] = "Better" if better else "No measured cost" if no_cost else "Worse"
        per_arm[arm] = entry

    better = [a for a in ARMS if per_arm[a].get("class") == "Better"]
    no_cost = [a for a in ARMS if per_arm[a].get("class") == "No measured cost"]
    if better:
        adopted, basis = max(better, key=lambda a: per_arm[a]["vs_nifty"]["median"]), "Better"
    elif no_cost:
        # Most positions first, then weekly over monthly — the registered preference order.
        adopted = max(no_cost, key=lambda a: (ARMS[a][0], ARMS[a][1] == "weekly"))
        basis = "No measured cost"
    else:
        adopted, basis = REFERENCE, "Reference kept"

    rec = {
        "experiment": "phase27_portfolio_construction", "arms": {a: list(v) for a, v in ARMS.items()},
        "reference": REFERENCE, "benchmark_series": [str(bench["date"].min()), str(bench["date"].max())],
        "folds": results,
        "summary": {
            "decision": {"adopted": adopted, "basis": basis, "better": better, "no_measured_cost": no_cost},
            "arms": {a: {k: (_rounded(v) if isinstance(v, dict) else round(v, 2) if isinstance(v, float) else v)
                         for k, v in e.items()} for a, e in per_arm.items()},
            "runtime_minutes": round((time.time() - started) / 60, 1),
        },
    }
    (OUT / "results.json").write_text(json.dumps(rec, indent=2, default=str))
    print(json.dumps(rec["summary"], indent=2, default=str))
    print(f"\nWritten to {OUT / 'results.json'}")


if __name__ == "__main__":
    with experiment_lock("phase27_portfolio_construction"):
        main()
