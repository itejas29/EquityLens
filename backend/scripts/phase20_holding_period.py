"""Phase 20 — does trading faster help? Live outcomes say the picks decay.

The signal to chase: the Track Record page, built from 160 measured forward
outcomes, shows v1's edge over NIFTY getting WORSE the longer a pick is held —
-0.25pp at 1 day, -1.27pp at 5, -1.26pp at 10 — with win rate falling 49% ->
44% -> 39%. If the picks are least bad on day one, a shorter holding period is
the obvious hypothesis, and it is the one the operator actually wants tested.

WHY EACH ARM PAIRS A HORIZON WITH A REBALANCE FREQUENCY

Holding period cannot be tested alone. Under monthly rebalancing a 5-day
horizon exits after a week and then sits in cash until the next month, so the
arm measures the gap, not the horizon, and loses by construction. Entry
frequency has to move with exit speed. _rebalance_dates gained "weekly" and
"daily" support for exactly this.

WHAT THIS CANNOT TEST

Not intraday. The database stores daily OHLC only, so a same-session entry and
exit has no price path to be evaluated against — the backtest would be inventing
the fills. The fastest honest arm here is daily rebalancing with a 1-day
horizon, i.e. close-to-close.

COSTS ARE THE POINT, NOT A FOOTNOTE

At DEFAULT_TRANSACTION_COST_PCT (12bps round-trip) plus DEFAULT_SLIPPAGE_PCT
(5bps per fill), one round trip costs ~22bps. Trading daily over ~250 sessions
burns roughly 55% of capital a year before any gross return. Faster arms are
therefore charged far more in total than slow ones, which is realistic and is
most of what this experiment is measuring. An arm that wins gross and loses net
has not found anything.

Same folds, same universe construction, same params as Phase 19 otherwise, so
the numbers stay comparable to everything already recorded.
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
from app.models.price_history import PriceHistory  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.services.backtest import BacktestConfig, run_backtest  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p20")

START, END, CAPITAL = date(2016, 10, 1), date(2026, 8, 1), 500_000
TRAIN, TEST, ROLL = 18, 6, 6
POOL_REQUIRED, ARM_SIZE = 1000, 500
OUT = Path(__file__).resolve().parents[2] / "docs" / "experiments" / "phase20_holding_period"

FROZEN = dict(atr_stop_multiplier=4.0, use_support_stop=False, cash_buffer_pct=0.0,
              use_regime_filter=True, bull_exposure=1.0, bear_exposure=0.25,
              reentry_rule="immediate", ranking_engine="momentum")
V1 = replace(StrategyParams.for_appetite("moderate"), **FROZEN,
             sizing_method="equal_weight", trend_confirm_days=5)

# (label, horizon_days, rebalance_frequency)
ARMS = [
    ("h90_monthly_LIVE", 90, "monthly"),   # what production runs today
    ("h30_monthly",      30, "monthly"),
    ("h20_weekly",       20, "weekly"),
    ("h10_weekly",       10, "weekly"),
    ("h5_weekly",         5, "weekly"),
    ("h5_daily",          5, "daily"),
    ("h1_daily",          1, "daily"),     # close-to-close, the fastest honest arm
]


def _pit_top500(db, fold_start: date) -> list[int]:
    """Point-in-time top 500 by 20-day traded value — the neutral construction
    from Phase 19, reused so results sit on the same basis as that study."""
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


def go(db, horizon, freq, stock_ids, s, e):
    params = replace(V1, horizon_days=horizon, rebalance_frequency=freq)
    return run_backtest(db, BacktestConfig(
        start_date=s, end_date=e, initial_capital=CAPITAL, risk_appetite="moderate",
        horizon_days=horizon, rebalance_frequency=freq,
        transaction_cost_pct=DEFAULT_TRANSACTION_COST_PCT, slippage_pct=DEFAULT_SLIPPAGE_PCT,
        # One fresh cache per run: scoring is cached on as_of alone, and these
        # arms differ in when they score. Sharing would leak one arm's dates
        # into another (the defect that voided Phase 18's first run).
        params=params, indicator_cache={}, universe_stock_ids=stock_ids))


def classify(r):
    if r is None:
        return "unknown"
    return ("strong bull" if r >= 12 else "bull" if r >= 3 else "sideways" if r > -3
            else "correction" if r > -12 else "bear")


def main() -> None:
    db = SessionLocal()
    active = db.query(Stock).filter(Stock.is_active == True).count()  # noqa: E712
    if active < POOL_REQUIRED:
        raise SystemExit(
            f"\nPhase 20 needs >= {POOL_REQUIRED} active stocks (draws {ARM_SIZE} "
            f"point-in-time); the DB has {active}.\nRun "
            f"scripts/phase18_prepare_universe.py against a non-production DB first.\n")
    log.info("pool: %d active stocks", active)

    rec = {"experiment": "phase20_holding_period", "pool": active,
           "arms": [a[0] for a in ARMS], "folds": []}

    s, fold = START, 0
    while True:
        te = s + timedelta(days=int(TRAIN * 30.44))
        tt = te + timedelta(days=int(TEST * 30.44))
        if tt > END:
            break
        fold += 1
        universe = _pit_top500(db, te)
        entry = {"fold": fold, "test": [str(te), str(tt)], "arms": {}}
        for label, horizon, freq in ARMS:
            r = go(db, horizon, freq, universe, te, tt)
            entry["arms"][label] = r.metrics
            if "benchmark" not in entry:
                entry["benchmark"] = r.benchmark_metrics
                entry["regime"] = classify(r.benchmark_metrics.get("total_return_pct"))
        rec["folds"].append(entry)
        log.info("Fold %2d %-12s NIFTY %7.2f%%  live %7.2f%%  h5_weekly %7.2f%%  h1_daily %7.2f%%",
                 fold, entry["regime"], entry["benchmark"].get("total_return_pct") or 0,
                 entry["arms"]["h90_monthly_LIVE"].get("total_return_pct") or 0,
                 entry["arms"]["h5_weekly"].get("total_return_pct") or 0,
                 entry["arms"]["h1_daily"].get("total_return_pct") or 0)
        s = s + timedelta(days=int(ROLL * 30.44))

    F = rec["folds"]
    bench = [f["benchmark"].get("total_return_pct") for f in F]
    bench_mean = mean([b for b in bench if b is not None])

    def agg(label, key):
        vals = [f["arms"][label].get(key) for f in F]
        clean = [v for v in vals if v is not None]
        return round(mean(clean), 2) if clean else None

    print("\n" + "=" * 124)
    print("PHASE 20 — HOLDING PERIOD (%d folds, neutral pit_top500 universe)" % len(F))
    print("=" * 124)
    print(f"  {'arm':<20}{'ret%':>8}{'Sharpe':>8}{'maxDD%':>9}{'trades':>8}{'costs':>11}"
          f"{'vs NIFTY':>10}{'beat':>8}")
    for label, _, _ in ARMS:
        rets = [f["arms"][label].get("total_return_pct") for f in F]
        wins = sum(1 for r, b in zip(rets, bench) if r is not None and b is not None and r > b)
        print(f"  {label:<20}{str(agg(label,'total_return_pct')):>8}{str(agg(label,'sharpe_ratio')):>8}"
              f"{str(agg(label,'max_drawdown_pct')):>9}{str(agg(label,'num_trades')):>8}"
              f"{str(agg(label,'total_transaction_costs')):>11}"
              f"{(agg(label,'total_return_pct') or 0) - bench_mean:>+10.2f}{f'{wins}/{len(F)}':>8}")
    print(f"  {'NIFTY50':<20}{round(bench_mean,2):>8}")

    live = agg("h90_monthly_LIVE", "total_return_pct") or 0
    print("\n  Change vs the live 90-day/monthly configuration:")
    for label, _, _ in ARMS[1:]:
        print(f"    {label:<20}{(agg(label,'total_return_pct') or 0) - live:>+8.2f} pp"
              f"   (costs {agg(label,'total_transaction_costs')} vs {agg('h90_monthly_LIVE','total_transaction_costs')})")
    print("\n  Faster arms pay far more in costs by construction. An arm only counts as a")
    print("  finding if it beats the live config AFTER that, and beats NIFTY too.")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(rec, indent=2))
    print(f"\nWritten to {OUT / 'results.json'}")


if __name__ == "__main__":
    with experiment_lock("phase20_holding_period"):
        main()
