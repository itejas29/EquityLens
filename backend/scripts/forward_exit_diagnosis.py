"""Forward exit diagnosis — why do published positions stop out, and would a
different exit have changed anything?

Three questions, fixed before the run:

1. IS THE STOP-OUT RATE ABNORMAL? The published geometry puts the stop 4 ATR
   below the reference close and the target 8 ATR above it, so even a stock
   with no edge reaches the stop far more often than the target. NULL MODEL:
   for every position, NULL_PATHS price paths built by resampling that
   stock's own last NULL_LOOKBACK daily bars (open/high/low/close relative to
   the previous close), drift removed, run through the identical exit rule.
   If the observed stop and target counts sit inside that distribution, the
   levels behave as they would on a coin-flip stock — the stop is not what is
   failing.

2. WERE ENTRIES OVEREXTENDED? How far the reference close sat above its
   20-session average, in ATRs, and the 5- and 20-session run-up before the
   call; whether outcomes differ across thirds of that range.

3. WHAT WOULD OTHER EXITS HAVE DONE? A fixed menu chosen before looking —
   tighter (2/4 ATR), middle (3/6), symmetric (4/4), structural (stop at the
   20-session low), a time stop (out after 5 sessions if below the fill), and
   no levels at all (hold 20 sessions). Every one is IN-SAMPLE on the same
   few dozen positions from one market regime. They describe this record;
   they are not a basis for choosing a rule. V1's exits are frozen
   (core/v1_strategy.py) and change only through the walk-forward research,
   which itself waits on backtest defects #19 and #20.

Only clean calls (record_integrity) and only positions with a complete
20-session window are used, so every outcome is final.

INPUTS. The database by default; --snapshot DIR reads an export of the same
data (prod_snapshot.json + universe_bars.json) for an offline run.

NO TUNING ON RESULTS. Nothing here is adjusted after seeing its output.
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.experiment_paths import experiment_dir  # noqa: E402
from app.services import forward_scoring as fs  # noqa: E402
from app.services.ingestion import is_no_trade_placeholder  # noqa: E402

OUT = experiment_dir("forward_exit_diagnosis")

NULL_PATHS = 2000
NULL_LOOKBACK = 250
SEED = 20261030

RULES = [
    fs.AS_PUBLISHED,
    fs.ExitRule(name="tight_2_4_atr", stop_atr=Decimal(2), target_atr=Decimal(4)),
    fs.ExitRule(name="mid_3_6_atr", stop_atr=Decimal(3), target_atr=Decimal(6)),
    fs.ExitRule(name="symmetric_4_4_atr", stop_atr=Decimal(4), target_atr=Decimal(4)),
    fs.ExitRule(name="structural_20_session_low", support_lookback=20),
    fs.ExitRule(name="time_stop_5_sessions", time_stop_sessions=5),
    fs.ExitRule(name="hold_20_no_levels", use_stop=False, use_target=False),
]


# ---------------------------------------------------------------- loading --

def load_db():
    from app.core.database import SessionLocal
    from app.models.price_history import PriceHistory

    db = SessionLocal()
    try:
        calls = fs.load_calls(db)
        first = min(c.date for c in calls)
        bars = fs.load_bars(db, sorted({c.stock_id for c in calls}), first - timedelta(days=400))
        sessions = fs.load_sessions(db, first - timedelta(days=45))
        basket = fs.BasketBenchmark.from_rows(
            db.query(PriceHistory.stock_id, PriceHistory.date, PriceHistory.open, PriceHistory.close)
            .filter(PriceHistory.date >= first).yield_per(5000)
        )
    finally:
        db.close()
    return calls, bars, sessions, basket


def load_snapshot(directory: Path):
    snap = json.loads((directory / "prod_snapshot.json").read_text())
    dec = lambda v: None if v is None else Decimal(v)  # noqa: E731
    calls = [
        fs.Call(signal_id=int(s["id"]), date=date.fromisoformat(s["date"]), stock_id=int(s["stock_id"]),
                symbol=s["symbol"], entry_low=dec(s["entry_low"]), entry_high=dec(s["entry_high"]),
                stop_loss=dec(s["stop_loss"]), target_price=dec(s["target_price"]),
                reference_date=date.fromisoformat(s["reference_date"]),
                generated_at=datetime.fromisoformat(s["generated_at"]))
        for s in snap["signals"]
    ]
    # Same exclusions as forward_scoring.load_bars: incomplete bars and no-trade
    # placeholders (rows are [date, open, high, low, close, volume]).
    bars = {
        int(sid): [fs.Bar(date.fromisoformat(r[0]), *(Decimal(x) for x in r[1:5]))
                   for r in rows if all(x is not None for x in r[1:5])
                   and not is_no_trade_placeholder(*(Decimal(x) for x in r[1:5]), int(r[5] or 0))]
        for sid, rows in snap["bars"].items()
    }
    first = min(c.date for c in calls)
    universe = json.loads((directory / "universe_bars.json").read_text())
    # The universe export carries no volume, so a flat bar stands in for a
    # placeholder there. Sessions come from it: every stock, not just picks.
    traded = [(int(sid), date.fromisoformat(r[0]), r) for sid, rows in universe.items() for r in rows
              if r[1] is not None and not (r[1] == r[2] == r[3] == r[4])]
    sessions = sorted({d for _, d, _ in traded})
    basket = fs.BasketBenchmark.from_rows((sid, d, r[1], r[4]) for sid, d, r in traded if d >= first)
    return calls, bars, sessions, basket


# ---------------------------------------------------------- 1. null model --

def bar_shapes(prior: list) -> np.ndarray | None:
    """Each prior session as (open, high, low, close) / previous close, with
    the average close-to-close drift divided out, so resampling it gives the
    stock's own volatility and intraday range with no trend."""
    window = prior[-(NULL_LOOKBACK + 1):]
    if len(window) < 60:
        return None
    rel = np.array([[float(b.open / p.close), float(b.high / p.close), float(b.low / p.close),
                     float(b.close / p.close)] for p, b in zip(window, window[1:])])
    drift = np.exp(np.log(rel[:, 3]).mean())
    return rel / drift


def simulate_exits(c: fs.Campaign, shapes: np.ndarray, rng) -> np.ndarray:
    """(stop, target, time) probabilities for this position under no edge,
    using the same order of checks as forward_scoring._manage."""
    paths = NULL_PATHS
    price = np.full(paths, float(c.fill_price))
    alive = np.ones(paths, bool)
    outcome = np.zeros(paths, int)  # 1 stop, 2 target, 3 time
    stop, target = float(c.stop), float(c.target)
    for _ in range(fs.MAX_HOLD_SESSIONS):
        o, h, low, close = (shapes[rng.integers(0, len(shapes), paths)] * price[:, None]).T
        gap_stop = alive & (o <= stop)
        gap_target = alive & ~gap_stop & (o >= target)
        touch_stop = alive & ~gap_stop & ~gap_target & (low <= stop)  # double touch -> stop
        touch_target = alive & ~gap_stop & ~gap_target & ~touch_stop & (h >= target)
        outcome[gap_stop | touch_stop] = 1
        outcome[gap_target | touch_target] = 2
        alive &= ~(gap_stop | touch_stop | gap_target | touch_target)
        price = close
    outcome[alive] = 3
    return np.bincount(outcome, minlength=4)[1:] / paths


def null_model(campaigns, bars, rng) -> dict:
    probs, actual = [], defaultdict(int)
    for c in campaigns:
        prior = [b for b in bars[c.stock_id] if b.date < c.fill_date]
        shapes = bar_shapes(prior)
        if shapes is None:
            continue
        probs.append(simulate_exits(c, shapes, rng))
        actual[c.exit_reason] += 1
    probs = np.array(probs)
    n = len(probs)
    # Distribution of the TOTAL count across positions under no edge: each
    # position is one draw with its own probabilities.
    draws = rng.random((100_000, n))
    stop_totals = (draws < probs[:, 0]).sum(axis=1)
    target_totals = ((draws >= probs[:, 0]) & (draws < probs[:, 0] + probs[:, 1])).sum(axis=1)
    return {
        "positions": n,
        "observed": {k: actual.get(k, 0) for k in ("stop", "target", "time")},
        "expected_under_no_edge": {
            "stop": round(float(probs[:, 0].sum()), 1),
            "target": round(float(probs[:, 1].sum()), 1),
            "time": round(float(probs[:, 2].sum()), 1),
        },
        "no_edge_90pct_range": {
            "stop": [int(np.percentile(stop_totals, 5)), int(np.percentile(stop_totals, 95))],
            "target": [int(np.percentile(target_totals, 5)), int(np.percentile(target_totals, 95))],
        },
        # Share of no-edge outcomes with at least as many stops / at most as
        # many targets as observed. Small = the record is worse than chance.
        "p_stops_at_least_observed": round(float((stop_totals >= actual.get("stop", 0)).mean()), 3),
        "p_targets_at_most_observed": round(float((target_totals <= actual.get("target", 0)).mean()), 3),
    }


# ----------------------------------------------------------- 2. extension --

def extension(campaigns, bars, basket) -> dict:
    rows = []
    for c in campaigns:
        call = c.first_call
        prior = [b for b in bars[c.stock_id] if b.date < call.date]
        if len(prior) < 21 or not call.atr:
            continue
        closes = [float(b.close) for b in prior]
        ref = float(call.reference_close)
        end_date, end_close = c.horizon_closes[fs.MAX_HOLD_SESSIONS]
        net = float((end_close / c.fill_price - 1 - fs.ROUND_TRIP_COST) * 100)
        b = basket.window(c.fill_date, end_date)
        rows.append({
            "ext_atr": (ref - float(np.mean(closes[-20:]))) / float(call.atr),
            "runup_5": (closes[-1] / closes[-6] - 1) * 100,
            "runup_20": (closes[-1] / closes[-21] - 1) * 100,
            "net_20": net,
            "excess_basket_20": None if b is None else net - b,
            "stopped": c.exit_reason == "stop",
            "mae": c.mae_pct,
        })
    if len(rows) < 6:
        return {"positions": len(rows)}
    ext = np.array([r["ext_atr"] for r in rows])
    cuts = np.percentile(ext, [100 / 3, 200 / 3])
    thirds = []
    for name, lo, hi in (("least_extended", -np.inf, cuts[0]), ("middle", cuts[0], cuts[1]),
                         ("most_extended", cuts[1], np.inf)):
        part = [r for r in rows if lo < r["ext_atr"] <= hi] if lo != -np.inf else [r for r in rows if r["ext_atr"] <= hi]
        excess = [r["excess_basket_20"] for r in part if r["excess_basket_20"] is not None]
        thirds.append({
            "third": name,
            "positions": len(part),
            "ext_atr_range": [round(float(min(r["ext_atr"] for r in part)), 2), round(float(max(r["ext_atr"] for r in part)), 2)],
            "mean_net_20_pct": round(float(np.mean([r["net_20"] for r in part])), 2),
            "mean_excess_vs_universe_20_pct": round(float(np.mean(excess)), 2) if excess else None,
            "stopped": sum(r["stopped"] for r in part),
            "median_mae_pct": round(float(np.median([r["mae"] for r in part])), 2),
        })

    def rank(a):
        return np.argsort(np.argsort(a))

    nets = np.array([r["net_20"] for r in rows])
    return {
        "positions": len(rows),
        "median_ext_atr": round(float(np.median(ext)), 2),
        "median_runup_5_pct": round(float(np.median([r["runup_5"] for r in rows])), 2),
        "median_runup_20_pct": round(float(np.median([r["runup_20"] for r in rows])), 2),
        "spearman_ext_vs_net_20": round(float(np.corrcoef(rank(ext), rank(nets))[0, 1]), 3),
        "thirds": thirds,
    }


# ---------------------------------------------------------- 3. what-ifs --

def what_if(rule, clean_calls, bars, basket) -> dict:
    campaigns, _, _ = fs.build_campaigns(clean_calls, bars, rule)
    final = [c for c in campaigns if c.window_sessions >= fs.MAX_HOLD_SESSIONS and c.resolved]
    nets = [float((c.exit_price / c.fill_price - 1 - fs.ROUND_TRIP_COST) * 100) for c in final]
    pairs = [(n, basket.window(c.fill_date, c.exit_date), c.fill_date.isocalendar()[:2]) for n, c in zip(nets, final)]
    pairs = [(n - b, wk) for n, b, wk in pairs if b is not None]
    exits = defaultdict(int)
    for c in final:
        exits[c.exit_reason] += 1
    ci = fs.bootstrap_mean_ci([e for e, _ in pairs], [wk for _, wk in pairs]) if pairs else None
    return {
        "rule": rule.name,
        **fs._stats(nets),
        "exits": dict(exits),
        "mean_holding_sessions": round(float(np.mean([c.held_sessions for c in final])), 1) if final else None,
        "mean_excess_vs_universe_pct": round(float(np.mean([e for e, _ in pairs])), 2) if pairs else None,
        "excess_ci95": list(ci) if ci else None,
    }


# ------------------------------------------------------------------- main --

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--snapshot", type=Path, help="read an exported snapshot instead of the database")
    args = parser.parse_args()

    calls, bars, sessions, basket = load_snapshot(args.snapshot) if args.snapshot else load_db()
    clean_calls, _ = fs.split_clean(calls, sessions)
    campaigns, _, _ = fs.build_campaigns(clean_calls, bars, fs.AS_PUBLISHED)
    final = [c for c in campaigns if c.window_sessions >= fs.MAX_HOLD_SESSIONS]
    rng = np.random.default_rng(SEED)

    result = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "source": f"snapshot:{args.snapshot.name}" if args.snapshot else "database",
        "calls": len(calls),
        "clean_calls": len(clean_calls),
        "positions_with_complete_window": len(final),
        "last_call_date": max(c.date for c in calls).isoformat(),
        "null_model": null_model(final, bars, rng),
        "extension": extension(final, bars, basket),
        "what_if_in_sample": [what_if(rule, clean_calls, bars, basket) for rule in RULES],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    print(f"\nWritten to {OUT / 'results.json'}")


if __name__ == "__main__":
    main()
