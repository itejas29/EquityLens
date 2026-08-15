"""Walk-forward parameter search for the EquityLens strategy.

Reports OUT-OF-SAMPLE results only. In-sample numbers are shown alongside
purely so the overfitting gap is visible.

Usage:
    python scripts/optimize_strategy.py                    # full search
    python scripts/optimize_strategy.py --quick            # small grid, 1 fold
    python scripts/optimize_strategy.py --attribution      # drag breakdown only
"""

import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal  # noqa: E402
from app.core.strategy_params import StrategyParams  # noqa: E402
from app.services.optimizer import build_grid, walk_forward  # noqa: E402
from app.services.backtest import BacktestConfig, run_backtest  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

START = date(2021, 10, 1)
END = date(2026, 8, 1)
CAPITAL = 500_000


def _row(label: str, m: dict) -> str:
    return (
        f"  {label:<34} "
        f"ret {str(m.get('total_return_pct')):>8}%  "
        f"CAGR {str(m.get('cagr_pct')):>7}%  "
        f"Sharpe {str(m.get('sharpe_ratio')):>7}  "
        f"maxDD {str(m.get('max_drawdown_pct')):>7}%  "
        f"trades {str(m.get('total_trades')):>4}  "
        f"deployed {str(m.get('avg_capital_deployed_pct')):>6}%"
    )


def attribution(db) -> None:
    """Isolate each structural drag by changing one knob at a time."""
    variants = {
        "live defaults": StrategyParams.for_appetite("moderate"),
        "no support stop": StrategyParams.for_appetite("moderate").__class__(
            **{**StrategyParams.for_appetite("moderate").__dict__, "use_support_stop": False}
        ),
        "wider stop (4 ATR)": StrategyParams.for_appetite("moderate").__class__(
            **{**StrategyParams.for_appetite("moderate").__dict__, "atr_stop_multiplier": 4.0}
        ),
        "hold 180d": StrategyParams.for_appetite("moderate").__class__(
            **{**StrategyParams.for_appetite("moderate").__dict__, "horizon_days": 180}
        ),
        "quarterly rebalance": StrategyParams.for_appetite("moderate").__class__(
            **{**StrategyParams.for_appetite("moderate").__dict__, "rebalance_frequency": "quarterly"}
        ),
        "20 stocks": StrategyParams.for_appetite("moderate").__class__(
            **{**StrategyParams.for_appetite("moderate").__dict__, "max_stocks": 20}
        ),
    }

    print("\n" + "=" * 120)
    print(f"ATTRIBUTION — one knob at a time, {START} to {END}")
    print("=" * 120)

    bench_printed = False
    for label, params in variants.items():
        cfg = BacktestConfig(
            start_date=START, end_date=END, initial_capital=CAPITAL, risk_appetite="moderate",
            horizon_days=params.horizon_days, rebalance_frequency=params.rebalance_frequency, params=params,
        )
        r = run_backtest(db, cfg)
        if not bench_printed:
            print(_row("NIFTY 50 buy & hold", r.benchmark_metrics))
            print("  " + "-" * 116)
            bench_printed = True
        print(_row(label, r.metrics))
    print("=" * 120)


def main() -> None:
    db = SessionLocal()
    try:
        if "--attribution" in sys.argv:
            attribution(db)
            return

        quick = "--quick" in sys.argv
        grid = (
            build_grid(
                stop_multipliers=(2.0, 4.0),
                use_support_stop=(True, False),
                cash_buffers=(0.0,),
                horizons=(90, 180),
                max_stocks=(10,),
                min_scores=(60.0,),
            )
            if quick
            else build_grid(
                stop_multipliers=(1.5, 2.0, 3.0, 4.0, 5.0),
                use_support_stop=(True, False),
                cash_buffers=(0.0,),
                horizons=(90, 180),
                max_stocks=(10, 20),
                min_scores=(60.0,),
            )
        )

        print(f"\nGrid: {len(grid)} configurations")
        result = walk_forward(
            db, START, END, grid=grid,
            train_months=18, test_months=6, capital=CAPITAL,
        )

        print("\n" + "=" * 120)
        print("WALK-FORWARD RESULTS — out-of-sample is the answer; in-sample shown only to expose the gap")
        print("=" * 120)
        for f in result.folds:
            print(f"\nFold {f.fold}: train {f.train_start}..{f.train_end}  ->  test {f.test_start}..{f.test_end}")
            print(f"  winner: {f.best_label}")
            print(_row("in-sample (selected on this)", f.in_sample_metrics))
            print(_row("OUT-OF-SAMPLE", f.out_of_sample_metrics))
            print(_row("NIFTY 50 same window", f.benchmark_metrics))

        print("\n" + "=" * 120)
        print("SUMMARY")
        print("=" * 120)
        for k, v in result.summary().items():
            print(f"  {k:<36} {v}")
        print("=" * 120)
    finally:
        db.close()


if __name__ == "__main__":
    main()
