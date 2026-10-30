"""Phase 26 — the crash brake on NIFTY 50 itself, 2007–2026.

Pre-registration: docs/experiments/phase26_crash_brake/PREREGISTRATION.md
(committed before this script existed). Yahoo's ^NSEI daily closes are the
only input; no database is touched.
"""

import json
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.experiment_lock import experiment_lock  # noqa: E402
from app.core.experiment_paths import experiment_dir  # noqa: E402
from app.services import index_timing as it  # noqa: E402
from app.services.market_data import fetch_price_history  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p26")

OUT = experiment_dir("phase26_crash_brake")
RULES = ("BH", "R1", "R2", "R3")
CAPITAL = 1_000_000.0
COST, COST_SENSITIVITY = 0.001, 0.0025
EPISODE_DEPTH = -0.15
DD_RATIO_MAX = 2 / 3   # R1's worst loss at most two-thirds of buy-and-hold's


def load_closes() -> pd.Series:
    df = fetch_price_history("^NSEI", period="max").dropna(subset=["close"])
    s = pd.Series(df["close"].astype(float).to_numpy(), index=list(df["date"])).sort_index()
    return s[~s.index.duplicated(keep="last")]


def data_checks(c: pd.Series) -> dict:
    moves = c.pct_change().dropna()
    gaps = pd.Series(c.index[1:], index=c.index[1:]).diff()
    return {"sessions": len(c), "first": str(c.index[0]), "last": str(c.index[-1]),
            "non_positive": int((c <= 0).sum()),
            "moves_over_12pct": {str(d): round(float(m) * 100, 2) for d, m in moves[moves.abs() > 0.12].items()},
            "gaps_over_5_days": [str(d) for d, g in
                                 zip(c.index[1:], [(b - a).days for a, b in zip(c.index[:-1], c.index[1:])]) if g > 5]}


def break_even_yield(c, decided, start, target_cagr) -> float:
    """Constant cash yield at which the rule's pre-tax CAGR equals buy-and-hold's."""
    def gap(y):
        run = it.simulate(c, decided, start, CAPITAL, COST, cash_yield=y)
        return it.metrics(run, CAPITAL)["cagr_pct"] - target_cagr
    if gap(0.0) >= 0:
        return 0.0
    lo, hi = 0.0, 0.30
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if gap(mid) < 0 else (lo, mid)
    return hi


def main() -> None:
    c = load_closes()
    checks = data_checks(c)
    decided = {r: it.decided_exposure(c, r) for r in RULES}
    first_signal = max(d.first_valid_index() for d in decided.values())
    start = c.index[c.index.get_loc(first_signal) + 1]   # first session that trades on a decision
    window = [d for d in c.index if d >= start]
    mid = window[len(window) // 2]
    log.info("NIFTY %s → %s (%d sessions); measured from %s, halves split at %s",
             checks["first"], checks["last"], checks["sessions"], start, mid)

    runs = {r: it.simulate(c, decided[r], start, CAPITAL, COST) for r in RULES}
    bh = runs["BH"]
    episodes = it.drawdown_episodes(bh.values, EPISODE_DEPTH)
    rec = {"experiment": "phase26_crash_brake", "data": checks, "start": str(start), "end": str(window[-1]),
           "half_split": str(mid), "cost": COST, "cash_yield": 0.0, "arms": {},
           "bh_drawdowns_deeper_than_15pct": [
               {"peak": str(p), "trough": str(t), "bh_pct": round(dd * 100, 2)} for p, t, dd in episodes]}
    bh_cagr = it.metrics(bh, CAPITAL)["cagr_pct"]
    for r in RULES:
        run = runs[r]
        m = it.metrics(run, CAPITAL)
        taxed = it.simulate(c, decided[r], start, CAPITAL, COST, taxed=True)
        dear = it.simulate(c, decided[r], start, CAPITAL, COST_SENSITIVITY)
        rec["arms"][r] = {
            **m,
            "first_half": it.sub_period(run.values, window[0], mid),
            "second_half": it.sub_period(run.values, mid, window[-1]),
            "downside_capture_pct": it.downside_capture(run.values, bh.values),
            "after_tax": {"final_value": taxed.final_value, "tax_paid": taxed.tax_paid,
                          "cagr_pct": it.cagr(CAPITAL, taxed.final_value, window[0], window[-1]) * 100},
            "cost_025pct": {"cagr_pct": it.metrics(dear, CAPITAL)["cagr_pct"],
                            "max_drawdown_pct": it.metrics(dear, CAPITAL)["max_drawdown_pct"]},
            "break_even_cash_yield_pct": (None if r == "BH" else
                                          break_even_yield(c, decided[r], start, bh_cagr) * 100),
            "in_bh_drawdowns_pct": [round((float(run.values.loc[t]) / float(run.values.loc[p]) - 1) * 100, 2)
                                    for p, t, _ in episodes],
        }

    # --- decision, exactly as registered -------------------------------------
    a, b = rec["arms"]["R1"], rec["arms"]["BH"]

    def passes(x, y):
        return (x["max_drawdown_pct"] / y["max_drawdown_pct"] <= DD_RATIO_MAX
                and x["calmar"] is not None and y["calmar"] is not None and x["calmar"] > y["calmar"])

    checks_ = {"whole": passes(a, b), "first_half": passes(a["first_half"], b["first_half"]),
               "second_half": passes(a["second_half"], b["second_half"])}
    rec["decision"] = {**checks_, "r1_passes": all(checks_.values()),
                       "r1_cagr_above_bh": a["cagr_pct"] > b["cagr_pct"]}
    (OUT / "results.json").write_text(json.dumps(rec, indent=2, default=str))
    for r in RULES:
        x = rec["arms"][r]
        log.info("%s  CAGR %6.2f  maxDD %7.2f  Calmar %5.2f  Sharpe %5.2f  vol %5.2f  switches/yr %4.1f  "
                 "invested %5.1f%%  after-tax CAGR %6.2f  break-even cash %s", r, x["cagr_pct"],
                 x["max_drawdown_pct"], x["calmar"] or 0, x["sharpe"] or 0, x["volatility_pct"],
                 x["switches_per_year"], x["invested_share_pct"], x["after_tax"]["cagr_pct"],
                 x["break_even_cash_yield_pct"])
    print(json.dumps(rec["decision"], indent=2))
    print(f"Written to {OUT / 'results.json'}")


if __name__ == "__main__":
    with experiment_lock("phase26_crash_brake"):
        main()
