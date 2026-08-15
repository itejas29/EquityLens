"""Phase 14 §3 — cross-sectional information coefficient: momentum vs composite.

Measures whether either ranking predicts NIFTY-RELATIVE forward returns, before
any portfolio construction gets involved. This is the honest place to look for
alpha: a portfolio backtest mixes selection with sizing, stops, regime and
costs, so a ranking can look fine there for reasons unrelated to its ranking.

METHOD. On a monthly grid, rank every eligible stock by each engine, bucket into
deciles, then measure forward 5/10/20-day returns MINUS the benchmark's forward
return over the same window. A useful ranking should show positive Spearman IC
and a monotonic decile pattern; a useless one shows IC near zero and noise
across deciles.

POINT-IN-TIME. Momentum at date T uses closes up to and including T only. The
forward window is the only place future data appears, and it feeds the outcome
being predicted — never a feature.
"""

import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal  # noqa: E402
from app.core.universe_config import HISTORY_PERIOD  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.services.backtest import _load_all_price_frames  # noqa: E402
from app.services.backtest_scoring import compute_point_in_time_universe  # noqa: E402
from app.services.market_data import fetch_price_history  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("phase14")

START = date(2018, 1, 1)
END = date(2026, 3, 1)          # leaves room for the 20-day forward window
HORIZONS = (5, 10, 20)
MOM_LONG, MOM_SHORT = 252, 21   # 12-month minus 1-month
OUT_DIR = Path(__file__).resolve().parents[2] / "docs" / "experiments" / "phase14_alpha"


def momentum_score(frame: pd.DataFrame) -> float | None:
    """12-month return minus 1-month return, from closes up to the frame's end.

    The 1-month leg is subtracted because the most recent month is dominated by
    short-term reversal, which is the opposite effect to momentum; excluding it
    is the standard construction.
    """
    closes = frame["close"].astype(float).dropna()
    if len(closes) < MOM_LONG + 1:
        return None
    last = closes.iloc[-1]
    long_ago = closes.iloc[-MOM_LONG]
    month_ago = closes.iloc[-MOM_SHORT]
    if long_ago <= 0 or month_ago <= 0:
        return None
    return float((last / long_ago - 1) - (last / month_ago - 1))


def forward_return(frame: pd.DataFrame, as_of: date, days: int) -> float | None:
    fut = frame[frame["date"] > as_of]["close"].astype(float).dropna()
    cur = frame[frame["date"] <= as_of]["close"].astype(float).dropna()
    if len(fut) < days or cur.empty:
        return None
    return float(fut.iloc[days - 1] / cur.iloc[-1] - 1)


def main() -> None:
    db = SessionLocal()
    stocks = db.query(Stock).filter(Stock.is_active == True).all()  # noqa: E712
    frames = _load_all_price_frames(db, stocks)
    bench = fetch_price_history("^NSEI", period=HISTORY_PERIOD)[["date", "close"]].sort_values("date")

    all_dates = sorted({d for f in frames.values() for d in f["date"]})
    # Monthly grid: first trading day of each month inside the window.
    grid, seen = [], set()
    for d in all_dates:
        if START <= d <= END and (d.year, d.month) not in seen:
            seen.add((d.year, d.month))
            grid.append(d)
    logger.info("Evaluating %d monthly dates over %d stocks", len(grid), len(frames))

    cache: dict = {}
    rows: list[dict] = []

    for i, as_of in enumerate(grid, start=1):
        bounded = {sid: f[f["date"] <= as_of] for sid, f in frames.items()}
        bounded_bench = bench[bench["date"] <= as_of]
        snapshot = compute_point_in_time_universe(bounded, bounded_bench, None, cache, as_of)

        bench_fwd = {h: forward_return(bench.rename(columns={"close": "close"}), as_of, h) for h in HORIZONS}

        for sid, frame in frames.items():
            b = bounded[sid]
            mom = momentum_score(b)
            snap = snapshot.get(sid)
            comp = snap.overall_score if snap is not None else None
            if mom is None and comp is None:
                continue
            rec = {"date": as_of, "stock_id": sid, "momentum": mom, "composite": comp}
            for h in HORIZONS:
                fwd = forward_return(frame, as_of, h)
                rec[f"fwd_{h}"] = fwd
                rec[f"rel_{h}"] = (fwd - bench_fwd[h]) if (fwd is not None and bench_fwd[h] is not None) else None
            rows.append(rec)

        if i % 12 == 0:
            logger.info("  %d/%d dates", i, len(grid))

    df = pd.DataFrame(rows)
    db.close()

    report: dict = {"dates": len(grid), "observations": len(df), "engines": {}}
    print("\n" + "=" * 118)
    print("PHASE 14 §3 — CROSS-SECTIONAL IC vs NIFTY-RELATIVE FORWARD RETURNS")
    print(f"{len(grid)} monthly dates, {len(df):,} stock-date observations, {START}..{END}")
    print("=" * 118)

    for engine in ("momentum", "composite"):
        print(f"\n--- {engine.upper()} ---")
        eng: dict = {}
        for h in HORIZONS:
            sub = df[[ "date", engine, f"rel_{h}"]].dropna()
            if sub.empty:
                continue
            # IC per date, then averaged — pooling across dates would let a few
            # large cross-sections dominate.
            ics = []
            for _, g in sub.groupby("date"):
                if len(g) < 20:
                    continue
                ic, _p = stats.spearmanr(g[engine], g[f"rel_{h}"])
                if not np.isnan(ic):
                    ics.append(ic)
            if not ics:
                continue
            mean_ic = float(np.mean(ics))
            # t-stat of the IC series: is the mean IC distinguishable from zero?
            t_stat = mean_ic / (float(np.std(ics, ddof=1)) / np.sqrt(len(ics))) if len(ics) > 2 else None

            deciles = []
            for _, g in sub.groupby("date"):
                if len(g) < 20:
                    continue
                g = g.copy()
                g["dec"] = pd.qcut(g[engine].rank(method="first"), 10, labels=False, duplicates="drop")
                deciles.append(g.groupby("dec")[f"rel_{h}"].mean())
            dec_df = pd.DataFrame(deciles)
            dec_mean = (dec_df.mean() * 100) if not dec_df.empty else pd.Series(dtype=float)

            top = float(dec_mean.get(9, np.nan))
            bot = float(dec_mean.get(0, np.nan))
            spread = top - bot
            # Monotonicity: correlation between decile index and its mean return.
            mono = float(np.corrcoef(dec_mean.index.astype(float), dec_mean.values)[0, 1]) if len(dec_mean) > 2 else None
            hit = float((sub[sub[engine] >= sub.groupby("date")[engine].transform("median")][f"rel_{h}"] > 0).mean() * 100)

            eng[f"h{h}"] = {"mean_ic": round(mean_ic, 4), "ic_t_stat": round(t_stat, 2) if t_stat else None,
                            "dates": len(ics), "top_decile_rel_pct": round(top, 3),
                            "bottom_decile_rel_pct": round(bot, 3), "spread_pct": round(spread, 3),
                            "decile_monotonicity": round(mono, 3) if mono else None,
                            "above_median_hit_rate_pct": round(hit, 2)}
            print(f"  {h:>2}d: IC {mean_ic:+.4f} (t={t_stat:+.2f}, n={len(ics)})   "
                  f"top decile {top:+.3f}%  bottom {bot:+.3f}%  spread {spread:+.3f}%   "
                  f"monotonicity {mono:+.3f}   hit {hit:.1f}%")
            print(f"      deciles: " + " ".join(f"{v:+.2f}" for v in dec_mean.values))
        report["engines"][engine] = eng

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / "ic_analysis.json"
    n = 1
    while p.exists():
        n += 1
        p = OUT_DIR / f"ic_analysis_{n}.json"
    p.write_text(json.dumps(report, indent=2, default=str))
    print("\n" + "=" * 118)
    print(f"written: {p}")


if __name__ == "__main__":
    main()
