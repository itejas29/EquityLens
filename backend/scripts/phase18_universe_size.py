"""Phase 18 — does a deeper universe (500 -> 1000 by liquidity) improve v1?

The question: the scored universe is capped at MAX_UNIVERSE_SIZE = 500, and
that cap is what currently binds (2,406 symbols in the NSE catalogue, exactly
500 active). Momentum is well documented to be stronger in smaller names, so
ranks 501-1000 might hold alpha the strategy never sees.

The reason to be suspicious of a good result: the backtest charges a flat
DEFAULT_TRANSACTION_COST_PCT (12bps round-trip) + DEFAULT_SLIPPAGE_PCT (5bps
per fill) to every trade, calibrated for the liquid names that make up today's
top 500. Stocks ranked 501-1000 trade on wider spreads, so a deeper universe
gets credited with extra return while being charged large-cap execution costs.
That flatters exactly the arm we are trying to evaluate.

So the cost-sensitivity arms are not decoration — they are the actual test.
`top1000` winning at 12bps proves nothing on its own; what matters is the
breakeven cost at which its edge over top500 disappears. If that breakeven sits
below what ranks 501-1000 realistically cost to trade, the edge is an artifact.

Everything else is the frozen live v1 (momentum + equal weight + 4-ATR stop +
regime filter + the Phase 17 5-day trend gate), perturbed on one axis only, on
the same folds/costs as Phase 16 and 17 so baselines stay comparable.

Held under the experiment lock so the production scheduler cannot rebuild the
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
from app.models.stock import Stock  # noqa: E402
from app.services.backtest import BacktestConfig, run_backtest  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p18")

# Identical to Phase 16/17 so baselines are directly comparable.
START, END, CAPITAL = date(2016, 10, 1), date(2026, 8, 1), 500_000
TRAIN, TEST, ROLL = 18, 6, 6
OUT = Path(__file__).resolve().parents[2] / "docs" / "experiments" / "phase18_universe_size"

# The deepest arm under test. The DB must actually hold at least this many
# active stocks or the deep arms silently collapse onto the shallow ones.
DEEPEST_ARM = 1000

FROZEN = dict(atr_stop_multiplier=4.0, use_support_stop=False, cash_buffer_pct=0.0,
              use_regime_filter=True, bull_exposure=1.0, bear_exposure=0.25,
              reentry_rule="immediate", ranking_engine="momentum")
BASE = StrategyParams.for_appetite("moderate")
# Live v1 as it stands after Phase 17 shipped — the gate is part of the baseline
# now, not a variable, so this measures universe depth in isolation.
V1 = replace(BASE, **FROZEN, sizing_method="equal_weight", trend_confirm_days=5)

# (label, params, transaction_cost_pct)
#
# CACHE CORRECTNESS — why arms are grouped by universe size below.
#
# backtest_scoring.compute_point_in_time_scores caches on `as_of` ALONE, with no
# reference to which stocks were in scope. Phase 17 could therefore share one
# cache across every arm safely, because all its arms scored the same 500-stock
# universe and differed only in a gate applied after scoring.
#
# That does not hold here: universe depth changes the scoring INPUT. Sharing one
# cache across depths means the first arm to touch a rebalance date populates it
# and every later arm silently reuses that arm's universe. The first run of this
# experiment did exactly that and returned top300/500/750/1000 byte-identical on
# every metric in all 16 folds — while the cost arms, whose costs apply at trade
# execution downstream of scoring, did differ. That asymmetry is the fingerprint.
#
# So: one cache per distinct universe_top_n. The three 1000-name cost arms share
# a cache legitimately — same universe, cost applied after scoring.
ARMS: list[tuple[str, StrategyParams, float]] = [
    ("V1_top500",            replace(V1, universe_top_n=500),  DEFAULT_TRANSACTION_COST_PCT),
    ("V1_top750",            replace(V1, universe_top_n=750),  DEFAULT_TRANSACTION_COST_PCT),
    ("V1_top1000",           replace(V1, universe_top_n=1000), DEFAULT_TRANSACTION_COST_PCT),
    # Same deep universe, progressively realistic execution costs. 25bps and
    # 40bps round-trip bracket what a retail-sized order in a ranks-500-1000
    # name plausibly pays once spread is included.
    ("V1_top1000_cost25bps", replace(V1, universe_top_n=1000), 0.0025),
    ("V1_top1000_cost40bps", replace(V1, universe_top_n=1000), 0.0040),
]


def go(db, params, cost, s, e, cache):
    return run_backtest(db, BacktestConfig(
        start_date=s, end_date=e, initial_capital=CAPITAL, risk_appetite="moderate",
        horizon_days=params.horizon_days, rebalance_frequency=params.rebalance_frequency,
        transaction_cost_pct=cost,
        slippage_pct=DEFAULT_SLIPPAGE_PCT,
        params=params, indicator_cache=cache))


def classify(r):
    if r is None:
        return "unknown"
    return ("strong bull" if r >= 12 else "bull" if r >= 3 else "sideways" if r > -3
            else "correction" if r > -12 else "bear")


def check_universe_depth(db) -> int:
    """Refuse to run if the DB cannot actually populate the deep arms.

    universe_top_n only NARROWS an existing set (backtest.py ranks the active
    stocks by traded value and slices). With 500 active stocks, top750 and
    top1000 both silently resolve to the same 500 names as top500 — the arms
    would come back near-identical and the honest reading of that output is
    "no data", not "depth doesn't help". Failing loudly here is the difference
    between an inconclusive run and a wrong conclusion.
    """
    active = db.query(Stock).filter(Stock.is_active == True).count()  # noqa: E712
    if active < DEEPEST_ARM:
        raise SystemExit(
            f"\nPhase 18 needs >= {DEEPEST_ARM} active stocks ingested; the DB has {active}.\n"
            f"The deep arms would collapse onto V1_top500 and the run would look\n"
            f"like 'depth adds nothing' when it actually tested nothing.\n\n"
            f"Build the expanded universe first (raise MAX_UNIVERSE_SIZE in\n"
            f"app/core/universe_config.py, then run scripts/build_universe.py),\n"
            f"against a NON-PRODUCTION database.\n")
    return active


def main() -> None:
    db = SessionLocal()
    active = check_universe_depth(db)
    log.info("universe depth OK — %d active stocks ingested", active)

    # One cache per distinct universe depth — see the note above ARMS.
    caches: dict[int, dict] = {p.universe_top_n: {} for _, p, _ in ARMS}
    log.info("allocated %d scoring caches (one per universe depth: %s)",
             len(caches), sorted(caches))

    rec = {"experiment": "phase18_universe_size", "active_stocks": active,
           "arms": [a[0] for a in ARMS], "folds": []}

    s, fold = START, 0
    while True:
        te = s + timedelta(days=int(TRAIN * 30.44))
        tt = te + timedelta(days=int(TEST * 30.44))
        if tt > END:
            break
        fold += 1
        entry = {"fold": fold, "test": [str(te), str(tt)], "arms": {}}
        for label, params, cost in ARMS:
            r = go(db, params, cost, te, tt, caches[params.universe_top_n])
            entry["arms"][label] = r.metrics
            if "benchmark" not in entry:
                entry["benchmark"] = r.benchmark_metrics
                entry["regime"] = classify(r.benchmark_metrics.get("total_return_pct"))
        rec["folds"].append(entry)
        log.info("Fold %2d %-12s NIFTY %7.2f%%  t500 %7.2f%%  t1000 %7.2f%%  t1000@40bps %7.2f%%",
                 fold, entry["regime"], entry["benchmark"].get("total_return_pct") or 0,
                 entry["arms"]["V1_top500"].get("total_return_pct") or 0,
                 entry["arms"]["V1_top1000"].get("total_return_pct") or 0,
                 entry["arms"]["V1_top1000_cost40bps"].get("total_return_pct") or 0)
        s = s + timedelta(days=int(ROLL * 30.44))

    F = rec["folds"]
    bench = [f["benchmark"].get("total_return_pct") for f in F]

    def agg(label, key):
        vals = [f["arms"][label].get(key) for f in F]
        clean = [v for v in vals if v is not None]
        return round(mean(clean), 2) if clean else None

    print("\n" + "=" * 132)
    print("PHASE 18 — UNIVERSE DEPTH (aggregate OOS across %d folds, %d stocks ingested)"
          % (len(F), active))
    print("=" * 132)
    print(f"  {'arm':<24}{'ret%':>8}{'Sharpe':>8}{'Sortino':>9}{'maxDD%':>9}{'win%':>8}"
          f"{'mean ex':>9}{'med ex':>9}{'depl%':>8}{'trades':>8}{'beat':>8}")
    for label, _, _ in ARMS:
        rets = [f["arms"][label].get("total_return_pct") for f in F]
        ex = [(r or 0) - (b or 0) for r, b in zip(rets, bench)]
        wins = sum(1 for r, b in zip(rets, bench) if r is not None and b is not None and r > b)
        print(f"  {label:<24}{str(agg(label,'total_return_pct')):>8}{str(agg(label,'sharpe_ratio')):>8}"
              f"{str(agg(label,'sortino_ratio')):>9}{str(agg(label,'max_drawdown_pct')):>9}"
              f"{str(agg(label,'win_rate_pct')):>8}{mean(ex):>9.2f}{median(ex):>9.2f}"
              f"{str(agg(label,'avg_capital_deployed_pct')):>8}{str(agg(label,'num_trades')):>8}"
              f"{f'{wins}/{len(F)}':>8}")
    print(f"  {'NIFTY50':<24}{round(mean([b for b in bench if b is not None]),2):>8}")

    # Degeneracy check. Different universe depths selecting from different
    # candidate pools cannot produce identical returns in every fold; if they
    # do, the depths were not really varied (the first run of this experiment
    # returned exactly that, from a scoring cache shared across depths). Flag it
    # rather than let a reader take the table at face value.
    depth_arms = ["V1_top500", "V1_top750", "V1_top1000"]
    per_fold = [tuple(round(f["arms"][a].get("total_return_pct") or 0, 6) for a in depth_arms)
                for f in F]
    if all(len(set(row)) == 1 for row in per_fold):
        print("\n  *** SUSPECT: every depth arm returned identical results in all folds.")
        print("      Depth did not actually vary — do not read this as 'depth adds nothing'.")
        print("      Check that each depth got its own scoring cache.")

    # The decision line. Depth is only worth shipping if it still wins once the
    # deeper names are charged what they actually cost to trade.
    base = agg("V1_top500", "total_return_pct") or 0
    print("\n  Edge over V1_top500 (mean OOS return, percentage points):")
    for label in ("V1_top750", "V1_top1000", "V1_top1000_cost25bps", "V1_top1000_cost40bps"):
        print(f"    {label:<24}{(agg(label, 'total_return_pct') or 0) - base:>+8.2f}")
    print("\n  Ship only if the edge survives the 25-40bps arms — at 12bps the deep\n"
          "  universe is being charged large-cap costs for small-cap names.")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(rec, indent=2))
    print(f"\nWritten to {OUT / 'results.json'}")


if __name__ == "__main__":
    with experiment_lock("phase18_universe_size"):
        main()
