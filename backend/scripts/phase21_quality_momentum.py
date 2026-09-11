"""Phase 21 — does a quality filter stabilise momentum?

THE HYPOTHESIS, AND WHY IT IS NOT TESTED THE WAY IT WAS FIRST PROPOSED

The idea is sound: filter out low-quality "junk" rallies and the momentum edge
should steady, with shallower drawdowns. The original specification reached for
ROE and EPS growth. It cannot use them, for two independent reasons measured
against production on 2026-09-10:

    fundamentals as_of_date : 2026-08-14 .. 2026-09-01   (18 days, 5 dates)
    price history spans     : 2016-08-16 .. 2026-09-10   (10.1 years)

    active universe : 500
    have roe        :  70  (14.0%)
    have both       :  62  (12.4%)

Using an ROE measured in September 2026 to make a rebalance decision in 2017 is
a decade of look-ahead. And it is the dangerous kind: it would not fail loudly,
it would produce a flattering curve, because the companies carrying high ROE
today are disproportionately the ones that compounded over that decade. The
project already knows this — README states that fundamentals are excluded from
backtesting for exactly this reason, and the September audit removed the same
leak from the ML feature set.

So quality is measured from PRICE, which is genuinely point-in-time here:

  lowvol   trailing annualised volatility, keep the calmer half
  lowdd    trailing max drawdown, keep the shallower half
  both     must pass on each

Both are legitimate junk proxies — a stock rallying on thin, violent, deeply
drawn-down price action is what the hypothesis wants to exclude — and both are
computable at every rebalance date from data that existed on that date.

WHAT WOULD COUNT AS A FINDING

An arm only counts if it beats the CONTROL after costs and beats NIFTY. The
control is the live V1 configuration, so this asks the honest question: does
adding the filter improve on what production already runs? Phases 18-20 have
each answered "no" to a variant of that, and the protocol is deliberately
unchanged so this answer is comparable to those.

ONE SHARED CACHE, ON PURPOSE

The gate is applied at SELECTION time, not during scoring, so every arm
produces an identical point-in-time snapshot for a given date. That makes one
shared indicator cache correct — and turns a day-long sweep into a few hours.
Sharing a cache across arms with DIFFERENT scoring would silently give every
later arm the first arm's universe, which is what voided the first Phase 18 run.

    python scripts/phase21_quality_momentum.py
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
from app.models.price_history import PriceHistory  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.services.backtest import BacktestConfig, run_backtest  # noqa: E402
from app.core.experiment_paths import experiment_dir  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p21")

# Identical to Phases 16-20 so every number stays comparable to them.
START, END, CAPITAL = date(2016, 10, 1), date(2026, 8, 1), 500_000
TRAIN, TEST, ROLL = 18, 6, 6
UNIVERSE_SIZE = 500
OUT = experiment_dir("phase21_quality_momentum")

FROZEN = dict(atr_stop_multiplier=4.0, use_support_stop=False, cash_buffer_pct=0.0,
              use_regime_filter=True, bull_exposure=1.0, bear_exposure=0.25,
              reentry_rule="immediate", ranking_engine="momentum")
V1 = replace(StrategyParams.for_appetite("moderate"), **FROZEN,
             sizing_method="equal_weight", trend_confirm_days=5)

# Percentile a name must reach on each active check. 50 keeps the better half;
# 70 is the tighter variant, included so a null result cannot be blamed on the
# filter having been too gentle to bite.
ARMS: dict[str, dict] = {
    "control_V1":      dict(quality_filter="none"),
    "lowvol_50":       dict(quality_filter="lowvol", quality_min_percentile=50.0),
    "lowdd_50":        dict(quality_filter="lowdd",  quality_min_percentile=50.0),
    "both_50":         dict(quality_filter="both",   quality_min_percentile=50.0),
    "both_70":         dict(quality_filter="both",   quality_min_percentile=70.0),
}
ARM_ORDER = list(ARMS)


def _traded_value(db, stock_ids, upto, window=20):
    """Mean daily traded value over the last `window` bars up to `upto`."""
    rows = (db.query(PriceHistory.stock_id, PriceHistory.date,
                     PriceHistory.close, PriceHistory.volume)
            .filter(PriceHistory.stock_id.in_(stock_ids), PriceHistory.date <= upto,
                    PriceHistory.close.isnot(None), PriceHistory.volume.isnot(None))
            .order_by(PriceHistory.stock_id, PriceHistory.date.desc()).all())
    acc: dict[int, list[float]] = {}
    for sid, _d, close, vol in rows:
        bucket = acc.setdefault(sid, [])
        if len(bucket) < window:
            bucket.append(float(close) * float(vol))
    return {sid: mean(v) for sid, v in acc.items() if v}


def build_universe(db, fold_start: date) -> list[int]:
    """Point-in-time top-500 by liquidity, the neutral construction Phase 19
    settled on. Not production's current-liquidity membership, which Phase 19
    showed encodes hindsight."""
    ids = [s.id for s in db.query(Stock).all()]
    tv = _traded_value(db, ids, fold_start)
    ranked = sorted(tv.items(), key=lambda kv: kv[1], reverse=True)
    return [sid for sid, _ in ranked[:UNIVERSE_SIZE]]


def go(db, stock_ids, s, e, params, cache):
    return run_backtest(db, BacktestConfig(
        start_date=s, end_date=e, initial_capital=CAPITAL, risk_appetite="moderate",
        horizon_days=params.horizon_days, rebalance_frequency=params.rebalance_frequency,
        transaction_cost_pct=DEFAULT_TRANSACTION_COST_PCT, slippage_pct=DEFAULT_SLIPPAGE_PCT,
        params=params, indicator_cache=cache, universe_stock_ids=stock_ids))


def classify(r):
    if r is None:
        return "unknown"
    return ("strong bull" if r >= 12 else "bull" if r >= 3 else "sideways" if r > -3
            else "correction" if r > -12 else "bear")


def main() -> None:
    db = SessionLocal()
    total = db.query(Stock).count()
    if total < UNIVERSE_SIZE:
        raise SystemExit(f"\nPhase 21 needs >= {UNIVERSE_SIZE} stocks; the DB has {total}.\n")
    log.info("pool: %d stocks; each fold draws the point-in-time top %d by liquidity",
             total, UNIVERSE_SIZE)

    rec = {"experiment": "phase21_quality_momentum", "pool": total,
           "universe_size": UNIVERSE_SIZE, "arms": ARM_ORDER, "folds": []}

    s, fold = START, 0
    while True:
        te = s + timedelta(days=int(TRAIN * 30.44))
        tt = te + timedelta(days=int(TEST * 30.44))
        if tt > END:
            break
        fold += 1

        universe = build_universe(db, te)
        # ONE cache for the whole fold. Every arm scores identically — the gate
        # runs at selection — so this is safe here and would not be if the arms
        # differed in any scoring parameter. See the module docstring.
        cache: dict = {}

        entry = {"fold": fold, "test": [str(te), str(tt)], "arms": {}}
        for label in ARM_ORDER:
            params = replace(V1, **ARMS[label])
            r = go(db, universe, te, tt, params, cache)
            entry["arms"][label] = r.metrics
            if "benchmark" not in entry:
                entry["benchmark"] = r.benchmark_metrics
                entry["regime"] = classify(r.benchmark_metrics.get("total_return_pct"))
        rec["folds"].append(entry)

        ctrl = entry["arms"]["control_V1"].get("total_return_pct") or 0
        log.info("Fold %2d %-12s NIFTY %7.2f%%  control %7.2f%%  lowvol %7.2f%%  "
                 "lowdd %7.2f%%  both50 %7.2f%%  both70 %7.2f%%",
                 fold, entry["regime"], entry["benchmark"].get("total_return_pct") or 0, ctrl,
                 entry["arms"]["lowvol_50"].get("total_return_pct") or 0,
                 entry["arms"]["lowdd_50"].get("total_return_pct") or 0,
                 entry["arms"]["both_50"].get("total_return_pct") or 0,
                 entry["arms"]["both_70"].get("total_return_pct") or 0)
        s = s + timedelta(days=int(ROLL * 30.44))

    F = rec["folds"]
    bench_mean = mean([f["benchmark"]["total_return_pct"] for f in F
                       if f["benchmark"].get("total_return_pct") is not None])

    def agg(label, key):
        vals = [f["arms"][label].get(key) for f in F]
        clean = [v for v in vals if v is not None]
        return round(mean(clean), 2) if clean else None

    ctrl_mean = agg("control_V1", "total_return_pct")
    summary = {"folds": len(F), "benchmark_mean_return_pct": round(bench_mean, 2), "arms": {}}
    for a in ARM_ORDER:
        r = agg(a, "total_return_pct")
        beat_bm = sum(1 for f in F
                      if (f["arms"][a].get("total_return_pct") or -999)
                      > (f["benchmark"].get("total_return_pct") or 999))
        beat_ctrl = sum(1 for f in F
                        if (f["arms"][a].get("total_return_pct") or -999)
                        > (f["arms"]["control_V1"].get("total_return_pct") or 999))
        summary["arms"][a] = {
            "return_pct": r, "sharpe": agg(a, "sharpe_ratio"),
            "max_drawdown_pct": agg(a, "max_drawdown_pct"),
            "trades": agg(a, "num_trades"),
            "vs_nifty_pp": round(r - bench_mean, 2) if r is not None else None,
            "vs_control_pp": round(r - ctrl_mean, 2) if r is not None else None,
            "folds_beating_nifty": beat_bm, "folds_beating_control": beat_ctrl,
        }
    rec["summary"] = summary

    log.info("=" * 108)
    log.info("PHASE 21 — QUALITY-MOMENTUM (%d folds, point-in-time top %d universe)",
             len(F), UNIVERSE_SIZE)
    log.info("  %-14s %8s %7s %9s %8s %10s %11s %8s", "arm", "ret%", "Sharpe",
             "maxDD%", "trades", "vs NIFTY", "vs CONTROL", "beat ctl")
    for a in ARM_ORDER:
        m = summary["arms"][a]
        log.info("  %-14s %8.2f %7.2f %9.2f %8.1f %10.2f %11.2f %6d/%d", a,
                 m["return_pct"], m["sharpe"], m["max_drawdown_pct"], m["trades"],
                 m["vs_nifty_pp"], m["vs_control_pp"], m["folds_beating_control"], len(F))
    log.info("  %-14s %8.2f", "NIFTY50", bench_mean)
    log.info("")
    log.info("  An arm counts as a finding only if vs CONTROL is positive AND vs NIFTY is")
    log.info("  positive. Beating the control while still trailing the index is not an edge.")
    # Written AFTER the summary is logged, not before. The 2026-09-10 run wrote
    # first, the write raised, and the summary table (Sharpe, drawdown, trades)
    # was never printed — only the per-fold returns survived, in the log.
    (OUT / "results.json").write_text(json.dumps(rec, indent=2, default=str))
    log.info("wrote %s", OUT / "results.json")
    db.close()


if __name__ == "__main__":
    with experiment_lock("phase21_quality_momentum"):
        main()
