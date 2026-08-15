"""Phase 12 — extended historical regime validation.

Extends Phase 11 from 5 to 10 years of history so the NIFTY 200DMA regime
filter is tested against materially more corrections, not just the two that
Phase 11 happened to contain.

SURVIVORSHIP / UNIVERSE BIAS — READ BEFORE TRUSTING ANY NUMBER HERE.
The 500-stock universe was selected by liquidity measured TODAY, then applied
backwards. Two distinct biases follow, and neither is corrected:

  1. Survivorship. Companies that were liquid in 2016 but later delisted,
     collapsed or were acquired are absent. Only survivors are tested, which
     flatters every historical result.
  2. Look-ahead universe membership. Names that became liquid only recently
     (recent IPOs) are included in early periods where they either did not
     trade or were marginal.

Stock coverage by year makes the size of it visible: 308/500 have data in 2016
rising to 500/500 in 2026. Historical NSE index membership is not available
from the current data source, so this cannot be fixed here — it is documented
rather than silently ignored. Absolute returns below are therefore optimistic;
the A/B/C COMPARISONS are far more trustworthy than the levels, since all
variants trade the same biased universe.
"""

_PHASE_11_DOC = """Phase 11 — full-history, multi-fold walk-forward validation.

Answers one question: do the improvements found so far (wider ATR stop, no
support stop, NIFTY 200DMA regime filter) survive on genuinely unseen data?

STRUCTURE. Sliding 18-month training window, 6-month out-of-sample window,
rolled forward by 6 months so the OOS windows never overlap. Parameters are
selected inside the training window only, frozen, then evaluated once on the
untouched OOS window.

Each fold evaluates FOUR things on the same OOS window so the attribution is
clean rather than confounded:
    A  fixed  2 ATR, support ON,  regime OFF   (frozen current baseline)
    B  fixed  4 ATR, support OFF, regime OFF   (stop architecture only)
    C  fixed  4 ATR, support OFF, regime ON    (stop + regime)
    S  the configuration the TRAINING window selected

Comparing S against fixed B/C separates "the architecture works" from "the
optimiser found something that happened to fit the training window".

CACHING. One indicator cache is shared across every fold and every candidate.
This is safe because a snapshot is keyed by date and computed from bars up to
that date only — it does not depend on which fold or configuration asked for
it, so reuse cannot leak information between windows.

NO TUNING ON RESULTS. The grid, fold layout and objective are fixed before the
run. Nothing here is adjusted after seeing out-of-sample numbers.
"""

import json
import logging
import subprocess
import sys
import time
from dataclasses import asdict, replace
from datetime import date, timedelta
from itertools import product
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal  # noqa: E402
from app.core.strategy_params import StrategyParams  # noqa: E402
from app.core.universe_config import HISTORY_PERIOD  # noqa: E402
from app.services.backtest import BacktestConfig, run_backtest  # noqa: E402
from app.services.optimizer import objective  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("phase12")

START = date(2016, 10, 1)
END = date(2026, 8, 1)
CAPITAL = 500_000
TRAIN_MONTHS = 18
TEST_MONTHS = 6
ROLL_MONTHS = 6  # == TEST_MONTHS, so OOS windows tile without overlap

OUT_DIR = Path(__file__).resolve().parents[2] / "docs" / "experiments" / "phase12_extended_regime"

BASE = StrategyParams.for_appetite("moderate")

# Frozen comparison configurations — never altered during the experiment.
FIXED = {
    "A_current": replace(BASE, atr_stop_multiplier=2.0, use_support_stop=True,
                         cash_buffer_pct=0.0, use_regime_filter=False),
    "B_wide_stop": replace(BASE, atr_stop_multiplier=4.0, use_support_stop=False,
                           cash_buffer_pct=0.0, use_regime_filter=False),
    "C_wide_regime": replace(BASE, atr_stop_multiplier=4.0, use_support_stop=False,
                             cash_buffer_pct=0.0, use_regime_filter=True,
                             bull_exposure=1.0, bear_exposure=0.25),
}


def build_phase11_grid() -> list[StrategyParams]:
    """ATR x support x (regime off | regime on x bear exposure)."""
    grid: list[StrategyParams] = []
    for atr, sup in product((2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0), (True, False)):
        grid.append(replace(BASE, atr_stop_multiplier=atr, use_support_stop=sup,
                            cash_buffer_pct=0.0, use_regime_filter=False))
        for bear in (0.0, 0.25, 0.50, 0.75, 1.0):
            grid.append(replace(BASE, atr_stop_multiplier=atr, use_support_stop=sup,
                                cash_buffer_pct=0.0, use_regime_filter=True,
                                bull_exposure=1.0, bear_exposure=bear))
    return grid


def run(db, params, start, end, cache):
    cfg = BacktestConfig(
        start_date=start, end_date=end, initial_capital=CAPITAL, risk_appetite="moderate",
        horizon_days=params.horizon_days, rebalance_frequency=params.rebalance_frequency,
        params=params, indicator_cache=cache,
    )
    return run_backtest(db, cfg)


def classify_market(bench_return: float | None) -> str:
    if bench_return is None:
        return "unknown"
    if bench_return >= 12:
        return "strong bull"
    if bench_return >= 3:
        return "bull"
    if bench_return > -3:
        return "sideways"
    if bench_return > -12:
        return "correction"
    return "bear"


def integrity_check(result, label: str, start, end) -> list[str]:
    """Phase 13 — fail loudly rather than silently reporting a broken fold."""
    problems = []
    if not result.equity_curve:
        problems.append(f"{label}: empty equity curve")
    if not result.benchmark_equity_curve:
        problems.append(f"{label}: empty benchmark curve")
    if result.equity_curve:
        first, last = result.equity_curve[0]["date"], result.equity_curve[-1]["date"]
        if first < start or last > end:
            problems.append(f"{label}: equity curve {first}..{last} escapes window {start}..{end}")
        if (last - first).days < 20:
            problems.append(f"{label}: only {(last - first).days} days of curve")
    if result.metrics.get("total_return_pct") is None:
        problems.append(f"{label}: no total return computed")
    return problems


def main() -> None:
    db = SessionLocal()
    cache: dict = {}
    grid = build_phase11_grid()
    started = time.time()

    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True).stdout.strip() or "unknown"
    except Exception:
        commit = "unknown"

    record = {
        "experiment_id": "phase12_extended_regime",
        "git_commit": commit,
        "history_period": HISTORY_PERIOD,
        "window": [str(START), str(END)],
        "train_months": TRAIN_MONTHS, "test_months": TEST_MONTHS, "roll_months": ROLL_MONTHS,
        "grid_size": len(grid),
        "capital": CAPITAL,
        "folds": [],
        "data_quality": [],
    }

    logger.info("Phase 11 — grid %d configs, folds of %dm train / %dm test",
                len(grid), TRAIN_MONTHS, TEST_MONTHS)

    fold = 0
    train_start = START
    while True:
        train_end = train_start + timedelta(days=int(TRAIN_MONTHS * 30.44))
        test_end = train_end + timedelta(days=int(TEST_MONTHS * 30.44))
        if test_end > END:
            break
        fold += 1
        logger.info("Fold %d — train %s..%s  test %s..%s", fold, train_start, train_end, train_end, test_end)

        # --- selection: training window only -----------------------------
        best, best_score, best_metrics = None, float("-inf"), {}
        for i, params in enumerate(grid, start=1):
            try:
                r = run(db, params, train_start, train_end, cache)
            except Exception as exc:
                logger.warning("  candidate %d failed: %s", i, exc)
                continue
            sc = objective(r.metrics)
            if sc > best_score:
                best, best_score, best_metrics = params, sc, r.metrics
            if i % 25 == 0:
                logger.info("    %d/%d evaluated", i, len(grid))

        if best is None:
            logger.error("Fold %d: no usable candidate — skipping", fold)
            train_start = train_start + timedelta(days=int(ROLL_MONTHS * 30.44))
            continue

        # --- evaluation: untouched OOS window ----------------------------
        entry = {
            "fold": fold,
            "train": [str(train_start), str(train_end)],
            "test": [str(train_end), str(test_end)],
            "selected": {"label": best.label(), "params": asdict(best),
                         "in_sample_score": round(best_score, 4),
                         "in_sample_metrics": best_metrics},
            "oos": {},
        }

        for label, params in {**FIXED, "S_selected": best}.items():
            r = run(db, params, train_end, test_end, cache)
            problems = integrity_check(r, f"fold{fold}/{label}", train_end, test_end)
            if problems:
                record["data_quality"].extend(problems)
                logger.error("  INTEGRITY: %s", "; ".join(problems))
            entry["oos"][label] = {"label": params.label(), "metrics": r.metrics}
            if "benchmark" not in entry:
                entry["benchmark"] = r.benchmark_metrics
                entry["market_regime"] = classify_market(r.benchmark_metrics.get("total_return_pct"))

        record["folds"].append(entry)
        b = entry["benchmark"].get("total_return_pct")
        logger.info("  regime=%s  NIFTY %.2f%%  A %.2f%%  B %.2f%%  C %.2f%%  S %.2f%%",
                    entry["market_regime"], b or 0,
                    entry["oos"]["A_current"]["metrics"].get("total_return_pct") or 0,
                    entry["oos"]["B_wide_stop"]["metrics"].get("total_return_pct") or 0,
                    entry["oos"]["C_wide_regime"]["metrics"].get("total_return_pct") or 0,
                    entry["oos"]["S_selected"]["metrics"].get("total_return_pct") or 0)

        train_start = train_start + timedelta(days=int(ROLL_MONTHS * 30.44))

    record["runtime_seconds"] = round(time.time() - started, 1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "results.json"
    n = 1
    while path.exists():
        n += 1
        path = OUT_DIR / f"results_{n}.json"
    path.write_text(json.dumps(record, indent=2, default=str))

    _report(record)
    print(f"\nwritten: {path}")
    db.close()


def _g(fold, key, metric):
    return fold["oos"][key]["metrics"].get(metric)


def _report(record: dict) -> None:
    folds = record["folds"]
    if not folds:
        print("\nNO FOLDS COMPLETED — check data coverage")
        return

    print("\n" + "=" * 150)
    print("PHASE 12 — FOLD-LEVEL OUT-OF-SAMPLE RESULTS")
    print("=" * 150)
    hdr = f"{'fold':<5}{'OOS window':<26}{'regime':<14}{'cfg':<8}{'ret%':>9}{'NIFTY%':>9}{'excess':>9}{'Sharpe':>9}{'maxDD%':>9}{'up%':>8}{'down%':>8}{'depl%':>8}"
    for f in folds:
        print(f"\nFold {f['fold']}  {f['test'][0]}..{f['test'][1]}   market: {f['market_regime']}")
        print(f"  training-selected: {f['selected']['label']}")
        print("  " + hdr)
        bench = f["benchmark"].get("total_return_pct")
        for key in ("A_current", "B_wide_stop", "C_wide_regime", "S_selected"):
            r = _g(f, key, "total_return_pct")
            ex = round(r - bench, 2) if (r is not None and bench is not None) else None
            print(f"  {'':<5}{'':<26}{'':<14}{key[:7]:<8}{str(r):>9}{str(bench):>9}{str(ex):>9}"
                  f"{str(_g(f,key,'sharpe_ratio')):>9}{str(_g(f,key,'max_drawdown_pct')):>9}"
                  f"{str(_g(f,key,'upside_capture_pct')):>8}{str(_g(f,key,'downside_capture_pct')):>8}"
                  f"{str(_g(f,key,'avg_capital_deployed_pct')):>8}")

    print("\n" + "=" * 150)
    print("AGGREGATE OUT-OF-SAMPLE (mean across folds)")
    print("=" * 150)
    print(f"  {'config':<16}{'ret%':>9}{'Sharpe':>9}{'maxDD%':>9}{'up%':>9}{'down%':>9}{'depl%':>9}{'beat NIFTY':>13}")

    def _m(vals):
        clean = [v for v in vals if v is not None]
        return round(mean(clean), 2) if clean else None

    bench_rets = [f["benchmark"].get("total_return_pct") for f in folds]
    for key in ("A_current", "B_wide_stop", "C_wide_regime", "S_selected"):
        rets = [_g(f, key, "total_return_pct") for f in folds]
        wins = sum(1 for r, b in zip(rets, bench_rets) if r is not None and b is not None and r > b)
        print(f"  {key:<16}{str(_m(rets)):>9}{str(_m([_g(f,key,'sharpe_ratio') for f in folds])):>9}"
              f"{str(_m([_g(f,key,'max_drawdown_pct') for f in folds])):>9}"
              f"{str(_m([_g(f,key,'upside_capture_pct') for f in folds])):>9}"
              f"{str(_m([_g(f,key,'downside_capture_pct') for f in folds])):>9}"
              f"{str(_m([_g(f,key,'avg_capital_deployed_pct') for f in folds])):>9}"
              f"{f'{wins}/{len(folds)}':>13}")
    print(f"  {'D_NIFTY50':<16}{str(_m(bench_rets)):>9}"
          f"{str(_m([f['benchmark'].get('sharpe_ratio') for f in folds])):>9}"
          f"{str(_m([f['benchmark'].get('max_drawdown_pct') for f in folds])):>9}"
          f"{'—':>9}{'—':>9}{'100':>9}{'—':>13}")

    print("\n" + "=" * 150)
    print("ATTRIBUTION (aggregate OOS)")
    print("=" * 150)
    a, b_, c = (_m([_g(f, k, "total_return_pct") for f in folds]) for k in ("A_current", "B_wide_stop", "C_wide_regime"))
    a_dd, b_dd, c_dd = (_m([_g(f, k, "max_drawdown_pct") for f in folds]) for k in ("A_current", "B_wide_stop", "C_wide_regime"))
    a_dn, b_dn, c_dn = (_m([_g(f, k, "downside_capture_pct") for f in folds]) for k in ("A_current", "B_wide_stop", "C_wide_regime"))
    if None not in (a, b_, c):
        print(f"  A -> B  (stop architecture): return {a:+.2f}% -> {b_:+.2f}%  ({b_-a:+.2f}pp)   maxDD {a_dd} -> {b_dd}   downside cap {a_dn} -> {b_dn}")
        print(f"  B -> C  (regime filter)    : return {b_:+.2f}% -> {c:+.2f}%  ({c-b_:+.2f}pp)   maxDD {b_dd} -> {c_dd}   downside cap {b_dn} -> {c_dn}")

    print("\n" + "=" * 150)
    print("PARAMETER STABILITY (what training selected each fold)")
    print("=" * 150)
    for f in folds:
        p = f["selected"]["params"]
        regime = f"regime ON bear{p['bear_exposure']:.0%}" if p["use_regime_filter"] else "regime OFF"
        print(f"  Fold {f['fold']}: {p['atr_stop_multiplier']:g} ATR / "
              f"{'support' if p['use_support_stop'] else 'nosup'} / {regime}")

    print("\n" + "=" * 150)
    print("OOS EXCESS RETURN vs NIFTY, and OVERFITTING (IS -> OOS)")
    print("=" * 150)
    for key in ("B_wide_stop", "C_wide_regime", "S_selected"):
        ex = [(_g(f, key, "total_return_pct") or 0) - (f["benchmark"].get("total_return_pct") or 0) for f in folds]
        print(f"  {key:<16} mean {mean(ex):+.2f}%  median {median(ex):+.2f}%  worst {min(ex):+.2f}%  best {max(ex):+.2f}%")
    print()
    for f in folds:
        is_s = f["selected"]["in_sample_metrics"].get("sharpe_ratio")
        oos_s = _g(f, "S_selected", "sharpe_ratio")
        is_r = f["selected"]["in_sample_metrics"].get("total_return_pct")
        oos_r = _g(f, "S_selected", "total_return_pct")
        ratio = round(oos_s / is_s, 2) if (is_s and oos_s is not None and is_s != 0) else None
        print(f"  Fold {f['fold']}: IS Sharpe {is_s} -> OOS {oos_s} (ratio {ratio}) | "
              f"IS ret {is_r}% -> OOS {oos_r}% (delta {round((oos_r or 0)-(is_r or 0),2)}pp)")

    print("\n" + "=" * 150)
    print("BY MARKET REGIME  (does regime protection reduce losses in weak markets?)")
    print("=" * 150)
    buckets: dict[str, list] = {}
    for f in folds:
        buckets.setdefault(f["market_regime"], []).append(f)
    print(f"  {'regime':<14}{'folds':>6}{'NIFTY%':>9}{'A%':>9}{'B%':>9}{'C%':>9}{'S%':>9}{'C down cap':>12}{'B down cap':>12}")
    for regime, fs in sorted(buckets.items()):
        def avg(vals):
            c = [v for v in vals if v is not None]
            return round(mean(c), 2) if c else None
        print(f"  {regime:<14}{len(fs):>6}"
              f"{str(avg([x['benchmark'].get('total_return_pct') for x in fs])):>9}"
              f"{str(avg([_g(x,'A_current','total_return_pct') for x in fs])):>9}"
              f"{str(avg([_g(x,'B_wide_stop','total_return_pct') for x in fs])):>9}"
              f"{str(avg([_g(x,'C_wide_regime','total_return_pct') for x in fs])):>9}"
              f"{str(avg([_g(x,'S_selected','total_return_pct') for x in fs])):>9}"
              f"{str(avg([_g(x,'C_wide_regime','downside_capture_pct') for x in fs])):>12}"
              f"{str(avg([_g(x,'B_wide_stop','downside_capture_pct') for x in fs])):>12}")

    print("\n" + "=" * 150)
    print("PARAMETER STABILITY SUMMARY")
    print("=" * 150)
    n = len(folds)
    wide = sum(1 for f in folds if f["selected"]["params"]["atr_stop_multiplier"] >= 4)
    nosup = sum(1 for f in folds if not f["selected"]["params"]["use_support_stop"])
    reg = sum(1 for f in folds if f["selected"]["params"]["use_regime_filter"])
    print(f"  ATR >= 4      : {wide}/{n} folds")
    print(f"  no support    : {nosup}/{n} folds")
    print(f"  regime filter : {reg}/{n} folds")
    bears = [f["selected"]["params"]["bear_exposure"] for f in folds if f["selected"]["params"]["use_regime_filter"]]
    if bears:
        print(f"  bear exposures selected: {[f'{b:.0%}' for b in bears]}")

    if record["data_quality"]:
        print("\n" + "=" * 150)
        print("DATA QUALITY PROBLEMS")
        for p in record["data_quality"]:
            print(f"  {p}")
    print("=" * 150)
    print(f"runtime: {record['runtime_seconds']}s")


if __name__ == "__main__":
    main()
