"""Phase 16 — falsification / stress test of the Phase 15 result.

The candidate is momentum ranking + equal-weight sizing + the frozen risk
layer. This phase tries to BREAK it. Nothing here is optimised; every arm is a
perturbation of the frozen candidate along exactly one axis, and the question
is whether the edge survives.

Axes (§1-§6): momentum definition, rebalance horizon, transaction cost,
slippage, sizing (equal vs inverse-vol only), universe size.

Held under an experiment lock (§10) so the production scheduler cannot mutate
the universe mid-run — which is precisely what corrupted the first Phase 15
attempt.
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
from app.core.strategy_params import StrategyParams  # noqa: E402
from app.services.backtest import BacktestConfig, run_backtest  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p16")

START, END, CAPITAL = date(2016, 10, 1), date(2026, 8, 1), 500_000
TRAIN, TEST, ROLL = 18, 6, 6
OUT = Path(__file__).resolve().parents[2] / "docs" / "experiments" / "phase16_robustness"

FROZEN = dict(atr_stop_multiplier=4.0, use_support_stop=False, cash_buffer_pct=0.0,
              use_regime_filter=True, bull_exposure=1.0, bear_exposure=0.25,
              reentry_rule="immediate", ranking_engine="momentum")
BASE = StrategyParams.for_appetite("moderate")
EQ = replace(BASE, **FROZEN, sizing_method="equal_weight")
IV = replace(BASE, **FROZEN, sizing_method="inverse_vol")

# (label, params, cost_multiplier, slippage_multiplier)
ARMS: list[tuple[str, StrategyParams, float, float]] = [
    # baseline (§5 — the only two sizing methods permitted)
    ("EQ_baseline",         EQ, 1.0, 1.0),
    ("IV_baseline",         IV, 1.0, 1.0),
    # §1 momentum definition neighbourhood
    ("EQ_mom_6m1m",         replace(EQ, momentum_long_days=126), 1.0, 1.0),
    ("EQ_mom_9m1m",         replace(EQ, momentum_long_days=189), 1.0, 1.0),
    ("EQ_mom_12m_noskip",   replace(EQ, momentum_skip_days=0),   1.0, 1.0),
    ("EQ_mom_12m_skip2m",   replace(EQ, momentum_skip_days=42),  1.0, 1.0),
    # §2 rebalance horizon
    ("EQ_quarterly",        replace(EQ, rebalance_frequency="quarterly"), 1.0, 1.0),
    ("EQ_hold180",          replace(EQ, horizon_days=180), 1.0, 1.0),
    # §3 cost stress
    ("EQ_cost_1.5x",        EQ, 1.5, 1.0),
    ("EQ_cost_2x",          EQ, 2.0, 1.0),
    ("IV_cost_2x",          IV, 2.0, 1.0),
    # §4 slippage stress
    ("EQ_slip_2x",          EQ, 1.0, 2.0),
    ("EQ_slip_3x",          EQ, 1.0, 3.0),
    ("EQ_cost2x_slip2x",    EQ, 2.0, 2.0),
    # §6 universe sensitivity
    ("EQ_top300",           replace(EQ, universe_top_n=300), 1.0, 1.0),
    ("IV_top300",           replace(IV, universe_top_n=300), 1.0, 1.0),
]


def go(db, params, cost_m, slip_m, s, e, cache):
    return run_backtest(db, BacktestConfig(
        start_date=s, end_date=e, initial_capital=CAPITAL, risk_appetite="moderate",
        horizon_days=params.horizon_days, rebalance_frequency=params.rebalance_frequency,
        transaction_cost_pct=DEFAULT_TRANSACTION_COST_PCT * cost_m,
        slippage_pct=DEFAULT_SLIPPAGE_PCT * slip_m,
        params=params, indicator_cache=cache))


def classify(r):
    if r is None:
        return "unknown"
    return ("strong bull" if r >= 12 else "bull" if r >= 3 else "sideways" if r > -3
            else "correction" if r > -12 else "bear")


def main() -> None:
    db, cache = SessionLocal(), {}
    rec = {"experiment": "phase16_robustness", "arms": [a[0] for a in ARMS], "folds": []}

    s, fold = START, 0
    while True:
        te = s + timedelta(days=int(TRAIN * 30.44))
        tt = te + timedelta(days=int(TEST * 30.44))
        if tt > END:
            break
        fold += 1
        entry = {"fold": fold, "test": [str(te), str(tt)], "arms": {}}
        for label, params, cm, sm in ARMS:
            r = go(db, params, cm, sm, te, tt, cache)
            entry["arms"][label] = r.metrics
            if "benchmark" not in entry:
                entry["benchmark"] = r.benchmark_metrics
                entry["regime"] = classify(r.benchmark_metrics.get("total_return_pct"))
        rec["folds"].append(entry)
        log.info("Fold %2d %-12s NIFTY %7.2f%%  EQ %7.2f%%  IV %7.2f%%  EQ@2xcost %7.2f%%",
                 fold, entry["regime"], entry["benchmark"].get("total_return_pct") or 0,
                 entry["arms"]["EQ_baseline"].get("total_return_pct") or 0,
                 entry["arms"]["IV_baseline"].get("total_return_pct") or 0,
                 entry["arms"]["EQ_cost_2x"].get("total_return_pct") or 0)
        s = s + timedelta(days=int(ROLL * 30.44))

    F = rec["folds"]
    bench = [f["benchmark"].get("total_return_pct") for f in F]

    def agg(label, key):
        vals = [f["arms"][label].get(key) for f in F]
        clean = [v for v in vals if v is not None]
        return round(mean(clean), 2) if clean else None

    print("\n" + "=" * 150)
    print("PHASE 16 — ROBUSTNESS / STRESS TEST (aggregate OOS across 16 folds)")
    print("=" * 150)
    print(f"  {'arm':<20}{'ret%':>8}{'Sharpe':>8}{'Sortino':>9}{'maxDD%':>9}{'mean ex':>9}{'med ex':>9}"
          f"{'up%':>8}{'down%':>8}{'depl%':>8}{'costs':>10}{'beat':>7}")
    for label, *_ in ARMS:
        rets = [f["arms"][label].get("total_return_pct") for f in F]
        ex = [(r or 0) - (b or 0) for r, b in zip(rets, bench)]
        wins = sum(1 for r, b in zip(rets, bench) if r is not None and b is not None and r > b)
        print(f"  {label:<20}{str(agg(label,'total_return_pct')):>8}{str(agg(label,'sharpe_ratio')):>8}"
              f"{str(agg(label,'sortino_ratio')):>9}{str(agg(label,'max_drawdown_pct')):>9}"
              f"{mean(ex):>9.2f}{median(ex):>9.2f}{str(agg(label,'upside_capture_pct')):>8}"
              f"{str(agg(label,'downside_capture_pct')):>8}{str(agg(label,'avg_capital_deployed_pct')):>8}"
              f"{str(agg(label,'total_transaction_costs')):>10}{f'{wins}/{len(F)}':>7}")
    print(f"  {'NIFTY50':<20}{round(mean([b for b in bench if b is not None]),2):>8}"
          f"{str(round(mean([f['benchmark'].get('sharpe_ratio') or 0 for f in F]),2)):>8}"
          f"{str(round(mean([f['benchmark'].get('sortino_ratio') or 0 for f in F]),2)):>9}"
          f"{str(round(mean([f['benchmark'].get('max_drawdown_pct') or 0 for f in F]),2)):>9}"
          f"{'—':>9}{'—':>9}{'—':>8}{'—':>8}{'100':>8}{'—':>10}{'—':>7}")

    print("\n" + "=" * 150)
    print("BY REGIME — EQ baseline vs IV baseline vs NIFTY  (§7)")
    print("=" * 150)
    buckets: dict[str, list] = {}
    for f in F:
        buckets.setdefault(f["regime"], []).append(f)
    print(f"  {'regime':<14}{'folds':>6}{'NIFTY%':>10}{'EQ%':>10}{'IV%':>10}{'EQ-NIFTY':>11}")
    for regime, fs in sorted(buckets.items()):
        def m(vals):
            c = [v for v in vals if v is not None]
            return round(mean(c), 2) if c else 0.0
        n = m([x["benchmark"].get("total_return_pct") for x in fs])
        e = m([x["arms"]["EQ_baseline"].get("total_return_pct") for x in fs])
        i = m([x["arms"]["IV_baseline"].get("total_return_pct") for x in fs])
        print(f"  {regime:<14}{len(fs):>6}{n:>10.2f}{e:>10.2f}{i:>10.2f}{e-n:>11.2f}")

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "results.json"
    n = 1
    while p.exists():
        n += 1
        p = OUT / f"results_{n}.json"
    p.write_text(json.dumps(rec, indent=2, default=str))
    print("=" * 150)
    print(f"written: {p}")
    db.close()


if __name__ == "__main__":
    with experiment_lock("phase16_robustness"):
        main()
