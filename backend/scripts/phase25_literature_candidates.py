"""Phase 25 — do rankings from the literature beat NIFTY 50 on the
survivorship-free bench?

Pre-registration: docs/experiments/phase25_literature_candidates/PREREGISTRATION.md
(committed before this script existed). Folds and each fold's universe B are
Phase 24's (its universes.json), unchanged. Every arm is frozen V1's engine at
30 positions; only the ranking differs. The ML arm's model is trained once per
fold, on month-ends whose label windows close before the fold starts.

Run against the lab only.
"""

import json
import logging
import sys
import time
from collections import Counter
from dataclasses import replace
from datetime import date
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.services.backtest as backtest_module  # noqa: E402
from app.core.backtest_config import DEFAULT_SLIPPAGE_PCT, DEFAULT_TRANSACTION_COST_PCT  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.experiment_lock import experiment_lock  # noqa: E402
from app.core.experiment_paths import experiment_dir  # noqa: E402
from app.core.universe_config import HISTORY_PERIOD  # noqa: E402
from app.core.v1_strategy import V1  # noqa: E402
from app.ml import ranker  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.services.backtest import BacktestConfig, _load_all_price_frames, run_backtest  # noqa: E402
from app.services.market_data import fetch_price_history  # noqa: E402
from app.services.price_integrity import find_discontinuity  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p25")

OUT = experiment_dir("phase25_literature_candidates")
P24 = experiment_dir("phase24_survivorship")
PRODUCTION_HOST_FRAGMENT = "ep-aged-scene-a6dw1x3w"
CAPITAL = 500_000
# Frozen V1 at 30 positions, keeping its 20% sector cap (2 of 10 -> 6 of 30).
BASE = replace(V1, max_stocks=30, max_stocks_per_sector=6)
ARMS = {
    "M0_momentum": "momentum",
    "M1_residual_momentum": "momentum_residual",
    "M2_high_52w": "high_52w",
    "M3_momentum_lowvol": "momentum_lowvol",
    "M4_ml_ranker": "external",
}
SELECTION = range(1, 11)   # folds 1-10: test windows 2018-04 -> 2023-03
HOLDOUT = range(11, 17)    # folds 11-16: 2023-03 -> 2026-03


def pin_benchmark():
    """Download NIFTY once and hand every backtest that copy.

    run_backtest fetches ^NSEI from Yahoo on each call, and Yahoo intermittently
    answers "no price data" mid-run (it did in this phase's smoke run). Every
    arm and fold should be measured against the identical series anyway."""
    bench = fetch_price_history("^NSEI", period=HISTORY_PERIOD)
    real = backtest_module.fetch_price_history

    def pinned(symbol, period="2y"):
        return bench.copy() if symbol == "^NSEI" else real(symbol, period=period)

    backtest_module.fetch_price_history = pinned
    return bench[["date", "close"]]


def _run(db, ids, te, tt, params, cache, score_fn=None):
    r = run_backtest(db, BacktestConfig(
        start_date=te, end_date=tt, initial_capital=CAPITAL, risk_appetite="moderate",
        horizon_days=params.horizon_days, rebalance_frequency=params.rebalance_frequency,
        transaction_cost_pct=DEFAULT_TRANSACTION_COST_PCT, slippage_pct=DEFAULT_SLIPPAGE_PCT,
        params=params, indicator_cache=cache, universe_stock_ids=ids, score_fn=score_fn))
    return r, {**r.metrics, "exit_reasons": dict(Counter(t.exit_reason for t in r.trade_log))}


def _basket(frames, ids, first, last) -> tuple[float, dict]:
    """Equal-weight buy-and-hold of the fold's universe at no cost: bought at
    the close of the first test session (or the stock's last close before it),
    held to the last; a name that stops trading is held at its last close."""
    rets, no_data, stepped = [], 0, 0
    for sid in ids:
        df = frames.get(sid)
        closes = df.dropna(subset=["close"]) if df is not None else None
        if closes is None or closes[closes["date"] <= first].empty:
            no_data += 1
            continue
        entry_day = closes[closes["date"] <= first]["date"].iloc[-1]
        window = closes[(closes["date"] >= entry_day) & (closes["date"] <= last)]["close"].astype(float).tolist()
        # Pre-registration change 2: the corporate-action gate every ranking applies.
        if find_discontinuity(window) is not None:
            stepped += 1
            continue
        rets.append(window[-1] / window[0] - 1)
    return mean(rets) * 100, {"names": len(rets), "no_data_before_window": no_data,
                              "left_out_corporate_action_step": stepped}


def _stats(values: list[float]) -> dict:
    """Unrounded, because the decision compares them with 0; rounded copies are reported."""
    loo = [mean(values[:i] + values[i + 1:]) for i in range(len(values))]
    return {"mean": mean(values), "median": median(values), "folds_beating": sum(v > 0 for v in values),
            "folds": len(values), "leave_one_out_mean_min": min(loo)}


def _rounded(stats: dict) -> dict:
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in stats.items()}


def main() -> None:
    db = SessionLocal()
    if PRODUCTION_HOST_FRAGMENT in str(db.get_bind().url):
        raise SystemExit("Refusing: DATABASE_URL is production. Run against the lab.")
    plan = json.loads((P24 / "universes.json").read_text())
    folds = plan["folds"]
    started = time.time()

    # --- ML: one panel of month-end rows, trained per fold on what precedes it ---
    union = sorted({sid for p in folds for sid in p["B"]})
    stocks = db.query(Stock).filter(Stock.id.in_(union)).all()
    frames = _load_all_price_frames(db, stocks)
    bench = pin_benchmark()
    calendar = sorted(bench["date"])
    starts = [(date.fromisoformat(p["test"][0]), p["B"]) for p in folds]

    def universe_for(d):
        # The B of the latest fold starting on or before d; fold 1's before it.
        current = starts[0][1]
        for s, ids in starts:
            if s <= d:
                current = ids
        return current

    last_end = date.fromisoformat(folds[-1]["test"][1])
    ends = [d for d in ranker.month_ends(calendar) if ranker.TRAIN_FROM <= d <= last_end]
    log.info("ML panel: %d stocks, %d month-ends", len(frames), len(ends))
    rows = ranker.training_rows(ranker.prepare_series(frames, bench), calendar, ends, universe_for)
    log.info("ML panel built: %d rows in %.0fs", len(rows), time.time() - started)

    rec = {"experiment": "phase25_literature_candidates", "arms": ARMS, "positions": BASE.max_stocks,
           "max_per_sector": BASE.max_stocks_per_sector, "params": BASE.label(),
           "benchmark_series": [str(calendar[0]), str(calendar[-1]), len(calendar)], "ml": {
               "model": ranker.MODEL_PARAMS, "features": list(ranker.FEATURES), "horizon": ranker.HORIZON,
               "panel_rows": len(rows), "panel_month_ends": len(ends)}, "folds": []}

    for p in folds:
        k = p["fold"]
        te, tt = date.fromisoformat(p["test"][0]), date.fromisoformat(p["test"][1])
        train = ranker.rows_before(rows, te)
        model = ranker.fit(train)
        score_fn = ranker.make_score_fn(model)
        cache: dict = {}
        fold = {"fold": k, "test": p["test"], "universe_size": len(p["B"]),
                "ml_training_rows": len(train),
                "ml_training_month_ends": int(train["month_end"].nunique()),
                "arms": {}}
        for arm, engine in ARMS.items():
            params = replace(BASE, ranking_engine=engine)
            r, m = _run(db, p["B"], te, tt, params, cache, score_fn if engine == "external" else None)
            fold["arms"][arm] = m
            fold["benchmark"] = r.benchmark_metrics
        sessions = [d for d in calendar if te <= d <= tt]
        fold["basket_return_pct"], fold["basket"] = _basket(frames, p["B"], sessions[0], sessions[-1])
        rec["folds"].append(fold)
        log.info("Fold %2d  NIFTY %7.2f  basket %7.2f  %s  (ML rows %d)", k,
                 fold["benchmark"]["total_return_pct"], fold["basket_return_pct"],
                 "  ".join(f"{a.split('_')[0]} {fold['arms'][a]['total_return_pct']:7.2f}" for a in ARMS),
                 len(train))

    # --- decision, exactly as registered --------------------------------------
    F = {f["fold"]: f for f in rec["folds"]}

    def excess(arm, ks, over="nifty"):
        ref = (lambda f: f["benchmark"]["total_return_pct"]) if over == "nifty" else (
            lambda f: f["basket_return_pct"])
        return [F[k]["arms"][arm]["total_return_pct"] - ref(F[k]) for k in ks]

    every = list(range(1, 17))
    per_arm = {}
    for arm in ARMS:
        per_arm[arm] = {
            "selection_vs_nifty": _stats(excess(arm, SELECTION)),
            "holdout_vs_nifty": _stats(excess(arm, HOLDOUT)),
            "holdout_vs_basket": _stats(excess(arm, HOLDOUT, "basket")),
            "all_vs_nifty": _stats(excess(arm, every)),
            "all_vs_basket": _stats(excess(arm, every, "basket")),
        }
    qualified = [a for a in ARMS if per_arm[a]["selection_vs_nifty"]["median"] > 0
                 and per_arm[a]["selection_vs_nifty"]["leave_one_out_mean_min"] > 0]
    selected = max(qualified, key=lambda a: per_arm[a]["selection_vs_nifty"]["mean"]) if qualified else None
    decision = {"qualified_on_selection_folds": qualified, "selected": selected}
    if selected:
        s = per_arm[selected]
        decision["holdout_pass"] = (s["holdout_vs_nifty"]["mean"] > 0 and s["holdout_vs_nifty"]["median"] > 0
                                    and s["holdout_vs_basket"]["mean"] > 0)
        decision["full_period_pass"] = (s["all_vs_nifty"]["median"] > 0
                                        and s["all_vs_nifty"]["leave_one_out_mean_min"] > 0)
        decision["v2_candidate"] = decision["holdout_pass"] and decision["full_period_pass"]
    else:
        decision["v2_candidate"] = False
    nifty = [F[k]["benchmark"]["total_return_pct"] for k in every]
    basket = [F[k]["basket_return_pct"] for k in every]
    p24 = json.loads((P24 / "results.json").read_text())
    rec["summary"] = {
        "decision": decision,
        "arms": {a: {key: _rounded(v) for key, v in st.items()} for a, st in per_arm.items()},
        "basket_vs_nifty": _rounded(_stats([b - n for b, n in zip(basket, nifty)])),
        "reference_v1_10_stocks_vs_nifty": p24["summary"]["survivorship_free_vs_nifty50"],
        "runtime_minutes": round((time.time() - started) / 60, 1),
    }
    (OUT / "results.json").write_text(json.dumps(rec, indent=2, default=str))
    print(json.dumps(rec["summary"], indent=2, default=str))
    print(f"\nWritten to {OUT / 'results.json'}")


if __name__ == "__main__":
    with experiment_lock("phase25_literature_candidates"):
        main()
