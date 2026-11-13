"""Phase 28 — descriptive diagnostics on the registered results. Changes no verdict.

1. Outliers: the trades with the largest excess, each checked for a price
   discontinuity (an unadjusted split/bonus) inside its window.
2. Random-entry control: the same trade rules on random (stock, date) pairs
   from the same universes and period, so a signal's median can be read
   against what the rules do to an arbitrary stock.
3. Means and medians with the top 5% of trades by excess removed.

Lab only.  DATABASE_URL=<lab> python scripts/phase28_diagnostics.py
"""

import json
import random
import sys
from datetime import date
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.database import SessionLocal  # noqa: E402
from app.core.lab_guard import require_lab_database  # noqa: E402
from app.services.price_integrity import find_discontinuity  # noqa: E402
from phase28_event_backtest import (  # noqa: E402
    OUT, SAMPLE_START, STOCK_COST_RT, Universe, load_nifty, load_stock_bars, simulate)

SEED = 28
CONTROL_TRADES = 5000


def main() -> None:
    db = SessionLocal()
    require_lab_database(db.get_bind().url)
    res = json.loads((OUT / "results.json").read_text())
    nifty, cal = load_nifty()
    universe = Universe()
    bars = load_stock_bars(db, universe.all_ids)
    diag = {}

    for key in ("S1_insider_cluster_buy", "S2_bulk_block_buy", "S5_index_deletion_recovery"):
        trades = res["signals"][key]["trades_detail"]
        flagged = []
        for t in trades:
            b = bars[t["stock_id"]]
            lo = b.d.index(date.fromisoformat(t["entry"]))
            hi = max(i for i, d in enumerate(b.d) if d <= date.fromisoformat(t["exit"]))
            if find_discontinuity(b.c[lo:hi + 1]) is not None:
                flagged.append(t)
        top = sorted(trades, key=lambda t: t["excess"], reverse=True)
        cut = max(1, len(trades) // 20)
        rest = top[cut:]
        flagged_ids = {(t["symbol"], t["entry"]) for t in flagged}
        clean = [t for t in trades if (t["symbol"], t["entry"]) not in flagged_ids]
        diag[key] = {
            "trades": len(trades),
            "with_price_discontinuity": len(flagged),
            "top10": [{k: (round(v * 100, 2) if k in ("excess", "net", "bench") else v) for k, v in t.items()
                       if k in ("symbol", "public", "entry", "exit", "reason", "net", "bench", "excess")}
                      | {"discontinuity": (t["symbol"], t["entry"]) in flagged_ids} for t in top[:10]],
            "without_top_5pct": {"median_excess_pct": median(t["excess"] for t in rest) * 100,
                                 "mean_excess_pct": mean(t["excess"] for t in rest) * 100},
            "without_discontinuities": {"trades": len(clean),
                                        "median_excess_pct": median(t["excess"] for t in clean) * 100,
                                        "mean_excess_pct": mean(t["excess"] for t in clean) * 100},
        }

    by_review: dict[str, list[float]] = {}
    for t in res["signals"]["S5_index_deletion_recovery"]["trades_detail"]:
        by_review.setdefault(t["effective"], []).append(t["excess"])
    diag["S5_by_review"] = {d: {"trades": len(v), "median_excess_pct": median(v) * 100,
                                "mean_excess_pct": mean(v) * 100} for d, v in sorted(by_review.items())}
    no_covid = [x for d, v in by_review.items() if not d.startswith("2020-03") for x in v]
    diag["S5_without_march_2020"] = {"trades": len(no_covid), "median_excess_pct": median(no_covid) * 100,
                                     "mean_excess_pct": mean(no_covid) * 100}

    # Random-entry control, same rules, same universes, same period.
    rng = random.Random(SEED)
    sessions = [d for d in cal if d >= SAMPLE_START]
    control = []
    while len(control) < CONTROL_TRADES:
        day = rng.choice(sessions)
        fold = next((ids for a, b, ids in universe.folds if a <= day <= b), universe.folds[-1][2])
        sid = rng.choice(sorted(fold))
        if sid not in bars:
            continue
        t = simulate(bars[sid], day, cal, nifty, STOCK_COST_RT, add_dividend=False)
        if t is not None:
            control.append(t)
    x = [t["excess"] for t in control]
    diag["random_entry_control"] = {
        "seed": SEED, "trades": len(x), "median_excess_pct": median(x) * 100, "mean_excess_pct": mean(x) * 100,
        "win_rate_pct": sum(v > 0 for v in x) / len(x) * 100,
        "exit_reasons": {r: sum(t["reason"] == r for t in control) for r in ("time", "stop", "delisted")}}

    (OUT / "diagnostics.json").write_text(json.dumps(diag, indent=1, default=str))
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "top10"} for k, v in diag.items()},
                     indent=1, default=str))
    for key in ("S1_insider_cluster_buy", "S5_index_deletion_recovery"):
        print(f"\n{key} top 10 by excess:")
        for t in diag[key]["top10"]:
            print("  ", t)


if __name__ == "__main__":
    main()
