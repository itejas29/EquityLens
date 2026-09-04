"""Phase 19 — is v1's measured edge a property of the strategy, or of the
particular universe it was measured on?

WHY THIS EXISTS

Phase 17 measured live v1 at 16.76% mean OOS return (Sharpe 1.23) against
NIFTY's 6.22%, on production's 500-stock universe. Phase 18 measured the SAME
strategy, same 16 folds, same benchmark, at 4.48% (Sharpe 0.07) — the only
difference being how the 500 names were chosen (production's current-liquidity
membership vs a point-in-time ranking inside a fresh 1000-stock ingest).

A ~12pp swing from universe construction alone means the headline number may
describe a universe rather than a strategy. Everything downstream — whether to
widen the universe, whether the live account's results mean anything, whether
this edge is worth anything to anyone — rests on which reading is right.

WHAT EACH ARM ISOLATES

Every arm runs identical v1 params on identical folds. Only membership differs.

  current_top500   Top 500 by liquidity measured over the FULL history, i.e.
                   today's most liquid names applied retroactively to 2016.
                   This is what production does, and it is the optimistic case:
                   being liquid today correlates with having done well since
                   2016, so the membership itself encodes hindsight.

  pit_top500       Top 500 by liquidity as of each fold's start. Removes the
                   "liquid today" hindsight; keeps a liquidity criterion.

  pit_top500_60d   Same, 60-day measurement window instead of 20. If the edge
                   moves materially on a measurement-window choice nobody has
                   reason to care about, it is not robust.

  random500_s{1,2,3}  Random 500 of the 1000, three seeds. The sharpest test:
                   if the edge survives arbitrary membership it belongs to the
                   strategy; if it only appears under liquidity-ranked
                   membership it belongs to the selection.

  bottom500        The LEAST liquid 500 of the 1000. Momentum is documented to
                   be stronger in smaller names, so this should be strong if
                   the alpha is real momentum — and weak if the strategy is
                   really riding large-cap drift.

WHAT THIS CANNOT FIX

None of these arms removes survivorship bias. Every universe here is drawn
from names that exist and trade TODAY; the NSE catalogue has no delisted
constituents and yfinance exposes no historical membership. So even the
point-in-time arms are optimistic in an absolute sense, and no number this
script produces should be quoted as an achievable return. What it can settle
is the RELATIVE question: how much of the measured edge is membership.

Runs against a NON-PRODUCTION 1000-stock database (see
phase18_prepare_universe.py).
"""

import json
import logging
import random
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, median, pstdev

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.backtest_config import DEFAULT_SLIPPAGE_PCT, DEFAULT_TRANSACTION_COST_PCT  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.experiment_lock import experiment_lock  # noqa: E402
from app.core.strategy_params import StrategyParams  # noqa: E402
from app.models.price_history import PriceHistory  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.services.backtest import BacktestConfig, run_backtest  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p19")

# Identical to Phase 16/17/18 so every number stays comparable.
START, END, CAPITAL = date(2016, 10, 1), date(2026, 8, 1), 500_000
TRAIN, TEST, ROLL = 18, 6, 6
UNIVERSE_SIZE = 1000
ARM_SIZE = 500
OUT = Path(__file__).resolve().parents[2] / "docs" / "experiments" / "phase19_universe_robustness"

FROZEN = dict(atr_stop_multiplier=4.0, use_support_stop=False, cash_buffer_pct=0.0,
              use_regime_filter=True, bull_exposure=1.0, bear_exposure=0.25,
              reentry_rule="immediate", ranking_engine="momentum")
V1 = replace(StrategyParams.for_appetite("moderate"), **FROZEN,
             sizing_method="equal_weight", trend_confirm_days=5)


def _traded_value(db, stock_ids: list[int], upto: date | None, window: int) -> dict[int, float]:
    """Mean daily traded value over the last `window` bars up to `upto`.

    Same definition the universe builder screens on (close x volume), so these
    memberships are constructed the way production constructs its own.
    """
    out: dict[int, float] = {}
    for sid in stock_ids:
        q = db.query(PriceHistory.close, PriceHistory.volume).filter(PriceHistory.stock_id == sid)
        if upto is not None:
            q = q.filter(PriceHistory.date <= upto)
        rows = q.order_by(PriceHistory.date.desc()).limit(window).all()
        vals = [float(c) * float(v) for c, v in rows if c is not None and v is not None]
        if vals:
            out[sid] = sum(vals) / len(vals)
    return out


def build_universes(db, fold_start: date) -> dict[str, list[int]]:
    """Membership per arm. Rebuilt per fold so the point-in-time arms are
    genuinely point-in-time rather than ranked once and reused."""
    all_ids = [s.id for s in db.query(Stock).filter(Stock.is_active == True).all()]  # noqa: E712

    full = _traded_value(db, all_ids, upto=None, window=20)
    pit20 = _traded_value(db, all_ids, upto=fold_start, window=20)
    pit60 = _traded_value(db, all_ids, upto=fold_start, window=60)

    def top(d, n, reverse=True):
        return [sid for sid, _ in sorted(d.items(), key=lambda kv: kv[1], reverse=reverse)[:n]]

    universes = {
        "current_top500": top(full, ARM_SIZE),
        "pit_top500": top(pit20, ARM_SIZE),
        "pit_top500_60d": top(pit60, ARM_SIZE),
        "bottom500": top(pit20, ARM_SIZE, reverse=False),
    }
    for seed in (1, 2, 3):
        rng = random.Random(seed)
        universes[f"random500_s{seed}"] = rng.sample(all_ids, min(ARM_SIZE, len(all_ids)))
    return universes


ARM_ORDER = ["current_top500", "pit_top500", "pit_top500_60d",
             "random500_s1", "random500_s2", "random500_s3", "bottom500"]


def go(db, stock_ids, s, e, cache):
    return run_backtest(db, BacktestConfig(
        start_date=s, end_date=e, initial_capital=CAPITAL, risk_appetite="moderate",
        horizon_days=V1.horizon_days, rebalance_frequency=V1.rebalance_frequency,
        transaction_cost_pct=DEFAULT_TRANSACTION_COST_PCT, slippage_pct=DEFAULT_SLIPPAGE_PCT,
        params=V1, indicator_cache=cache, universe_stock_ids=stock_ids))


def classify(r):
    if r is None:
        return "unknown"
    return ("strong bull" if r >= 12 else "bull" if r >= 3 else "sideways" if r > -3
            else "correction" if r > -12 else "bear")


def main() -> None:
    db = SessionLocal()
    active = db.query(Stock).filter(Stock.is_active == True).count()  # noqa: E712
    if active < UNIVERSE_SIZE:
        raise SystemExit(
            f"\nPhase 19 needs >= {UNIVERSE_SIZE} active stocks; the DB has {active}.\n"
            f"Every arm draws {ARM_SIZE} names from that pool — with a smaller pool the\n"
            f"arms overlap heavily and the comparison measures nothing.\n"
            f"Run scripts/phase18_prepare_universe.py against a non-production DB first.\n")
    log.info("pool: %d active stocks; each arm draws %d", active, ARM_SIZE)

    rec = {"experiment": "phase19_universe_robustness", "pool": active,
           "arm_size": ARM_SIZE, "arms": ARM_ORDER, "folds": []}

    s, fold = START, 0
    while True:
        te = s + timedelta(days=int(TRAIN * 30.44))
        tt = te + timedelta(days=int(TEST * 30.44))
        if tt > END:
            break
        fold += 1

        universes = build_universes(db, te)
        overlap = len(set(universes["current_top500"]) & set(universes["pit_top500"]))
        log.info("Fold %2d universes built — current/pit overlap %d/%d", fold, overlap, ARM_SIZE)

        entry = {"fold": fold, "test": [str(te), str(tt)],
                 "current_pit_overlap": overlap, "arms": {}}
        for label in ARM_ORDER:
            # One cache per arm: membership differs, and the scoring cache is
            # keyed by date alone (see BacktestConfig.indicator_cache).
            r = go(db, universes[label], te, tt, {})
            entry["arms"][label] = r.metrics
            if "benchmark" not in entry:
                entry["benchmark"] = r.benchmark_metrics
                entry["regime"] = classify(r.benchmark_metrics.get("total_return_pct"))
        rec["folds"].append(entry)
        log.info("Fold %2d %-12s NIFTY %7.2f%%  current %7.2f%%  pit %7.2f%%  rand1 %7.2f%%",
                 fold, entry["regime"], entry["benchmark"].get("total_return_pct") or 0,
                 entry["arms"]["current_top500"].get("total_return_pct") or 0,
                 entry["arms"]["pit_top500"].get("total_return_pct") or 0,
                 entry["arms"]["random500_s1"].get("total_return_pct") or 0)
        s = s + timedelta(days=int(ROLL * 30.44))

    F = rec["folds"]
    bench = [f["benchmark"].get("total_return_pct") for f in F]
    bench_mean = mean([b for b in bench if b is not None])

    def agg(label, key):
        vals = [f["arms"][label].get(key) for f in F]
        clean = [v for v in vals if v is not None]
        return round(mean(clean), 2) if clean else None

    print("\n" + "=" * 120)
    print("PHASE 19 — UNIVERSE CONSTRUCTION ROBUSTNESS (%d folds, pool %d, arms of %d)"
          % (len(F), rec["pool"], ARM_SIZE))
    print("=" * 120)
    print(f"  {'universe construction':<24}{'ret%':>8}{'Sharpe':>8}{'Sortino':>9}{'maxDD%':>9}"
          f"{'vs NIFTY':>10}{'beat':>8}")
    for label in ARM_ORDER:
        rets = [f["arms"][label].get("total_return_pct") for f in F]
        wins = sum(1 for r, b in zip(rets, bench) if r is not None and b is not None and r > b)
        print(f"  {label:<24}{str(agg(label,'total_return_pct')):>8}{str(agg(label,'sharpe_ratio')):>8}"
              f"{str(agg(label,'sortino_ratio')):>9}{str(agg(label,'max_drawdown_pct')):>9}"
              f"{(agg(label,'total_return_pct') or 0) - bench_mean:>+10.2f}{f'{wins}/{len(F)}':>8}")
    print(f"  {'NIFTY50':<24}{round(bench_mean,2):>8}")

    # The verdict. If membership choice moves the result more than the strategy
    # beats its benchmark, the headline number is describing a universe.
    arm_means = [agg(l, "total_return_pct") or 0 for l in ARM_ORDER]
    spread = max(arm_means) - min(arm_means)
    edge = mean(arm_means) - bench_mean
    rand = [agg(f"random500_s{i}", "total_return_pct") or 0 for i in (1, 2, 3)]

    print("\n  Spread across constructions : %.2f pp (%.2f worst -> %.2f best)"
          % (spread, min(arm_means), max(arm_means)))
    print("  Mean edge over NIFTY        : %+.2f pp" % edge)
    print("  Random-membership arms      : %s (mean %+.2f vs NIFTY, sd %.2f)"
          % (", ".join("%.2f" % r for r in rand), mean(rand) - bench_mean, pstdev(rand)))
    print("\n  Read: if the spread dwarfs the edge, the measured edge is mostly membership.")
    print("  Random arms beating NIFTY is the strongest evidence the alpha is real —")
    print("  it would mean the strategy works on membership nobody selected for it.")
    print("\n  CAVEAT: no arm removes survivorship — all draw from names liquid TODAY.")
    print("  These figures compare constructions; none is an achievable return.")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(rec, indent=2))
    print(f"\nWritten to {OUT / 'results.json'}")


if __name__ == "__main__":
    with experiment_lock("phase19_universe_robustness"):
        main()
