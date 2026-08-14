from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.cache import get_backtest_cache, hash_backtest_config, set_backtest_cache
from app.core.database import get_db
from app.core.exceptions import AppError
from app.core.rate_limit import rate_limit_analysis
from app.models.backtest import Backtest
from app.schemas.backtest import BacktestRequest, BacktestResponse
from app.services.backtest import BacktestConfig, run_backtest

router = APIRouter(prefix="/backtest", tags=["backtest"])


def _to_response(backtest_id: int, config: dict, results: dict) -> BacktestResponse:
    return BacktestResponse(
        id=backtest_id,
        config=BacktestRequest(**config),
        metrics=results["metrics"],
        benchmark_metrics=results["benchmark_metrics"],
        equity_curve=results["equity_curve"],
        benchmark_equity_curve=results["benchmark_equity_curve"],
        trade_log=results["trade_log"],
    )


@router.post(
    "", response_model=BacktestResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limit_analysis)]
)
def create_backtest(payload: BacktestRequest, db: Session = Depends(get_db)) -> BacktestResponse:
    config_json = payload.model_dump(mode="json")
    config_hash = hash_backtest_config(config_json)

    cached = get_backtest_cache(config_hash)
    if cached is not None:
        return _to_response(cached["id"], config_json, cached["results"])

    config = BacktestConfig(
        start_date=payload.start_date,
        end_date=payload.end_date,
        initial_capital=payload.initial_capital,
        risk_appetite=payload.risk_appetite,
        rebalance_frequency=payload.rebalance_frequency,
        horizon_days=payload.horizon_days,
        transaction_cost_pct=payload.transaction_cost_pct,
        slippage_pct=payload.slippage_pct,
        risk_free_rate=payload.risk_free_rate,
    )

    try:
        result = run_backtest(db, config)
    except ValueError as exc:
        raise AppError(str(exc), status_code=status.HTTP_400_BAD_REQUEST)

    results_json = {
        "metrics": result.metrics,
        "benchmark_metrics": result.benchmark_metrics,
        "equity_curve": [{"date": p["date"].isoformat(), "equity": p["equity"]} for p in result.equity_curve],
        "benchmark_equity_curve": [
            {"date": p["date"].isoformat(), "equity": p["equity"]} for p in result.benchmark_equity_curve
        ],
        "trade_log": [
            {
                "stock_id": t.stock_id,
                "symbol": t.symbol,
                "entry_date": t.entry_date.isoformat(),
                "exit_date": t.exit_date.isoformat(),
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "quantity": t.quantity,
                "pnl": t.pnl,
                "pnl_pct": t.pnl_pct,
                "exit_reason": t.exit_reason,
                "holding_days": t.holding_days,
            }
            for t in result.trade_log
        ],
    }
    backtest = Backtest(config=config_json, results=results_json)
    db.add(backtest)
    db.commit()
    db.refresh(backtest)

    set_backtest_cache(config_hash, {"id": backtest.id, "results": backtest.results})

    return _to_response(backtest.id, backtest.config, backtest.results)


@router.get("/{backtest_id}", response_model=BacktestResponse)
def get_backtest(backtest_id: int, db: Session = Depends(get_db)) -> BacktestResponse:
    backtest = db.query(Backtest).filter(Backtest.id == backtest_id).first()
    if backtest is None:
        raise AppError(f"Backtest {backtest_id} not found", status_code=status.HTTP_404_NOT_FOUND)
    return _to_response(backtest.id, backtest.config, backtest.results)
