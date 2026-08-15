"""Walk-forward parameter search over the backtest engine.

WHY WALK-FORWARD, AND NOT A PLAIN SWEEP. Running 60 parameter sets over one
window and keeping the best is not a test — it is a selection. The best of 60
configurations will look good on any single window by chance alone, and the
number it produces is unreproducible out of sample. That is how a strategy ends
up "beating NIFTY" in a backtest and losing money live.

So the search runs in folds. Each fold optimises on an IN-SAMPLE window, then
takes the single configuration that won there and evaluates it, untouched, on
the immediately following OUT-OF-SAMPLE window. Only the out-of-sample results
are reported as the answer. In-sample numbers are kept for comparison, because
the gap between them is itself the finding: a large gap means the search is
fitting noise, and that is worth knowing.

OBJECTIVE. Configurations are ranked on risk-adjusted return, not raw return.
Transaction costs are already inside the equity curve the backtest produces, so
they are not subtracted again — double-counting them would bias the search
toward doing nothing. A drawdown penalty and an optional explicit turnover
penalty sit on top, for the case where two configurations earn the same return
but one churns far more to get it.
"""

import logging
from dataclasses import dataclass, field, replace
from datetime import date as date_type
from datetime import timedelta
from itertools import product

from sqlalchemy.orm import Session

from app.core.strategy_params import StrategyParams
from app.services.backtest import BacktestConfig, run_backtest

logger = logging.getLogger(__name__)


@dataclass
class FoldResult:
    fold: int
    train_start: date_type
    train_end: date_type
    test_start: date_type
    test_end: date_type
    best_label: str
    best_params: StrategyParams
    in_sample_score: float
    in_sample_metrics: dict
    out_of_sample_score: float
    out_of_sample_metrics: dict
    benchmark_metrics: dict
    candidates_evaluated: int


@dataclass
class OptimizationResult:
    folds: list[FoldResult] = field(default_factory=list)
    baseline_out_of_sample: list[dict] = field(default_factory=list)
    grid_size: int = 0

    def summary(self) -> dict:
        """Aggregate out-of-sample performance vs the benchmark and the
        unoptimised baseline. This is the only part that answers the question.
        """
        if not self.folds:
            return {}

        def _mean(values: list[float | None]) -> float | None:
            clean = [v for v in values if v is not None]
            return round(sum(clean) / len(clean), 3) if clean else None

        oos = [f.out_of_sample_metrics for f in self.folds]
        bench = [f.benchmark_metrics for f in self.folds]

        beat = sum(
            1
            for f in self.folds
            if (f.out_of_sample_metrics.get("total_return_pct") or 0)
            > (f.benchmark_metrics.get("total_return_pct") or 0)
        )

        return {
            "folds": len(self.folds),
            "grid_size": self.grid_size,
            "optimised_oos_return_pct": _mean([m.get("total_return_pct") for m in oos]),
            "optimised_oos_sharpe": _mean([m.get("sharpe_ratio") for m in oos]),
            "optimised_oos_max_drawdown_pct": _mean([m.get("max_drawdown_pct") for m in oos]),
            "benchmark_return_pct": _mean([m.get("total_return_pct") for m in bench]),
            "benchmark_sharpe": _mean([m.get("sharpe_ratio") for m in bench]),
            "baseline_oos_return_pct": _mean([m.get("total_return_pct") for m in self.baseline_out_of_sample]),
            "folds_beating_benchmark": f"{beat}/{len(self.folds)}",
            # In-sample minus out-of-sample. A large positive gap is the
            # signature of a search that fitted noise.
            "mean_in_sample_score": _mean([f.in_sample_score for f in self.folds]),
            "mean_out_of_sample_score": _mean([f.out_of_sample_score for f in self.folds]),
        }


def objective(metrics: dict, drawdown_penalty: float = 0.5, turnover_penalty: float = 0.0) -> float:
    """Rank configurations on risk-adjusted, post-cost performance.

    Sharpe is the base term because it already divides return by the volatility
    taken to earn it. Costs are NOT subtracted here — they are inside the equity
    curve Sharpe is computed from, and subtracting again would double-charge.
    """
    sharpe = metrics.get("sharpe_ratio")
    if sharpe is None:
        return float("-inf")

    max_dd = abs(metrics.get("max_drawdown_pct") or 0.0) / 100.0
    trades = metrics.get("total_trades") or 0

    score = float(sharpe) - drawdown_penalty * max_dd
    if turnover_penalty:
        score -= turnover_penalty * trades
    return score


def build_grid(
    stop_multipliers=(1.5, 2.0, 3.0, 4.0, 5.0),
    use_support_stop=(True, False),
    cash_buffers=(0.0, 0.10),
    horizons=(60, 90, 180),
    rebalance=("monthly",),
    max_stocks=(10, 20),
    min_scores=(55.0, 60.0),
    risk_rewards=(2.0,),
) -> list[StrategyParams]:
    """Cartesian grid. Kept deliberately small — every extra axis multiplies
    both runtime and the number of chances the search has to get lucky.
    """
    base = StrategyParams.for_appetite("moderate")
    grid = []
    for stop, sup, cash, horizon, freq, n, min_score, rr in product(
        stop_multipliers, use_support_stop, cash_buffers, horizons, rebalance, max_stocks, min_scores, risk_rewards
    ):
        grid.append(
            replace(
                base,
                atr_stop_multiplier=stop,
                use_support_stop=sup,
                cash_buffer_pct=cash,
                horizon_days=horizon,
                rebalance_frequency=freq,
                max_stocks=n,
                min_score=min_score,
                risk_reward_ratio=rr,
            )
        )
    return grid


def _run(db: Session, params: StrategyParams, start: date_type, end: date_type, capital: float, cache=None):
    config = BacktestConfig(
        start_date=start,
        end_date=end,
        initial_capital=capital,
        risk_appetite="moderate",
        horizon_days=params.horizon_days,
        rebalance_frequency=params.rebalance_frequency,
        params=params,
        indicator_cache=cache,
    )
    return run_backtest(db, config)


def walk_forward(
    db: Session,
    start: date_type,
    end: date_type,
    grid: list[StrategyParams] | None = None,
    train_months: int = 18,
    test_months: int = 6,
    capital: float = 500_000,
    drawdown_penalty: float = 0.5,
    turnover_penalty: float = 0.0,
) -> OptimizationResult:
    """Roll a train/test pair forward across the available history.

    Folds are non-overlapping in their test windows, so the concatenated
    out-of-sample record reads as one continuous experiment.
    """
    grid = grid or build_grid()
    result = OptimizationResult(grid_size=len(grid))

    fold = 0
    train_start = start
    while True:
        train_end = train_start + timedelta(days=int(train_months * 30.44))
        test_end = train_end + timedelta(days=int(test_months * 30.44))
        if test_end > end:
            break
        fold += 1

        logger.info(
            "Fold %d — train %s..%s, test %s..%s, %d candidates",
            fold, train_start, train_end, train_end, test_end, len(grid),
        )

        # One cache per window. Rebalance dates depend on the frequency knob, so
        # different configs touch different dates — the cache simply fills in.
        train_cache: dict = {}
        test_cache: dict = {}
        best_params = None
        best_score = float("-inf")
        best_metrics: dict = {}
        for i, params in enumerate(grid, start=1):
            try:
                r = _run(db, params, train_start, train_end, capital, train_cache)
            except Exception as exc:
                logger.warning("  candidate %d failed: %s", i, exc)
                continue
            score = objective(r.metrics, drawdown_penalty, turnover_penalty)
            if score > best_score:
                best_score, best_params, best_metrics = score, params, r.metrics
            if i % 20 == 0:
                logger.info("    evaluated %d/%d", i, len(grid))

        if best_params is None:
            logger.warning("Fold %d produced no usable candidate", fold)
            train_start = train_end
            continue

        # The winner is evaluated ONCE on the untouched next window.
        oos = _run(db, best_params, train_end, test_end, capital, test_cache)
        oos_score = objective(oos.metrics, drawdown_penalty, turnover_penalty)

        # The same window under the unoptimised live defaults, so the search can
        # be judged against "changing nothing" rather than only against NIFTY.
        baseline = _run(db, StrategyParams.for_appetite("moderate"), train_end, test_end, capital, test_cache)
        result.baseline_out_of_sample.append(baseline.metrics)

        result.folds.append(
            FoldResult(
                fold=fold,
                train_start=train_start,
                train_end=train_end,
                test_start=train_end,
                test_end=test_end,
                best_label=best_params.label(),
                best_params=best_params,
                in_sample_score=round(best_score, 4),
                in_sample_metrics=best_metrics,
                out_of_sample_score=round(oos_score, 4),
                out_of_sample_metrics=oos.metrics,
                benchmark_metrics=oos.benchmark_metrics,
                candidates_evaluated=len(grid),
            )
        )

        logger.info(
            "  fold %d best=%s  in-sample %.3f -> out-of-sample %.3f",
            fold, best_params.label(), best_score, oos_score,
        )

        train_start = train_end  # roll forward; test windows never overlap

    return result
