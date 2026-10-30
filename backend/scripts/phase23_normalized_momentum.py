"""Phase 23 — does NSE's normalized momentum ranking improve V1?

Pre-registered in docs/experiments/phase23_normalized_momentum/PREREGISTRATION.md,
committed before this was first run. Nothing below may be changed after seeing
results without saying so there.

THE QUESTION. V1 ranks by raw 12-month-minus-1-month return. NSE's own
Nifty200 Momentum 30 divides 6- and 12-month returns by one year of daily
volatility and averages the z-scores, so a steady riser outranks a jumpy one
with the same return (backtest_scoring.normalized_momentum_scores). Does
swapping ONLY the ranking — universe, entries, stops, regime filter, trend
gate, sizing, costs and execution frozen — improve V1 out of sample?

TWO ARMS, IDENTICAL BUT FOR THE RANKING
    v1_raw         ranking_engine="momentum"             (V1 as it runs)
    v1_normalized  ranking_engine="momentum_normalized"  (the challenger)
Each is run at the existing cost model (12bps round trip + 5bps slippage per
fill, as in Phase 20) and at the statutory floor (24bps + 5bps). The engine is
the fixed one: next-session entries, gap-aware stops (audit #19, #20), so the
v1_raw arm at default costs is also the re-run of V1's folds the audit asks
for — on this lab's data, not Phase 20's.

SAME FOLDS AND UNIVERSE AS PHASE 20. 2016-10 to 2026-08, 6-month test windows
rolled by 6 months (16 folds; the 18-month "training" span is only where each
window starts — nothing is fitted). Each fold trades the point-in-time top
500 by 20-day traded value, drawn from the >= 1,000-stock pool that
phase18_prepare_universe.py builds. Survivor-only, as every phase before it.

BENCHMARKS. NIFTY 50 price index from the engine, as in Phase 20. And, for
folds that lie entirely inside its listed history, MOM30IETF — an ETF on the
Nifty200 Momentum 30 index itself, the thing a person could buy instead.

Run against a non-production database only.
"""

import json
import logging
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.backtest_config import DEFAULT_SLIPPAGE_PCT, DEFAULT_TRANSACTION_COST_PCT  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.experiment_lock import experiment_lock  # noqa: E402
from app.core.experiment_paths import experiment_dir  # noqa: E402
from app.core.strategy_params import StrategyParams  # noqa: E402
from app.models.price_history import PriceHistory  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.services.backtest import BacktestConfig, run_backtest  # noqa: E402
from app.services.market_data import fetch_price_history  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p23")

START, END, CAPITAL = date(2016, 10, 1), date(2026, 8, 1), 500_000
TRAIN, TEST, ROLL = 18, 6, 6
POOL_REQUIRED, ARM_SIZE = 1000, 500
OUT = experiment_dir("phase23_normalized_momentum")

# Exactly Phase 20's frozen V1, which is the production strategy.
FROZEN = dict(atr_stop_multiplier=4.0, use_support_stop=False, cash_buffer_pct=0.0,
              use_regime_filter=True, bull_exposure=1.0, bear_exposure=0.25,
              reentry_rule="immediate", ranking_engine="momentum")
V1 = replace(StrategyParams.for_appetite("moderate"), **FROZEN,
             sizing_method="equal_weight", trend_confirm_days=5,
             horizon_days=90, rebalance_frequency="monthly")

ARMS = {
    "v1_raw": V1,
    "v1_normalized": replace(V1, ranking_engine="momentum_normalized"),
}
COSTS = {
    "default": (DEFAULT_TRANSACTION_COST_PCT, DEFAULT_SLIPPAGE_PCT),
    "statutory": (0.0024, DEFAULT_SLIPPAGE_PCT),
}
PRIMARY_COST = "default"

# Decision rule — see PREREGISTRATION.md. The challenger becomes a candidate
# for further testing (never straight to production) only if ALL hold, on the
# paired per-fold difference (normalized - raw) in total return at
# PRIMARY_COST, and on the challenger's own excess over NIFTY 50.
MIN_FOLDS_BETTER = 10            # of 16
MIN_MEDIAN_PAIRED_GAIN_PP = 0.0
MIN_LEAVE_ONE_OUT_MEAN_GAIN_PP = 0.0
MIN_CHALLENGER_MEDIAN_EXCESS_PP = 0.0


def _pit_top500(db, fold_start: date) -> list[int]:
    """Phase 20's construction, unchanged."""
    ids = [s.id for s in db.query(Stock).filter(Stock.is_active == True).all()]  # noqa: E712
    ranked = []
    for sid in ids:
        rows = (db.query(PriceHistory.close, PriceHistory.volume)
                .filter(PriceHistory.stock_id == sid, PriceHistory.date <= fold_start)
                .order_by(PriceHistory.date.desc()).limit(20).all())
        vals = [float(c) * float(v) for c, v in rows if c is not None and v is not None]
        if vals:
            ranked.append((sum(vals) / len(vals), sid))
    ranked.sort(reverse=True)
    return [sid for _, sid in ranked[:ARM_SIZE]]


def _etf_window_return(etf, start: date, end: date) -> float | None:
    """Buy-and-hold over [start, end] from the first close on or after start to
    the last close on or before end — only when the ETF traded through the
    whole window."""
    if etf is None or etf.empty or etf["date"].min() > start or etf["date"].max() < end:
        return None
    window = etf[(etf["date"] >= start) & (etf["date"] <= end)]
    if len(window) < 2:
        return None
    return round(float(window["close"].iloc[-1] / window["close"].iloc[0] - 1) * 100, 2)


def _paired(folds, metric="total_return_pct", cost=PRIMARY_COST) -> dict:
    diffs = []
    for f in folds:
        a = f["runs"][f"v1_raw|{cost}"].get(metric)
        b = f["runs"][f"v1_normalized|{cost}"].get(metric)
        if a is not None and b is not None:
            diffs.append(b - a)
    if len(diffs) < 2:
        return {"folds": len(diffs)}
    loo = [mean(diffs[:i] + diffs[i + 1:]) for i in range(len(diffs))]
    return {
        "folds": len(diffs),
        "mean_gain_pp": round(mean(diffs), 2),
        "median_gain_pp": round(median(diffs), 2),
        "folds_better": sum(d > 0 for d in diffs),
        "leave_one_out_mean_min_pp": round(min(loo), 2),
        "per_fold_gain_pp": [round(d, 2) for d in diffs],
    }


def _excess(folds, arm, cost=PRIMARY_COST) -> dict:
    ex = [f["runs"][f"{arm}|{cost}"]["total_return_pct"] - f["benchmark"]["total_return_pct"]
          for f in folds if f["runs"][f"{arm}|{cost}"].get("total_return_pct") is not None
          and f["benchmark"].get("total_return_pct") is not None]
    return {"folds": len(ex), "mean_pp": round(mean(ex), 2) if ex else None,
            "median_pp": round(median(ex), 2) if ex else None, "folds_beating": sum(e > 0 for e in ex)}


def main() -> None:
    db = SessionLocal()
    active = db.query(Stock).filter(Stock.is_active == True).count()  # noqa: E712
    if active < POOL_REQUIRED:
        raise SystemExit(
            f"\nPhase 23 needs >= {POOL_REQUIRED} active stocks (draws {ARM_SIZE} point-in-time); "
            f"the DB has {active}.\nRun scripts/phase18_prepare_universe.py against a non-production DB first.\n")
    log.info("pool: %d active stocks", active)
    try:
        etf = fetch_price_history("MOM30IETF", period="max")[["date", "close"]].dropna()
        log.info("MOM30IETF history from %s", etf["date"].min())
    except Exception as exc:  # the ETF comparison is secondary; its absence is recorded, not guessed
        log.warning("MOM30IETF unavailable: %s", exc)
        etf = None

    rec = {"experiment": "phase23_normalized_momentum", "pool": active,
           "arms": list(ARMS), "costs": {k: list(v) for k, v in COSTS.items()}, "folds": []}
    s, fold = START, 0
    while True:
        te = s + timedelta(days=int(TRAIN * 30.44))
        tt = te + timedelta(days=int(TEST * 30.44))
        if tt > END:
            break
        fold += 1
        universe = _pit_top500(db, te)
        # One cache per fold, shared by every run in it: it holds only the
        # parameter-independent indicator snapshot, and the ranking — the only
        # thing the arms disagree on — is computed after it.
        cache: dict = {}
        entry = {"fold": fold, "test": [str(te), str(tt)], "runs": {},
                 "mom30_etf_return_pct": _etf_window_return(etf, te, tt)}
        for arm, params in ARMS.items():
            for cost_name, (cost, slippage) in COSTS.items():
                r = run_backtest(db, BacktestConfig(
                    start_date=te, end_date=tt, initial_capital=CAPITAL, risk_appetite="moderate",
                    horizon_days=params.horizon_days, rebalance_frequency=params.rebalance_frequency,
                    transaction_cost_pct=cost, slippage_pct=slippage,
                    params=params, indicator_cache=cache, universe_stock_ids=universe))
                entry["runs"][f"{arm}|{cost_name}"] = r.metrics
                entry.setdefault("benchmark", r.benchmark_metrics)
        rec["folds"].append(entry)
        log.info("Fold %2d  NIFTY %7.2f%%  raw %7.2f%%  normalized %7.2f%%  ETF %s",
                 fold, entry["benchmark"].get("total_return_pct") or 0,
                 entry["runs"]["v1_raw|default"].get("total_return_pct") or 0,
                 entry["runs"]["v1_normalized|default"].get("total_return_pct") or 0,
                 entry["mom30_etf_return_pct"])
        s = s + timedelta(days=int(ROLL * 30.44))

    F = rec["folds"]
    paired = _paired(F)
    challenger = _excess(F, "v1_normalized")
    decision = (
        paired.get("folds_better", 0) >= MIN_FOLDS_BETTER
        and (paired.get("median_gain_pp") or 0) > MIN_MEDIAN_PAIRED_GAIN_PP
        and (paired.get("leave_one_out_mean_min_pp") or 0) > MIN_LEAVE_ONE_OUT_MEAN_GAIN_PP
        and (challenger.get("median_pp") or 0) > MIN_CHALLENGER_MEDIAN_EXCESS_PP
    )
    etf_folds = [f for f in F if f["mom30_etf_return_pct"] is not None]
    rec["summary"] = {
        "paired_gain_default_costs": paired,
        "paired_gain_statutory_costs": _paired(F, cost="statutory"),
        "excess_vs_nifty50": {f"{arm}|{c}": _excess(F, arm, c) for arm in ARMS for c in COSTS},
        "vs_mom30_etf": {
            "folds": len(etf_folds),
            **{arm: (round(mean(f["runs"][f"{arm}|{PRIMARY_COST}"]["total_return_pct"] - f["mom30_etf_return_pct"]
                                for f in etf_folds), 2) if etf_folds else None) for arm in ARMS},
        },
        "decision": "CANDIDATE — test further before any production change" if decision else "REJECTED — V1 unchanged",
        "rule": {"min_folds_better": MIN_FOLDS_BETTER, "median_paired_gain_gt": MIN_MEDIAN_PAIRED_GAIN_PP,
                 "leave_one_out_mean_gain_gt": MIN_LEAVE_ONE_OUT_MEAN_GAIN_PP,
                 "challenger_median_excess_gt": MIN_CHALLENGER_MEDIAN_EXCESS_PP},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(rec, indent=2, default=str))
    print(json.dumps(rec["summary"], indent=2))
    print(f"\nWritten to {OUT / 'results.json'}")


if __name__ == "__main__":
    with experiment_lock("phase23_normalized_momentum"):
        main()
