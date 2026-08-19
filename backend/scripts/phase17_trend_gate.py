"""Phase 17 — does a recent-trend entry gate improve the frozen v1 candidate?

The gap being tested: 12m-1m momentum is a whole-year statistic. A stock that
ran early in the lookback and has since rolled over can still rank top-8, and
the monthly re-rank takes up to a month to drop it. Observed live on
2026-08-19, where 4 of 8 published picks were down over the prior 3 sessions
(CPPLUS -9.8%, ATHERENERG -4.0%, CUPID -3.7%, RPTECH -1.2%).

The candidate fix is an ENTRY GATE, not a ranking change: refuse to OPEN a new
position in a name whose trailing N-day return is negative. Phase 14 measured
the technical composite as inverted at short horizons (5-day decile
monotonicity -0.918), so technicals must not be blended into the score — but
gating is a different mechanism from ranking and has never been tested.

Every arm is the frozen Phase 15/16 candidate (momentum + equal-weight + the
frozen risk layer) perturbed along exactly one axis: the gate window. Same
folds, same costs, same universe as Phase 16, so the baseline row here should
reproduce EQ_baseline there.

Held under the experiment lock so the production scheduler cannot mutate the
universe mid-run.
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
log = logging.getLogger("p17")

# Identical to Phase 16 so the baseline is directly comparable.
START, END, CAPITAL = date(2016, 10, 1), date(2026, 8, 1), 500_000
TRAIN, TEST, ROLL = 18, 6, 6
OUT = Path(__file__).resolve().parents[2] / "docs" / "experiments" / "phase17_trend_gate"

FROZEN = dict(atr_stop_multiplier=4.0, use_support_stop=False, cash_buffer_pct=0.0,
              use_regime_filter=True, bull_exposure=1.0, bear_exposure=0.25,
              reentry_rule="immediate", ranking_engine="momentum")
BASE = StrategyParams.for_appetite("moderate")
EQ = replace(BASE, **FROZEN, sizing_method="equal_weight")

ARMS: list[tuple[str, StrategyParams]] = [
    ("EQ_baseline",        EQ),
    ("EQ_trend3d",         replace(EQ, trend_confirm_days=3)),
    ("EQ_trend5d",         replace(EQ, trend_confirm_days=5)),
    ("EQ_trend10d",        replace(EQ, trend_confirm_days=10)),
    ("EQ_trend20d",        replace(EQ, trend_confirm_days=20)),
    # Tolerate a mild pullback rather than requiring strictly non-negative —
    # separates "don't chase a falling knife" from "demand recent strength".
    ("EQ_trend5d_tol2pct", replace(EQ, trend_confirm_days=5, trend_confirm_min_return=-0.02)),
]


def go(db, params, s, e, cache):
    return run_backtest(db, BacktestConfig(
        start_date=s, end_date=e, initial_capital=CAPITAL, risk_appetite="moderate",
        horizon_days=params.horizon_days, rebalance_frequency=params.rebalance_frequency,
        transaction_cost_pct=DEFAULT_TRANSACTION_COST_PCT,
        slippage_pct=DEFAULT_SLIPPAGE_PCT,
        params=params, indicator_cache=cache))


def classify(r):
    if r is None:
        return "unknown"
    return ("strong bull" if r >= 12 else "bull" if r >= 3 else "sideways" if r > -3
            else "correction" if r > -12 else "bear")


def main() -> None:
    db, cache = SessionLocal(), {}
    rec = {"experiment": "phase17_trend_gate", "arms": [a[0] for a in ARMS], "folds": []}

    s, fold = START, 0
    while True:
        te = s + timedelta(days=int(TRAIN * 30.44))
        tt = te + timedelta(days=int(TEST * 30.44))
        if tt > END:
            break
        fold += 1
        entry = {"fold": fold, "test": [str(te), str(tt)], "arms": {}}
        for label, params in ARMS:
            r = go(db, params, te, tt, cache)
            entry["arms"][label] = r.metrics
            if "benchmark" not in entry:
                entry["benchmark"] = r.benchmark_metrics
                entry["regime"] = classify(r.benchmark_metrics.get("total_return_pct"))
        rec["folds"].append(entry)
        log.info("Fold %2d %-12s NIFTY %7.2f%%  base %7.2f%%  t5d %7.2f%%  t20d %7.2f%%",
                 fold, entry["regime"], entry["benchmark"].get("total_return_pct") or 0,
                 entry["arms"]["EQ_baseline"].get("total_return_pct") or 0,
                 entry["arms"]["EQ_trend5d"].get("total_return_pct") or 0,
                 entry["arms"]["EQ_trend20d"].get("total_return_pct") or 0)
        s = s + timedelta(days=int(ROLL * 30.44))

    F = rec["folds"]
    bench = [f["benchmark"].get("total_return_pct") for f in F]

    def agg(label, key):
        vals = [f["arms"][label].get(key) for f in F]
        clean = [v for v in vals if v is not None]
        return round(mean(clean), 2) if clean else None

    print("\n" + "=" * 132)
    print("PHASE 17 — RECENT-TREND ENTRY GATE (aggregate OOS across %d folds)" % len(F))
    print("=" * 132)
    print(f"  {'arm':<22}{'ret%':>8}{'Sharpe':>8}{'Sortino':>9}{'maxDD%':>9}{'win%':>8}"
          f"{'mean ex':>9}{'med ex':>9}{'depl%':>8}{'trades':>8}{'beat':>8}")
    for label, _ in ARMS:
        rets = [f["arms"][label].get("total_return_pct") for f in F]
        ex = [(r or 0) - (b or 0) for r, b in zip(rets, bench)]
        wins = sum(1 for r, b in zip(rets, bench) if r is not None and b is not None and r > b)
        print(f"  {label:<22}{str(agg(label,'total_return_pct')):>8}{str(agg(label,'sharpe_ratio')):>8}"
              f"{str(agg(label,'sortino_ratio')):>9}{str(agg(label,'max_drawdown_pct')):>9}"
              f"{str(agg(label,'win_rate_pct')):>8}{mean(ex):>9.2f}{median(ex):>9.2f}"
              f"{str(agg(label,'avg_capital_deployed_pct')):>8}{str(agg(label,'num_trades')):>8}"
              f"{f'{wins}/{len(F)}':>8}")
    print(f"  {'NIFTY50':<22}{round(mean([b for b in bench if b is not None]),2):>8}")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(rec, indent=2))
    print(f"\nWritten to {OUT / 'results.json'}")


if __name__ == "__main__":
    with experiment_lock("phase17_trend_gate"):
        main()
