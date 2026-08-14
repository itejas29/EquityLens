"""Backtest simulation loop.

POINT-IN-TIME CORRECTNESS: raw OHLCV rows (price_history, benchmark) are
loaded once per run — that's immutable historical fact, not a derived
aggregate, so loading it upfront carries no look-ahead risk. What matters is
that every INDICATOR, SCORE, and LEVEL is recomputed from scratch at each
rebalance date using only rows filtered to `date <= that date`
(app.services.backtest_scoring.compute_point_in_time_universe does this).
Nothing computed using the full history is ever reused across iterations —
only the raw, unprocessed rows are shared.
"""

from dataclasses import dataclass, field
from datetime import date as date_type

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.core.backtest_config import (
    DEFAULT_HORIZON_DAYS,
    DEFAULT_REBALANCE_FREQUENCY,
    DEFAULT_RISK_FREE_RATE,
    DEFAULT_SLIPPAGE_PCT,
    DEFAULT_TRANSACTION_COST_PCT,
    TRADING_DAYS_PER_YEAR,
)
from app.core.portfolio_config import MAX_ALLOCATION_PCT, MAX_STOCKS_PER_SECTOR, MAX_STOCKS_PREFERENCE, MIN_SCORE_THRESHOLD
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.services.backtest_scoring import compute_point_in_time_universe
from app.services.market_data import fetch_price_history
from app.services.position_sizing import size_position


@dataclass
class BacktestConfig:
    start_date: date_type
    end_date: date_type
    initial_capital: float
    risk_appetite: str
    rebalance_frequency: str = DEFAULT_REBALANCE_FREQUENCY
    horizon_days: int = DEFAULT_HORIZON_DAYS
    transaction_cost_pct: float = DEFAULT_TRANSACTION_COST_PCT
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE


@dataclass
class OpenPosition:
    stock_id: int
    symbol: str
    sector: str | None
    quantity: int
    entry_price: float
    entry_date: date_type
    stop_loss: float
    target_price: float


@dataclass
class Trade:
    stock_id: int
    symbol: str
    entry_date: date_type
    exit_date: date_type
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    exit_reason: str
    holding_days: int


@dataclass
class BacktestResult:
    equity_curve: list[dict] = field(default_factory=list)
    benchmark_equity_curve: list[dict] = field(default_factory=list)
    trade_log: list[Trade] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    benchmark_metrics: dict = field(default_factory=dict)


def _buy_fill(close: float, slippage_pct: float) -> float:
    return close * (1 + slippage_pct)


def _sell_fill(price: float, slippage_pct: float) -> float:
    return price * (1 - slippage_pct)


def _half_cost(fill_price: float, quantity: int, transaction_cost_pct: float) -> float:
    # transaction_cost_pct is a round-trip figure; half applies per leg.
    return fill_price * quantity * (transaction_cost_pct / 2)


def _rebalance_dates(trading_calendar: list[date_type], frequency: str) -> list[date_type]:
    if not trading_calendar:
        return []
    seen_periods: set[tuple[int, int]] = set()
    dates: list[date_type] = []
    for d in trading_calendar:
        period = (d.year, d.month) if frequency == "monthly" else (d.year, (d.month - 1) // 3)
        if period not in seen_periods:
            seen_periods.add(period)
            dates.append(d)
    return dates


def _load_ohlcv_df(db: Session, stock_id: int) -> pd.DataFrame:
    rows = (
        db.query(
            PriceHistory.date,
            PriceHistory.open,
            PriceHistory.high,
            PriceHistory.low,
            PriceHistory.close,
            PriceHistory.volume,
        )
        .filter(PriceHistory.stock_id == stock_id)
        .order_by(PriceHistory.date)
        .all()
    )
    return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])


def _load_all_price_frames(db: Session, stocks: list[Stock]) -> dict[int, pd.DataFrame]:
    return {stock.id: _load_ohlcv_df(db, stock.id) for stock in stocks}


def _select_new_entries(
    snapshot: dict,
    stocks_by_id: dict[int, Stock],
    held_ids: set[int],
    held_sector_counts: dict[str, int],
    current_equity: float,
    available_cash: float,
    config: BacktestConfig,
    max_new: int,
) -> list[dict]:
    min_score = MIN_SCORE_THRESHOLD[config.risk_appetite]
    max_allocation_per_stock = current_equity * MAX_ALLOCATION_PCT[config.risk_appetite]

    candidates = [
        snap
        for sid, snap in snapshot.items()
        if sid not in held_ids and snap.overall_score is not None and snap.overall_score >= min_score and snap.levels is not None
    ]
    candidates.sort(key=lambda s: s.overall_score, reverse=True)

    sector_counts = dict(held_sector_counts)
    remaining_cash = available_cash
    entries: list[dict] = []

    for snap in candidates:
        if len(entries) >= max_new or remaining_cash <= 0:
            break
        stock = stocks_by_id[snap.stock_id]
        sector = stock.sector or "Unknown"
        if sector_counts.get(sector, 0) >= MAX_STOCKS_PER_SECTOR:
            continue

        position = size_position(
            entry=snap.levels.entry_high,
            stop_loss=snap.levels.stop_loss,
            capital=current_equity,
            risk_appetite=config.risk_appetite,
            max_allocation_per_stock=min(max_allocation_per_stock, remaining_cash),
        )
        if not position.is_valid:
            continue

        entries.append(
            {
                "stock_id": snap.stock_id,
                "symbol": stock.symbol,
                "sector": stock.sector,
                "shares": position.shares,
                "entry_reference_price": snap.levels.entry_high,
                "stop_loss": snap.levels.stop_loss,
                "target_price": snap.levels.target_price,
            }
        )
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        remaining_cash -= position.allocated_amount

    return entries


def _equity_metrics(equity_curve: list[dict], initial_capital: float, risk_free_rate: float) -> dict:
    if not equity_curve or initial_capital <= 0:
        return {}

    df = pd.DataFrame(equity_curve)
    equity = df["equity"].astype(float)
    final_equity = float(equity.iloc[-1])
    total_return = final_equity / initial_capital - 1

    n_days = (df["date"].iloc[-1] - df["date"].iloc[0]).days
    years = n_days / 365.25 if n_days > 0 else None
    cagr = (final_equity / initial_capital) ** (1 / years) - 1 if years and years > 0 and final_equity > 0 else None

    daily_returns = equity.pct_change(fill_method=None).dropna()
    sharpe = sortino = None
    if len(daily_returns) > 1:
        ann_return = daily_returns.mean() * TRADING_DAYS_PER_YEAR
        ann_vol = daily_returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR)
        if ann_vol > 0:
            sharpe = (ann_return - risk_free_rate) / ann_vol
        downside = daily_returns[daily_returns < 0]
        if len(downside) > 1:
            downside_vol = downside.std() * np.sqrt(TRADING_DAYS_PER_YEAR)
            if downside_vol > 0:
                sortino = (ann_return - risk_free_rate) / downside_vol

    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    max_dd = float(drawdown.min())

    max_duration, current_duration = 0, 0
    for in_drawdown in (drawdown < 0).tolist():
        if in_drawdown:
            current_duration += 1
            max_duration = max(max_duration, current_duration)
        else:
            current_duration = 0

    return {
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2) if cagr is not None else None,
        "sharpe_ratio": round(float(sharpe), 3) if sharpe is not None else None,
        "sortino_ratio": round(float(sortino), 3) if sortino is not None else None,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "max_drawdown_duration_days": max_duration,
        "final_equity": round(final_equity, 2),
    }


def _trade_metrics(trade_log: list[Trade]) -> dict:
    if not trade_log:
        return {
            "num_trades": 0,
            "win_rate_pct": None,
            "avg_win": None,
            "avg_loss": None,
            "profit_factor": None,
            "avg_holding_period_days": None,
        }

    pnls = [t.pnl for t in trade_log]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    return {
        "num_trades": len(trade_log),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
        "avg_holding_period_days": round(sum(t.holding_days for t in trade_log) / len(trade_log), 1),
    }


def _benchmark_buy_and_hold(
    benchmark_df: pd.DataFrame, start_date: date_type, end_date: date_type, initial_capital: float, risk_free_rate: float
) -> tuple[list[dict], dict]:
    window = (
        benchmark_df[(benchmark_df["date"] >= start_date) & (benchmark_df["date"] <= end_date)]
        .sort_values("date")
        .reset_index(drop=True)
    )
    if window.empty:
        return [], {}

    start_price = float(window["close"].iloc[0])
    shares = initial_capital / start_price
    curve = [{"date": row.date, "equity": round(shares * float(row.close), 2)} for row in window.itertuples(index=False)]
    metrics = _equity_metrics(curve, initial_capital, risk_free_rate)
    return curve, metrics


def run_backtest(db: Session, config: BacktestConfig) -> BacktestResult:
    stocks = db.query(Stock).filter(Stock.is_active == True).order_by(Stock.symbol).all()  # noqa: E712
    stocks_by_id = {s.id: s for s in stocks}
    raw_frames = _load_all_price_frames(db, stocks)
    indexed_frames = {sid: df.set_index("date") for sid, df in raw_frames.items()}

    benchmark_df = fetch_price_history("^NSEI", period="2y")[["date", "close"]]
    trading_calendar = sorted(d for d in benchmark_df["date"] if config.start_date <= d <= config.end_date)
    if not trading_calendar:
        raise ValueError("No trading days available in the given date range")

    rebalance_dates = set(_rebalance_dates(trading_calendar, config.rebalance_frequency))

    cash = config.initial_capital
    open_positions: list[OpenPosition] = []
    trade_log: list[Trade] = []
    equity_curve: list[dict] = []
    last_known_close: dict[int, float] = {}

    for today in trading_calendar:
        # --- exits: stop / target / horizon, checked every day ---
        still_open: list[OpenPosition] = []
        for pos in open_positions:
            frame = indexed_frames.get(pos.stock_id)
            row = frame.loc[today] if frame is not None and today in frame.index else None
            if row is None or pd.isna(row["close"]):
                still_open.append(pos)  # no data today — hold, can't evaluate triggers
                continue

            close = float(row["close"])
            low = float(row["low"]) if pd.notna(row["low"]) else None
            high = float(row["high"]) if pd.notna(row["high"]) else None
            last_known_close[pos.stock_id] = close

            exit_price, reason = None, None
            if low is not None and low <= pos.stop_loss:
                exit_price, reason = pos.stop_loss, "stop"
            elif high is not None and high >= pos.target_price:
                exit_price, reason = pos.target_price, "target"
            elif (today - pos.entry_date).days >= config.horizon_days:
                exit_price, reason = close, "horizon"

            if exit_price is None:
                still_open.append(pos)
                continue

            fill = _sell_fill(exit_price, config.slippage_pct)
            sell_cost = _half_cost(fill, pos.quantity, config.transaction_cost_pct)
            cash += fill * pos.quantity - sell_cost
            effective_exit = fill - sell_cost / pos.quantity
            pnl = (effective_exit - pos.entry_price) * pos.quantity
            trade_log.append(
                Trade(
                    stock_id=pos.stock_id,
                    symbol=pos.symbol,
                    entry_date=pos.entry_date,
                    exit_date=today,
                    entry_price=round(pos.entry_price, 2),
                    exit_price=round(effective_exit, 2),
                    quantity=pos.quantity,
                    pnl=round(pnl, 2),
                    pnl_pct=round((effective_exit / pos.entry_price - 1) * 100, 2),
                    exit_reason=reason,
                    holding_days=(today - pos.entry_date).days,
                )
            )
        open_positions = still_open

        # --- rebalance: score point-in-time, enter new positions ---
        if today in rebalance_dates:
            bounded_frames = {sid: df[df["date"] <= today] for sid, df in raw_frames.items()}
            bounded_bench = benchmark_df[benchmark_df["date"] <= today]
            snapshot = compute_point_in_time_universe(bounded_frames, bounded_bench)

            held_ids = {p.stock_id for p in open_positions}
            held_sector_counts: dict[str, int] = {}
            for p in open_positions:
                sec = p.sector or "Unknown"
                held_sector_counts[sec] = held_sector_counts.get(sec, 0) + 1

            current_equity = cash + sum(
                p.quantity * last_known_close.get(p.stock_id, p.entry_price) for p in open_positions
            )
            max_new = MAX_STOCKS_PREFERENCE - len(open_positions)
            if max_new > 0:
                new_entries = _select_new_entries(
                    snapshot, stocks_by_id, held_ids, held_sector_counts, current_equity, cash, config, max_new
                )
                for e in new_entries:
                    fill = _buy_fill(e["entry_reference_price"], config.slippage_pct)
                    buy_cost = _half_cost(fill, e["shares"], config.transaction_cost_pct)
                    cash -= fill * e["shares"] + buy_cost
                    effective_entry = fill + buy_cost / e["shares"]
                    open_positions.append(
                        OpenPosition(
                            stock_id=e["stock_id"],
                            symbol=e["symbol"],
                            sector=e["sector"],
                            quantity=e["shares"],
                            entry_price=effective_entry,
                            entry_date=today,
                            stop_loss=e["stop_loss"],
                            target_price=e["target_price"],
                        )
                    )
                    last_known_close[e["stock_id"]] = e["entry_reference_price"]

        # --- daily mark-to-market ---
        equity_today = cash + sum(
            p.quantity * last_known_close.get(p.stock_id, p.entry_price) for p in open_positions
        )
        equity_curve.append({"date": today, "equity": round(equity_today, 2)})

    # close anything still open at the last known price so the trade log is complete
    final_date = trading_calendar[-1]
    for pos in open_positions:
        exit_price = last_known_close.get(pos.stock_id, pos.entry_price)
        fill = _sell_fill(exit_price, config.slippage_pct)
        sell_cost = _half_cost(fill, pos.quantity, config.transaction_cost_pct)
        cash += fill * pos.quantity - sell_cost
        effective_exit = fill - sell_cost / pos.quantity
        pnl = (effective_exit - pos.entry_price) * pos.quantity
        trade_log.append(
            Trade(
                stock_id=pos.stock_id,
                symbol=pos.symbol,
                entry_date=pos.entry_date,
                exit_date=final_date,
                entry_price=round(pos.entry_price, 2),
                exit_price=round(effective_exit, 2),
                quantity=pos.quantity,
                pnl=round(pnl, 2),
                pnl_pct=round((effective_exit / pos.entry_price - 1) * 100, 2),
                exit_reason="end_of_backtest",
                holding_days=(final_date - pos.entry_date).days,
            )
        )

    metrics = {**_equity_metrics(equity_curve, config.initial_capital, config.risk_free_rate), **_trade_metrics(trade_log)}
    benchmark_curve, benchmark_metrics = _benchmark_buy_and_hold(
        benchmark_df, config.start_date, config.end_date, config.initial_capital, config.risk_free_rate
    )

    return BacktestResult(
        equity_curve=equity_curve,
        benchmark_equity_curve=benchmark_curve,
        trade_log=trade_log,
        metrics=metrics,
        benchmark_metrics=benchmark_metrics,
    )
