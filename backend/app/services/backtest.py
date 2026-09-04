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

from dataclasses import dataclass, field, replace
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
from app.core.universe_config import HISTORY_PERIOD
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
    # Injected strategy knobs. None -> the live per-appetite defaults, so an
    # unparameterised backtest reproduces exactly what it did before.
    params: object = None
    # Caller-owned {as_of_date: indicator snapshot}. Shared across a parameter
    # sweep so the expensive indicator pass runs once per date, not per config.
    # NOTE: keyed by date alone, with no reference to which stocks were in
    # scope, so a cache may only be shared between runs whose universe is
    # identical. Sharing one across differing universes silently gives every
    # later run the first run's universe.
    indicator_cache: dict | None = None
    # Explicit universe, bypassing both the is_active query and universe_top_n.
    # Exists so a study can compare universe CONSTRUCTIONS (point-in-time vs
    # current liquidity, different measurement windows, random subsets) rather
    # than only sizes — universe_top_n can rank exactly one way, at one date.
    # None -> unchanged behaviour.
    universe_stock_ids: list[int] | None = None


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
    p = config.params
    min_score = p.min_score
    max_allocation_per_stock = current_equity * p.max_allocation_pct

    # `snap.trend_ok` is the Phase 17 recent-trend entry gate. It sits here and
    # nowhere else on purpose: this is the only place a NEW position is opened,
    # so gating here cannot reorder the ranking, cannot force an exit, and
    # cannot affect a stock already held. Defaults True when the gate is off.
    candidates = [
        snap
        for sid, snap in snapshot.items()
        if sid not in held_ids and snap.overall_score is not None and snap.overall_score >= min_score
        and snap.levels is not None and snap.trend_ok
    ]
    candidates.sort(key=lambda s: s.overall_score, reverse=True)

    sector_counts = dict(held_sector_counts)
    entries: list[dict] = []

    # --- 1) pick the slate ------------------------------------------------
    # Selection is separated from sizing so every sizing method chooses the
    # SAME stocks. Only the rupee allocation differs, which is what makes the
    # Phase 15 attribution clean.
    slate = []
    for snap in candidates:
        if len(slate) >= max_new:
            break
        stock = stocks_by_id[snap.stock_id]
        sector = stock.sector or "Unknown"
        if sector_counts.get(sector, 0) >= p.max_stocks_per_sector:
            continue
        slate.append(snap)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1

    if not slate:
        return entries

    # --- 2) target rupee allocation per name ------------------------------
    method = getattr(p, "sizing_method", "atr_risk")
    targets: dict[int, float] = {}

    if method == "equal_weight":
        # One slot per name out of max_stocks, so a full book is fully invested
        # regardless of how volatile the names happen to be.
        per_slot = current_equity / max(1, p.max_stocks)
        targets = {snap.stock_id: per_slot for snap in slate}

    elif method == "inverse_vol":
        # Weight by 1/vol, normalised across the slate, then scaled to the same
        # total the equal-weight book would have deployed — so the two differ in
        # DISTRIBUTION, not in gross exposure, per the spec's fairness rule.
        inv = {}
        for snap in slate:
            vol = snap.volatility
            if vol is None or vol <= 0:
                vol = 0.30  # fallback only when vol is genuinely unavailable
            inv[snap.stock_id] = 1.0 / vol
        total_inv = sum(inv.values())
        budget = current_equity * (len(slate) / max(1, p.max_stocks))
        targets = {sid: budget * (w / total_inv) for sid, w in inv.items()}

    # --- 3) convert to shares, honouring the same caps for every method ----
    remaining_cash = available_cash
    for snap in slate:
        if remaining_cash <= 0:
            break
        stock = stocks_by_id[snap.stock_id]

        if method == "atr_risk":
            position = size_position(
                entry=snap.levels.entry_high,
                stop_loss=snap.levels.stop_loss,
                capital=current_equity,
                risk_appetite=config.risk_appetite,
                risk_pct=p.risk_pct_per_trade,
                max_allocation_per_stock=min(max_allocation_per_stock, remaining_cash),
            )
            if not position.is_valid:
                continue
            shares, allocated = position.shares, position.allocated_amount
        else:
            entry = snap.levels.entry_high
            budget = min(targets.get(snap.stock_id, 0.0), max_allocation_per_stock, remaining_cash)
            shares = int(budget // entry) if entry > 0 else 0
            if shares <= 0:
                continue
            allocated = shares * entry

        entries.append(
            {
                "stock_id": snap.stock_id,
                "symbol": stock.symbol,
                "sector": stock.sector,
                "shares": shares,
                "entry_reference_price": snap.levels.entry_high,
                "stop_loss": snap.levels.stop_loss,
                "target_price": snap.levels.target_price,
            }
        )
        remaining_cash -= allocated

    return entries


def _capture_ratios(equity_curve: list[dict], benchmark_curve: list[dict]) -> dict:
    """Upside/downside capture against the benchmark.

    Standard definition: compound the strategy's daily returns over the days the
    BENCHMARK rose, and divide by the benchmark's compounded return over those
    same days; repeat for the days it fell. >100% downside capture means the
    strategy loses more than the index when the index falls, which is the exact
    failure mode the walk-forward identified (203%).

    Signs are handled explicitly: both numerator and denominator are negative on
    down days, so the ratio stays positive and "lower is better" holds.
    """
    if not equity_curve or not benchmark_curve:
        return {"upside_capture_pct": None, "downside_capture_pct": None}

    s_df = pd.DataFrame(equity_curve).set_index("date")["equity"].astype(float)
    b_df = pd.DataFrame(benchmark_curve).set_index("date")["equity"].astype(float)
    joined = pd.concat([s_df.rename("s"), b_df.rename("b")], axis=1).dropna()
    if len(joined) < 3:
        return {"upside_capture_pct": None, "downside_capture_pct": None}

    # Resampled to MONTHLY before computing capture. Compounding daily returns
    # over only-up days across several years produces enormous denominators
    # (the benchmark's up-days alone compound to several hundred percent), which
    # makes the ratio arithmetically correct but wildly unintuitive — a strategy
    # returning nearly as much as the index can show "9% upside capture".
    # Monthly is the industry-standard basis and keeps the number readable.
    idx = pd.to_datetime(pd.Series(joined.index))
    monthly = joined.set_index(pd.DatetimeIndex(idx)).resample("ME").last().dropna()
    if len(monthly) < 4:
        return {"upside_capture_pct": None, "downside_capture_pct": None}

    s_ret = monthly["s"].pct_change(fill_method=None).dropna()
    b_ret = monthly["b"].pct_change(fill_method=None).dropna()
    aligned = pd.concat([s_ret.rename("s"), b_ret.rename("b")], axis=1).dropna()

    def _capture(mask) -> float | None:
        sub = aligned[mask]
        if len(sub) < 2:
            return None
        s_comp = (1 + sub["s"]).prod() - 1
        b_comp = (1 + sub["b"]).prod() - 1
        if b_comp == 0:
            return None
        return round(s_comp / b_comp * 100, 2)

    return {
        "upside_capture_pct": _capture(aligned["b"] > 0),
        "downside_capture_pct": _capture(aligned["b"] < 0),
    }


def _relative_metrics(equity_curve: list[dict], benchmark_curve: list[dict]) -> dict:
    """Tracking error and information ratio, on monthly returns.

    Same monthly basis as the capture ratios so every benchmark-relative figure
    in a report is computed consistently.
    """
    if not equity_curve or not benchmark_curve:
        return {"tracking_error_pct": None, "information_ratio": None}
    s_df = pd.DataFrame(equity_curve).set_index("date")["equity"].astype(float)
    b_df = pd.DataFrame(benchmark_curve).set_index("date")["equity"].astype(float)
    joined = pd.concat([s_df.rename("s"), b_df.rename("b")], axis=1).dropna()
    if len(joined) < 4:
        return {"tracking_error_pct": None, "information_ratio": None}

    monthly = joined.set_index(pd.DatetimeIndex(pd.to_datetime(pd.Series(joined.index)))).resample("ME").last().dropna()
    if len(monthly) < 4:
        return {"tracking_error_pct": None, "information_ratio": None}

    active = (monthly["s"].pct_change(fill_method=None) - monthly["b"].pct_change(fill_method=None)).dropna()
    if len(active) < 3 or active.std() == 0:
        return {"tracking_error_pct": None, "information_ratio": None}

    te = float(active.std()) * np.sqrt(12)
    return {
        "tracking_error_pct": round(te * 100, 2),
        "information_ratio": round(float(active.mean()) * 12 / te, 3) if te > 0 else None,
    }


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
        "annualised_volatility_pct": round(float(daily_returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR)) * 100, 2)
        if len(daily_returns) > 1 else None,
        # Calmar = CAGR / |max drawdown|. Undefined when there was no drawdown.
        "calmar_ratio": round(cagr / abs(max_dd), 3) if (cagr is not None and max_dd < 0) else None,
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
    # Fill in the live per-appetite defaults when the caller did not inject a
    # parameter set, so existing callers keep their exact previous behaviour.
    if config.params is None:
        from app.core.strategy_params import StrategyParams

        config = replace(
            config,
            params=replace(
                StrategyParams.for_appetite(config.risk_appetite),
                horizon_days=config.horizon_days,
                rebalance_frequency=config.rebalance_frequency,
            ),
        )

    if config.universe_stock_ids is not None:
        stocks = (db.query(Stock)
                  .filter(Stock.id.in_(config.universe_stock_ids))
                  .order_by(Stock.symbol).all())
    else:
        stocks = db.query(Stock).filter(Stock.is_active == True).order_by(Stock.symbol).all()  # noqa: E712

    # Universe subset (Phase 16 §6): keep the top N by 20-day traded value,
    # measured point-in-time at the backtest start using the same liquidity
    # definition the universe builder uses. Applied before anything else so
    # every downstream stage sees the reduced set. Skipped when an explicit
    # universe was injected — that caller has already decided membership.
    top_n = getattr(config.params, "universe_top_n", None) if config.params else None
    if top_n and config.universe_stock_ids is None:
        ranked = []
        for st in stocks:
            df = _load_ohlcv_df(db, st.id)
            df = df[df["date"] <= config.start_date].tail(20)
            tv = (df["close"].astype(float) * df["volume"].astype(float)).dropna()
            if not tv.empty:
                ranked.append((float(tv.mean()), st))
        ranked.sort(key=lambda r: r[0], reverse=True)
        stocks = [st for _, st in ranked[:top_n]]
    stocks_by_id = {s.id: s for s in stocks}
    raw_frames = _load_all_price_frames(db, stocks)
    indexed_frames = {sid: df.set_index("date") for sid, df in raw_frames.items()}

    # The trading calendar is derived from this frame, so a hardcoded "2y" here
    # silently capped every backtest at the last two years regardless of how
    # much stock history existed — walk-forward folds starting earlier failed
    # with "No trading days available". Tied to HISTORY_PERIOD so the calendar
    # always spans as much history as the stocks themselves.
    benchmark_df = fetch_price_history("^NSEI", period=HISTORY_PERIOD)[["date", "close"]]
    trading_calendar = sorted(d for d in benchmark_df["date"] if config.start_date <= d <= config.end_date)
    if not trading_calendar:
        raise ValueError("No trading days available in the given date range")

    rebalance_dates = set(_rebalance_dates(trading_calendar, config.params.rebalance_frequency))

    # --- Market regime ---------------------------------------------------
    # POINT-IN-TIME CONTRACT, stated explicitly:
    #   signal timestamp       = close of day T
    #   construction timestamp = close of day T (same bar)
    #   execution timestamp    = close of day T, at that day's price
    # The 200DMA at T is a trailing mean of closes up to and including T, so no
    # value after T is ever read. `.rolling(min_periods=...)` leaves the warm-up
    # window NaN rather than averaging a short series, and a NaN regime is
    # treated as bull (no filtering) rather than silently forcing the strategy
    # to cash before the average exists.
    regime_is_bull: dict[date_type, bool] = {}
    exposure_by_date: dict[date_type, float] = {}
    if config.params.use_regime_filter:
        bench = benchmark_df.sort_values("date").reset_index(drop=True)
        closes = bench["close"].astype(float)
        ma = closes.rolling(config.params.regime_ma_days, min_periods=config.params.regime_ma_days).mean()
        mom = closes / closes.shift(config.params.reentry_momentum_days) - 1

        rule = config.params.reentry_rule
        stage = max(1, config.params.reentry_stage_days)
        state_bull = True   # assume invested at the start; the first bear close flips it
        bull_streak = 0

        for i, (d, px, ma_px) in enumerate(zip(bench["date"], closes, ma)):
            raw_bull = True if pd.isna(ma_px) else bool(px >= ma_px)

            if not raw_bull:
                # Exit is ALWAYS immediate — never delayed by any rule.
                state_bull = False
                bull_streak = 0
                regime_is_bull[d] = False
                exposure_by_date[d] = config.params.bear_exposure
                continue

            bull_streak += 1
            if not state_bull:
                if rule == "confirm3":
                    allowed = bull_streak >= 3
                elif rule == "confirm5":
                    allowed = bull_streak >= 5
                elif rule == "momentum":
                    m = mom.iloc[i]
                    allowed = bool(pd.notna(m) and m > 0)
                else:  # "immediate" and "staged" both re-enter at once
                    allowed = True
                if allowed:
                    state_bull = True

            regime_is_bull[d] = state_bull
            if not state_bull:
                exposure_by_date[d] = config.params.bear_exposure
            elif rule == "staged":
                # Ramp 25/50/75/100 as the bull streak extends, so a false dawn
                # is entered at a quarter size rather than in full.
                step = min(4, bull_streak // stage + 1)
                exposure_by_date[d] = min(config.params.bull_exposure, 0.25 * step)
            else:
                exposure_by_date[d] = config.params.bull_exposure

    def _target_exposure(day) -> float:
        if not config.params.use_regime_filter:
            return 1.0
        return exposure_by_date.get(day, config.params.bull_exposure)

    bull_days = 0
    bear_days = 0

    cash = config.initial_capital
    open_positions: list[OpenPosition] = []
    trade_log: list[Trade] = []
    equity_curve: list[dict] = []
    deployed_samples: list[float] = []
    total_costs_paid = 0.0  # commission only; slippage is inside the fill price
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
            elif (today - pos.entry_date).days >= config.params.horizon_days:
                exit_price, reason = close, "horizon"

            if exit_price is None:
                still_open.append(pos)
                continue

            fill = _sell_fill(exit_price, config.slippage_pct)
            sell_cost = _half_cost(fill, pos.quantity, config.transaction_cost_pct)
            total_costs_paid += sell_cost
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
            snapshot = compute_point_in_time_universe(
                bounded_frames, bounded_bench, config.params, config.indicator_cache, today
            )

            held_ids = {p.stock_id for p in open_positions}
            held_sector_counts: dict[str, int] = {}
            for p in open_positions:
                sec = p.sector or "Unknown"
                held_sector_counts[sec] = held_sector_counts.get(sec, 0) + 1

            current_equity = cash + sum(
                p.quantity * last_known_close.get(p.stock_id, p.entry_price) for p in open_positions
            )

            # --- regime exposure enforcement ---
            # Capping new entries alone would not reduce downside participation:
            # in a bear regime the book is usually already full, so the strategy
            # would ride the decline holding everything. Existing positions are
            # therefore trimmed down to the target, weakest score first, so the
            # filter actually removes exposure rather than merely pausing buying.
            exposure_target = _target_exposure(today)
            if config.params.use_regime_filter and exposure_target < 1.0:
                max_market_value = current_equity * exposure_target
                held_value = sum(
                    p.quantity * last_known_close.get(p.stock_id, p.entry_price) for p in open_positions
                )
                if held_value > max_market_value:
                    ranked = sorted(
                        open_positions,
                        key=lambda pos: (
                            snapshot[pos.stock_id].overall_score
                            if pos.stock_id in snapshot and snapshot[pos.stock_id].overall_score is not None
                            else -1.0
                        ),
                    )
                    for pos in ranked:
                        if held_value <= max_market_value:
                            break
                        px = last_known_close.get(pos.stock_id, pos.entry_price)
                        fill = _sell_fill(px, config.slippage_pct)
                        sell_cost = _half_cost(fill, pos.quantity, config.transaction_cost_pct)
                        total_costs_paid += sell_cost
                        cash += fill * pos.quantity - sell_cost
                        effective_exit = fill - sell_cost / pos.quantity
                        pnl = (effective_exit - pos.entry_price) * pos.quantity
                        trade_log.append(
                            Trade(
                                stock_id=pos.stock_id, symbol=pos.symbol,
                                entry_date=pos.entry_date, exit_date=today,
                                entry_price=round(pos.entry_price, 2), exit_price=round(effective_exit, 2),
                                quantity=pos.quantity, pnl=round(pnl, 2),
                                pnl_pct=round((effective_exit / pos.entry_price - 1) * 100, 2),
                                exit_reason="regime", holding_days=(today - pos.entry_date).days,
                            )
                        )
                        held_value -= pos.quantity * px
                        open_positions = [q for q in open_positions if q is not pos]
                    held_ids = {q.stock_id for q in open_positions}
                    held_sector_counts = {}
                    for q in open_positions:
                        sec = q.sector or "Unknown"
                        held_sector_counts[sec] = held_sector_counts.get(sec, 0) + 1

            max_new = config.params.max_stocks - len(open_positions)
            if max_new > 0:
                # Cash reserved by policy is withheld from the sizing routine
                # rather than subtracted afterwards, so a buffer of 0 genuinely
                # allows full deployment. The regime target caps it further.
                reserve = current_equity * config.params.cash_buffer_pct
                held_value_now = sum(
                    q.quantity * last_known_close.get(q.stock_id, q.entry_price) for q in open_positions
                )
                regime_room = max(0.0, current_equity * exposure_target - held_value_now)
                deployable = min(max(0.0, cash - reserve), regime_room)
                new_entries = _select_new_entries(
                    snapshot, stocks_by_id, held_ids, held_sector_counts, current_equity, deployable, config, max_new
                )
                for e in new_entries:
                    fill = _buy_fill(e["entry_reference_price"], config.slippage_pct)
                    buy_cost = _half_cost(fill, e["shares"], config.transaction_cost_pct)
                    total_costs_paid += buy_cost
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
        if config.params.use_regime_filter:
            if regime_is_bull.get(today, True):
                bull_days += 1
            else:
                bear_days += 1

        deployed_samples.append(
            0.0 if not open_positions else
            sum(p.quantity * last_known_close.get(p.stock_id, p.entry_price) for p in open_positions)
        )
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

    # Average share of equity actually at work. This is the honest measure of
    # idle-capital drag: the backtest applies no buffer by default, so any cash
    # sitting out is a consequence of risk-based sizing, not of policy.
    if deployed_samples and equity_curve:
        equities = [pt["equity"] for pt in equity_curve][: len(deployed_samples)]
        ratios = [d / e for d, e in zip(deployed_samples, equities) if e > 0]
        metrics["avg_capital_deployed_pct"] = round(sum(ratios) / len(ratios) * 100, 2) if ratios else None
    else:
        metrics["avg_capital_deployed_pct"] = None

    metrics["total_transaction_costs"] = round(total_costs_paid, 2)
    metrics.update(_capture_ratios(equity_curve, benchmark_curve))
    metrics.update(_relative_metrics(equity_curve, benchmark_curve))
    metrics["days_in_bull_regime"] = bull_days
    metrics["days_in_bear_regime"] = bear_days

    return BacktestResult(
        equity_curve=equity_curve,
        benchmark_equity_curve=benchmark_curve,
        trade_log=trade_log,
        metrics=metrics,
        benchmark_metrics=benchmark_metrics,
    )
