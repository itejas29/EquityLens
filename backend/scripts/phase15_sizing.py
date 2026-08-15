"""Phase 14 §4 — composite vs momentum ranking, frozen risk layer.

Momentum has no trainable parameters, so per spec §2 it is evaluated directly
on every OOS window rather than selected in-sample. The risk layer is frozen at
the Phase 13 conclusion and identical for both arms; the ONLY difference is
which score drives selection.
"""
import json, logging, sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.database import SessionLocal
from app.core.strategy_params import StrategyParams
from app.services.backtest import BacktestConfig, run_backtest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p14")

START, END, CAPITAL = date(2016,10,1), date(2026,8,1), 500_000
TRAIN, TEST, ROLL = 18, 6, 6
OUT = Path(__file__).resolve().parents[2]/"docs"/"experiments"/"phase15_sizing"

# Phase 15: momentum ranking AND the whole risk layer are frozen. Only
# sizing_method varies, so every difference below is attributable to sizing.
FROZEN = dict(atr_stop_multiplier=4.0, use_support_stop=False, cash_buffer_pct=0.0,
              use_regime_filter=True, bull_exposure=1.0, bear_exposure=0.25,
              reentry_rule="immediate", ranking_engine="momentum")
BASE = StrategyParams.for_appetite("moderate")
ARMS = {
    "A_atr_risk":     replace(BASE, **FROZEN, sizing_method="atr_risk"),
    "B_equal_weight": replace(BASE, **FROZEN, sizing_method="equal_weight"),
    "C_inverse_vol":  replace(BASE, **FROZEN, sizing_method="inverse_vol"),
}

def go(db, p, s, e, cache):
    return run_backtest(db, BacktestConfig(start_date=s, end_date=e, initial_capital=CAPITAL,
        risk_appetite="moderate", horizon_days=p.horizon_days,
        rebalance_frequency=p.rebalance_frequency, params=p, indicator_cache=cache))

def classify(r):
    if r is None: return "unknown"
    return "strong bull" if r>=12 else "bull" if r>=3 else "sideways" if r>-3 else "correction" if r>-12 else "bear"

def main():
    db, cache, rec = SessionLocal(), {}, {"folds": []}
    s = START; fold = 0
    while True:
        te = s+timedelta(days=int(TRAIN*30.44)); tt = te+timedelta(days=int(TEST*30.44))
        if tt > END: break
        fold += 1
        e = {"fold": fold, "test": [str(te), str(tt)], "arms": {}}
        for name, p in ARMS.items():
            r = go(db, p, te, tt, cache)
            e["arms"][name] = r.metrics
            if "benchmark" not in e:
                e["benchmark"] = r.benchmark_metrics
                e["regime"] = classify(r.benchmark_metrics.get("total_return_pct"))
        rec["folds"].append(e)
        log.info("Fold %d %s  NIFTY %.2f%%  atr %.2f%%  eq %.2f%%  ivol %.2f%%", fold, e["regime"],
                 e["benchmark"].get("total_return_pct") or 0,
                 e["arms"]["A_atr_risk"].get("total_return_pct") or 0,
                 e["arms"]["B_equal_weight"].get("total_return_pct") or 0,
                 e["arms"]["C_inverse_vol"].get("total_return_pct") or 0)
        s = s+timedelta(days=int(ROLL*30.44))

    F = rec["folds"]
    def m(vals):
        c=[v for v in vals if v is not None]; return round(mean(c),2) if c else None
    print("\n"+"="*126); print("PHASE 15 — MOMENTUM POSITION SIZING (ranking + risk layer frozen)"); print("="*126)
    print(f"  {'fold':<6}{'OOS window':<26}{'regime':<14}{'NIFTY%':>9}{'atr%':>9}{'eqwt%':>9}{'ivol%':>9}{'eq-atr':>9}")
    for f in F:
        b=f["benchmark"].get("total_return_pct")
        a=f["arms"]["A_atr_risk"].get("total_return_pct")
        eq=f["arms"]["B_equal_weight"].get("total_return_pct")
        iv=f["arms"]["C_inverse_vol"].get("total_return_pct")
        print(f"  {f['fold']:<6}{f['test'][0]+'..'+f['test'][1]:<26}{f['regime']:<14}{b:>9.2f}{a:>9.2f}{eq:>9.2f}{iv:>9.2f}{eq-a:>9.2f}")
    print("\n"+"="*126); print("AGGREGATE OOS"); print("="*126)
    print(f"  {'arm':<14}{'ret%':>9}{'Sharpe':>9}{'Sortino':>9}{'maxDD%':>9}{'up%':>9}{'down%':>9}{'depl%':>9}{'trades':>8}{'hold_d':>8}{'beat':>8}")
    bench=[f["benchmark"].get("total_return_pct") for f in F]
    for name in ARMS:
        rets=[f["arms"][name].get("total_return_pct") for f in F]
        wins=sum(1 for r,b in zip(rets,bench) if r is not None and b is not None and r>b)
        g=lambda k: m([f["arms"][name].get(k) for f in F])
        print(f"  {name:<14}{str(m(rets)):>9}{str(g('sharpe_ratio')):>9}{str(g('sortino_ratio')):>9}{str(g('max_drawdown_pct')):>9}"
              f"{str(g('upside_capture_pct')):>9}{str(g('downside_capture_pct')):>9}{str(g('avg_capital_deployed_pct')):>9}"
              f"{str(g('total_trades')):>8}{str(g('avg_holding_period_days')):>8}{f'{wins}/{len(F)}':>8}")
    print(f"  {'NIFTY50':<14}{str(m(bench)):>9}{str(m([f['benchmark'].get('sharpe_ratio') for f in F])):>9}"
          f"{str(m([f['benchmark'].get('sortino_ratio') for f in F])):>9}"
          f"{str(m([f['benchmark'].get('max_drawdown_pct') for f in F])):>9}{'—':>9}{'—':>9}{'100':>9}{'—':>8}{'—':>8}{'—':>8}")
    print("\n  EXCESS vs NIFTY")
    for name in ARMS:
        ex=[(f["arms"][name].get("total_return_pct") or 0)-(f["benchmark"].get("total_return_pct") or 0) for f in F]
        print(f"    {name:<12} mean {mean(ex):+.2f}%  median {median(ex):+.2f}%  worst {min(ex):+.2f}%  best {max(ex):+.2f}%")
    print("\n  BY REGIME")
    buckets={}
    for f in F: buckets.setdefault(f["regime"],[]).append(f)
    for reg,fs in sorted(buckets.items()):
        print(f"    {reg:<14} n={len(fs)}  NIFTY {m([x['benchmark'].get('total_return_pct') for x in fs]):>7}  "
              f"atr {m([x['arms']['A_atr_risk'].get('total_return_pct') for x in fs]):>7}  "
              f"eqwt {m([x['arms']['B_equal_weight'].get('total_return_pct') for x in fs]):>7}  "
              f"ivol {m([x['arms']['C_inverse_vol'].get('total_return_pct') for x in fs]):>7}")
    print("="*126)
    OUT.mkdir(parents=True, exist_ok=True)
    p=OUT/"portfolio.json"; n=1
    while p.exists(): n+=1; p=OUT/f"portfolio_{n}.json"
    p.write_text(json.dumps(rec, indent=2, default=str)); print(f"written: {p}")
    db.close()

if __name__ == "__main__":
    main()
