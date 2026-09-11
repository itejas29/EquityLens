"""Phase 21 — how much of v1's measured return is survivorship?

WHY THIS EXISTS

Phase 19's docstring states the limitation and then closes the door on it:

    "None of these arms removes survivorship bias. Every universe here is
     drawn from names that exist and trade TODAY; the NSE catalogue has no
     delisted constituents and yfinance exposes no historical membership."

The first sentence is right. The second is only PARTLY right, and the September
2026 audit found the difference. The production database holds **67 inactive
stocks with 112,385 bars spanning 2016-08-16 to 2026-08-28** — names that were
real, tradeable universe members during the backtest window and then stopped
being members. `run_backtest` filters them out with `Stock.is_active == True`,
so every phase run so far has excluded a tenth of the stored price history.

That does not make a fully survivorship-free backtest possible: a company that
delisted before ever entering this database is still invisible, and always will
be. What it makes possible is a MEASUREMENT of the part we can see, which is
strictly better than the current position of not knowing the sign or the size.

WHAT THE TWO ARMS ISOLATE

Identical v1 params, identical folds, identical benchmark. Only membership
differs.

  survivors_only   Stock.is_active == True — exactly what every published
                   phase ran. The optimistic case.

  with_delisted    Every stock with stored history, active or not. A name that
                   stopped trading simply stops having bars, and the delisting
                   exit added in the same audit closes any position in it after
                   STALE_POSITION_EXIT_DAYS at the last known close.

WHAT THE RESULT MEANS, AND WHAT IT DOES NOT

The gap is a LOWER BOUND on survivorship bias, for two reasons pointing the
same way:

  * only names that reached this database are included, and
  * the delisting exit fills at the last traded price rather than at zero,
    because assuming a wipeout would be inventing a number the data does not
    support. A stock that stops trading has usually not stopped falling.

So a measured gap of X means "at least X". The count of `delisted` exits in
each fold's trade log says how much of the arm rests on that fill assumption,
and is reported alongside.

RUNNING IT

Needs a database with the inactive stocks retained — production has them; a
freshly prepared research DB may not. Not to be run against production during
market hours: it is CPU-bound for hours and holds a connection throughout.

    python scripts/phase21_survivorship.py
"""

import json
import logging
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.backtest_config import DEFAULT_SLIPPAGE_PCT, DEFAULT_TRANSACTION_COST_PCT  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.experiment_lock import experiment_lock  # noqa: E402
from app.core.strategy_params import StrategyParams  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.services.backtest import BacktestConfig, run_backtest  # noqa: E402
from app.core.experiment_paths import experiment_dir  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p21")

# Identical to Phases 16-19 so every number stays comparable.
START, END, CAPITAL = date(2016, 10, 1), date(2026, 8, 1), 500_000
TRAIN, TEST, ROLL = 18, 6, 6
OUT = experiment_dir("phase21_survivorship")

FROZEN = dict(atr_stop_multiplier=4.0, use_support_stop=False, cash_buffer_pct=0.0,
              use_regime_filter=True, bull_exposure=1.0, bear_exposure=0.25,
              reentry_rule="immediate", ranking_engine="momentum")
V1 = replace(StrategyParams.for_appetite("moderate"), **FROZEN,
             sizing_method="equal_weight", trend_confirm_days=5)

ARMS = ("survivors_only", "with_delisted")


def go(db, s, e, include_inactive):
    return run_backtest(db, BacktestConfig(
        start_date=s, end_date=e, initial_capital=CAPITAL, risk_appetite="moderate",
        horizon_days=V1.horizon_days, rebalance_frequency=V1.rebalance_frequency,
        transaction_cost_pct=DEFAULT_TRANSACTION_COST_PCT, slippage_pct=DEFAULT_SLIPPAGE_PCT,
        # One cache per arm, never shared: the two arms have different
        # membership, and the scoring cache is keyed by date alone. Sharing it
        # would silently give the second arm the first arm's universe — the
        # exact mistake that voided the first Phase 18 run.
        params=V1, indicator_cache={}, include_inactive=include_inactive))


def classify(r):
    if r is None:
        return "unknown"
    return ("strong bull" if r >= 12 else "bull" if r >= 3 else "sideways" if r > -3
            else "correction" if r > -12 else "bear")


def main() -> None:
    db = SessionLocal()
    active = db.query(Stock).filter(Stock.is_active == True).count()  # noqa: E712
    inactive = db.query(Stock).filter(Stock.is_active == False).count()  # noqa: E712
    if inactive == 0:
        raise SystemExit(
            "\nPhase 21 needs a database that has RETAINED delisted stocks, and this\n"
            "one has none. With zero inactive names both arms are identical and the\n"
            "comparison measures nothing. Production held 67 as of 2026-09-10.\n")
    log.info("universe: %d active, %d inactive — the inactive set is what this measures", active, inactive)

    rec = {"experiment": "phase21_survivorship", "active": active, "inactive": inactive,
           "arms": list(ARMS), "folds": []}

    s, fold = START, 0
    while True:
        te = s + timedelta(days=int(TRAIN * 30.44))
        tt = te + timedelta(days=int(TEST * 30.44))
        if tt > END:
            break
        fold += 1

        entry = {"fold": fold, "test": [str(te), str(tt)], "arms": {}}
        for label, include in zip(ARMS, (False, True)):
            r = go(db, te, tt, include)
            delisted_exits = sum(1 for t in r.trade_log if t.exit_reason == "delisted")
            entry["arms"][label] = {
                **r.metrics,
                # How much of this arm rests on the last-known-close fill.
                "delisted_exits": delisted_exits,
            }
            if "benchmark" not in entry:
                entry["benchmark"] = r.benchmark_metrics
                entry["regime"] = classify(r.benchmark_metrics.get("total_return_pct"))

        sv = entry["arms"]["survivors_only"].get("total_return_pct") or 0
        wd = entry["arms"]["with_delisted"].get("total_return_pct") or 0
        entry["survivorship_gap_pct"] = round(sv - wd, 2)
        rec["folds"].append(entry)
        log.info(
            "Fold %2d %-12s NIFTY %7.2f%%  survivors %7.2f%%  with_delisted %7.2f%%  "
            "GAP %6.2fpp  (delisted exits %d)",
            fold, entry["regime"], entry["benchmark"].get("total_return_pct") or 0,
            sv, wd, entry["survivorship_gap_pct"],
            entry["arms"]["with_delisted"]["delisted_exits"],
        )
        s = s + timedelta(days=int(ROLL * 30.44))

    F = rec["folds"]

    def agg(label, key):
        vals = [f["arms"][label].get(key) for f in F]
        clean = [v for v in vals if v is not None]
        return round(mean(clean), 2) if clean else None

    gaps = [f["survivorship_gap_pct"] for f in F]
    rec["summary"] = {
        "folds": len(F),
        "benchmark_mean_return_pct": round(mean(
            [f["benchmark"].get("total_return_pct") for f in F
             if f["benchmark"].get("total_return_pct") is not None]), 2),
        **{f"{a}_mean_return_pct": agg(a, "total_return_pct") for a in ARMS},
        **{f"{a}_mean_sharpe": agg(a, "sharpe_ratio") for a in ARMS},
        **{f"{a}_mean_max_dd_pct": agg(a, "max_drawdown_pct") for a in ARMS},
        "mean_survivorship_gap_pp": round(mean(gaps), 2),
        "folds_where_survivors_did_better": sum(1 for g in gaps if g > 0),
        "total_delisted_exits": sum(f["arms"]["with_delisted"]["delisted_exits"] for f in F),
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(rec, indent=2, default=str))
    log.info("wrote %s", OUT / "results.json")

    S = rec["summary"]
    log.info("=" * 78)
    log.info("MEAN over %d folds — NIFTY %.2f%%", S["folds"], S["benchmark_mean_return_pct"])
    for a in ARMS:
        log.info("  %-15s return %7.2f%%  Sharpe %6.2f  maxDD %7.2f%%",
                 a, S[f"{a}_mean_return_pct"], S[f"{a}_mean_sharpe"], S[f"{a}_mean_max_dd_pct"])
    log.info("  SURVIVORSHIP GAP %.2fpp (survivors better in %d/%d folds, %d delisted exits)",
             S["mean_survivorship_gap_pp"], S["folds_where_survivors_did_better"],
             S["folds"], S["total_delisted_exits"])
    log.info("  This is a LOWER BOUND — see the module docstring for both reasons.")
    db.close()


if __name__ == "__main__":
    with experiment_lock("phase21_survivorship"):
        main()
