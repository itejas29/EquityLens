"""Phase 24 — how much of V1's backtest excess is survivorship?

Pre-registration: docs/experiments/phase24_survivorship/PREREGISTRATION.md
(committed before the universes were built). Universes come from
phase24_prepare_survivorship_lab.py (universes.json in the same experiment
directory): per fold, A = survivors-only and B = survivorship-free, identically
ranked from NSE's own traded values.

Frozen V1 on the fixed engine, existing costs, NIFTY 50 benchmark. Both arms
share a fold's indicator cache: it holds per-stock snapshots only, and each
run restricts it to its own universe (backtest_scoring, audit #18).

Run against the lab only.
"""

import json
import logging
import sys
from collections import Counter
from dataclasses import replace
from datetime import date
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.core.backtest_config import DEFAULT_SLIPPAGE_PCT, DEFAULT_TRANSACTION_COST_PCT  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.experiment_lock import experiment_lock  # noqa: E402
from app.core.experiment_paths import experiment_dir  # noqa: E402
from app.core.strategy_params import StrategyParams  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.services.backtest import BacktestConfig, run_backtest  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p24")

OUT = experiment_dir("phase24_survivorship")
CAPITAL = 500_000
# Exactly Phase 23's v1_raw arm — the frozen production strategy.
FROZEN = dict(atr_stop_multiplier=4.0, use_support_stop=False, cash_buffer_pct=0.0,
              use_regime_filter=True, bull_exposure=1.0, bear_exposure=0.25,
              reentry_rule="immediate", ranking_engine="momentum")
V1 = replace(StrategyParams.for_appetite("moderate"), **FROZEN,
             sizing_method="equal_weight", trend_confirm_days=5,
             horizon_days=90, rebalance_frequency="monthly")
SENSITIVITY_SHARE = 0.05   # pre-registered: run the shared-sector sensitivity above this
SHARED_SECTOR = "Delisted (shared bucket)"


def _run(db, ids, te, tt, cache):
    r = run_backtest(db, BacktestConfig(
        start_date=te, end_date=tt, initial_capital=CAPITAL, risk_appetite="moderate",
        horizon_days=V1.horizon_days, rebalance_frequency=V1.rebalance_frequency,
        transaction_cost_pct=DEFAULT_TRANSACTION_COST_PCT, slippage_pct=DEFAULT_SLIPPAGE_PCT,
        params=V1, indicator_cache=cache, universe_stock_ids=ids))
    exits = Counter(t.exit_reason for t in r.trade_log)
    return r, {**r.metrics, "exit_reasons": dict(exits),
               "trades_in_bhavcopy_names": sum(1 for t in r.trade_log if t.symbol.startswith("DL:"))}


def _excess_stats(values: list[float]) -> dict:
    loo = [mean(values[:i] + values[i + 1:]) for i in range(len(values))]
    return {"mean_pp": round(mean(values), 2), "median_pp": round(median(values), 2),
            "folds_beating": sum(v > 0 for v in values), "folds": len(values),
            "leave_one_out_mean_min_pp": round(min(loo), 2)}


def main() -> None:
    plan = json.loads((OUT / "universes.json").read_text())
    db = SessionLocal()
    rec = {"experiment": "phase24_survivorship", "universes": {k: v for k, v in plan.items() if k != "folds"},
           "folds": []}
    for p in plan["folds"]:
        te, tt = date.fromisoformat(p["test"][0]), date.fromisoformat(p["test"][1])
        cache: dict = {}
        _, a = _run(db, p["A"], te, tt, cache)
        rb, b = _run(db, p["B"], te, tt, cache)
        rec["folds"].append({"fold": p["fold"], "test": p["test"], "benchmark": rb.benchmark_metrics,
                             "A": a, "B": b, "universe_sizes": {"A": len(p["A"]), "B": len(p["B"])},
                             "B_from_bhavcopy": p["B_from_bhavcopy"]})
        log.info("Fold %2d  NIFTY %7.2f%%  survivors %7.2f%%  survivorship-free %7.2f%%  (B: %d from bhavcopy, "
                 "%d delisted exits)", p["fold"], rb.benchmark_metrics.get("total_return_pct") or 0,
                 a.get("total_return_pct") or 0, b.get("total_return_pct") or 0, p["B_from_bhavcopy"],
                 b["exit_reasons"].get("delisted", 0))

    # Pre-registered sensitivity: no-longer-listed names have no sector, and
    # each got its own bucket. Above the threshold, re-run B with them sharing one.
    worst_share = max(f["B_from_bhavcopy"] / max(1, f["universe_sizes"]["B"]) for f in rec["folds"])
    rec["sensitivity"] = None
    if worst_share > SENSITIVITY_SHARE:
        log.info("bhavcopy names reach %.1f%% of a fold's B: running the shared-sector sensitivity", worst_share * 100)
        saved = dict(db.execute(text("SELECT id, sector FROM stocks WHERE symbol LIKE 'DL:%'")).all())
        db.execute(text("UPDATE stocks SET sector = :s WHERE symbol LIKE 'DL:%'"), {"s": SHARED_SECTOR})
        db.commit()
        try:
            sens = []
            for p in plan["folds"]:
                te, tt = date.fromisoformat(p["test"][0]), date.fromisoformat(p["test"][1])
                _, b = _run(db, p["B"], te, tt, {})
                sens.append({"fold": p["fold"], "B": b})
            rec["sensitivity"] = {"shared_sector": SHARED_SECTOR, "folds": sens}
        finally:
            for sid, sector in saved.items():
                db.query(Stock).filter(Stock.id == sid).update({"sector": sector})
            db.commit()

    F = rec["folds"]
    nifty = [f["benchmark"]["total_return_pct"] for f in F]
    a_ret = [f["A"]["total_return_pct"] for f in F]
    b_ret = [f["B"]["total_return_pct"] for f in F]
    gap = [a - b for a, b in zip(a_ret, b_ret)]
    b_excess = [b - n for b, n in zip(b_ret, nifty)]
    survives = (_excess_stats(b_excess)["median_pp"] > 0
                and _excess_stats(b_excess)["leave_one_out_mean_min_pp"] > 0)
    rec["summary"] = {
        "survivorship_gap_pp": {"mean": round(mean(gap), 2), "median": round(median(gap), 2),
                                "folds_where_survivors_did_better": sum(g > 0 for g in gap)},
        "survivors_only_vs_nifty50": _excess_stats([a - n for a, n in zip(a_ret, nifty)]),
        "survivorship_free_vs_nifty50": _excess_stats(b_excess),
        "delisted_exits_in_B": sum(f["B"]["exit_reasons"].get("delisted", 0) for f in F),
        "trades_in_bhavcopy_names_in_B": sum(f["B"]["trades_in_bhavcopy_names"] for f in F),
        "decision": ("SURVIVES — the backtest excess holds on a survivorship-free universe (a lower-bound correction)"
                     if survives else "DOES NOT SURVIVE — the backtest excess is attributable, at least in part, "
                     "to survivorship"),
    }
    if rec["sensitivity"]:
        s_ret = [f["B"]["total_return_pct"] for f in rec["sensitivity"]["folds"]]
        rec["summary"]["sensitivity_shared_sector_vs_nifty50"] = _excess_stats(
            [s - n for s, n in zip(s_ret, nifty)])
    (OUT / "results.json").write_text(json.dumps(rec, indent=2, default=str))
    print(json.dumps(rec["summary"], indent=2))
    print(f"\nWritten to {OUT / 'results.json'}")


if __name__ == "__main__":
    with experiment_lock("phase24_survivorship"):
        main()
