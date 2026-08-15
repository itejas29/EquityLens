"""Phase 0/10 — freeze the current baseline and compare the four strategies.

    A  current      2 ATR, support stop ON,  no regime filter
    B  wide stop    4 ATR, support stop OFF, no regime filter
    C  wide+regime  4 ATR, support stop OFF, NIFTY 200DMA regime filter
    D  NIFTY 50     fully invested benchmark

Run over identical windows so the attribution reads cleanly:
    current -> effect of the stop change -> effect of the regime filter -> NIFTY

Results are written to docs/experiments/ as versioned JSON. Nothing is ever
overwritten: each run lands under its own version key, so a later experiment
cannot quietly replace an earlier one.

Usage:
    python scripts/compare_strategies.py                 # full history
    python scripts/compare_strategies.py --bear-sweep    # sweep bear exposure
"""

import json
import logging
import sys
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal  # noqa: E402
from app.core.strategy_params import StrategyParams  # noqa: E402
from app.services.backtest import BacktestConfig, run_backtest  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

START = date(2021, 10, 1)
END = date(2026, 8, 1)
CAPITAL = 500_000
OUT_DIR = Path(__file__).resolve().parents[2] / "docs" / "experiments"

BASE = StrategyParams.for_appetite("moderate")

VARIANTS = {
    "A_current": replace(BASE, cash_buffer_pct=0.0),
    "B_wide_stop": replace(BASE, atr_stop_multiplier=4.0, use_support_stop=False, cash_buffer_pct=0.0),
    "C_wide_stop_regime": replace(
        BASE, atr_stop_multiplier=4.0, use_support_stop=False, cash_buffer_pct=0.0,
        use_regime_filter=True, bull_exposure=1.0, bear_exposure=0.25,
    ),
}


def _fmt(label: str, m: dict) -> str:
    return (
        f"  {label:<22} "
        f"ret {str(m.get('total_return_pct')):>8}%  "
        f"CAGR {str(m.get('cagr_pct')):>7}%  "
        f"Sharpe {str(m.get('sharpe_ratio')):>7}  "
        f"maxDD {str(m.get('max_drawdown_pct')):>7}%  "
        f"up {str(m.get('upside_capture_pct')):>7}%  "
        f"down {str(m.get('downside_capture_pct')):>8}%  "
        f"depl {str(m.get('avg_capital_deployed_pct')):>6}%"
    )


def _run(db, params, cache):
    cfg = BacktestConfig(
        start_date=START, end_date=END, initial_capital=CAPITAL, risk_appetite="moderate",
        horizon_days=params.horizon_days, rebalance_frequency=params.rebalance_frequency,
        params=params, indicator_cache=cache,
    )
    return run_backtest(db, cfg)


def main() -> None:
    db = SessionLocal()
    cache: dict = {}
    record: dict = {"window": [str(START), str(END)], "capital": CAPITAL, "variants": {}}

    try:
        variants = dict(VARIANTS)
        if "--bear-sweep" in sys.argv:
            # Phase 5 — is full cash actually necessary, or does partial work?
            for bear in (0.0, 0.25, 0.5, 0.75):
                variants[f"C_regime_bear{int(bear*100)}"] = replace(
                    BASE, atr_stop_multiplier=4.0, use_support_stop=False, cash_buffer_pct=0.0,
                    use_regime_filter=True, bear_exposure=bear,
                )
            variants.pop("C_wide_stop_regime", None)

        print("\n" + "=" * 132)
        print(f"STRATEGY COMPARISON  {START} .. {END}   (capture: up>100 good, down<100 good)")
        print("=" * 132)

        benchmark_printed = False
        for name, params in variants.items():
            result = _run(db, params, cache)
            if not benchmark_printed:
                print(_fmt("D_NIFTY50", result.benchmark_metrics))
                record["variants"]["D_NIFTY50"] = {"params": None, "metrics": result.benchmark_metrics}
                print("  " + "-" * 128)
                benchmark_printed = True
            print(_fmt(name, result.metrics))
            record["variants"][name] = {
                "params": {k: v for k, v in asdict(params).items()},
                "label": params.label(),
                "metrics": result.metrics,
            }
        print("=" * 132)

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        version = "v3_regime" if "--bear-sweep" not in sys.argv else "v3_regime_bear_sweep"
        path = OUT_DIR / f"{version}.json"
        # Never overwrite: a repeated version gets a numbered suffix so prior
        # evidence survives.
        n = 1
        while path.exists():
            n += 1
            path = OUT_DIR / f"{version}_{n}.json"
        path.write_text(json.dumps(record, indent=2, default=str))
        print(f"\nwritten: {path.relative_to(Path(__file__).resolve().parents[2])}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
