from datetime import date as date_type

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.models.price_history import PriceHistory

BENCHMARK_SYMBOL = "^NSEI"

RSI_PERIOD = 14
DMA_SHORT = 50
DMA_LONG = 200
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
VOLUME_RATIO_WINDOW = 20
ANNUALIZATION_DAYS = 252


def _rsi_wilder(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """RSI using Wilder's smoothing (not a simple/exponential moving average).

    Seed value is the plain average of the first `period` gains/losses; every
    value after that is smoothed with weight (period-1)/period on the prior
    average, per Wilder's original method. A plain EMA seeded from the first
    observation gives materially different early values, so this is done
    with an explicit loop rather than pandas .ewm().

    A missing close produces a NaN gain/loss for that day (and the day after,
    since diff() spans both). Because this is a recursive running average
    with no fixed window, letting a NaN through the arithmetic would corrupt
    every value for the rest of the series rather than just that one day —
    so a NaN day carries the prior average forward unchanged instead.
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    n = len(close)
    avg_gain = pd.Series(np.nan, index=close.index, dtype=float)
    avg_loss = pd.Series(np.nan, index=close.index, dtype=float)

    if n <= period:
        return pd.Series(np.nan, index=close.index)

    avg_gain.iloc[period] = gain.iloc[1 : period + 1].mean()
    avg_loss.iloc[period] = loss.iloc[1 : period + 1].mean()

    for i in range(period + 1, n):
        gain_i, loss_i = gain.iloc[i], loss.iloc[i]
        if pd.isna(gain_i) or pd.isna(loss_i):
            avg_gain.iloc[i] = avg_gain.iloc[i - 1]
            avg_loss.iloc[i] = avg_loss.iloc[i - 1]
        else:
            avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain_i) / period
            avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss_i) / period

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    rsi[(avg_loss == 0) & (avg_gain > 0)] = 100
    rsi[(avg_loss == 0) & (avg_gain == 0)] = np.nan
    return rsi


def _rolling_max_drawdown(close: pd.Series, window: int) -> pd.Series:
    """Worst peak-to-trough decline within each trailing `window`-day slice."""

    def _dd(values: np.ndarray) -> float:
        running_max = np.maximum.accumulate(values)
        drawdown = (values - running_max) / running_max
        return float(drawdown.min())

    return close.rolling(window).apply(_dd, raw=True)


def _rolling_beta(
    dates: pd.Series, stock_returns: pd.Series, benchmark_df: pd.DataFrame | None, window: int
) -> pd.Series:
    """Rolling beta over the last `window` trading days with a valid benchmark return.

    NSE's index data occasionally has a date the underlying stock trades on
    but ^NSEI doesn't (or vice versa) — a handful of such gaps out of ~500
    rows. A plain rolling().cov() requires every row in the window to be
    non-NaN, so one gap day blocks beta for the next `window` calendar rows
    even though 251 perfectly good paired observations exist. Instead this
    drops unpaired days first, then takes a rolling window over the
    remaining valid pairs — the standard "252 trading days" beta window is a
    count of trading days anyway, not calendar days.
    """
    if benchmark_df is None or benchmark_df.empty:
        return pd.Series(np.nan, index=stock_returns.index)

    merged = pd.DataFrame({"date": dates}).merge(
        benchmark_df[["date", "close"]].rename(columns={"close": "bench_close"}), on="date", how="left"
    )
    bench_returns = merged["bench_close"].astype(float).pct_change(fill_method=None)

    paired = pd.DataFrame({"stock_ret": stock_returns.values, "bench_ret": bench_returns.values})
    valid = paired.dropna()

    cov = valid["stock_ret"].rolling(window).cov(valid["bench_ret"])
    var = valid["bench_ret"].rolling(window).var()
    beta_valid = cov / var

    beta = pd.Series(np.nan, index=paired.index)
    beta.loc[valid.index] = beta_valid.values
    return beta


def compute_indicators(price_df: pd.DataFrame, benchmark_df: pd.DataFrame | None) -> pd.DataFrame:
    """Compute the full indicator series for one stock in a single pass.

    price_df: columns [date, close, volume], one row per trading day, sorted ascending.
    benchmark_df: same shape for the benchmark index (^NSEI), used for beta.
    Rows where a window isn't full stay NaN (mapped to NULL on upsert) rather
    than 0 — a stock with < 200 rows of history simply won't get dma_200,
    volatility, beta, or max_drawdown, but still gets whatever shorter-window
    indicators its history supports.
    """
    df = price_df.sort_values("date").reset_index(drop=True).copy()
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)

    df["dma_50"] = close.rolling(DMA_SHORT).mean()
    df["dma_200"] = close.rolling(DMA_LONG).mean()
    df["rsi_14"] = _rsi_wilder(close, RSI_PERIOD)

    ema_fast = close.ewm(span=MACD_FAST, adjust=False, min_periods=MACD_FAST).mean()
    ema_slow = close.ewm(span=MACD_SLOW, adjust=False, min_periods=MACD_SLOW).mean()
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=MACD_SIGNAL, adjust=False, min_periods=MACD_SIGNAL).mean()
    df["macd"] = macd
    df["macd_signal"] = macd_signal
    df["macd_hist"] = macd - macd_signal

    avg_volume_20 = volume.rolling(VOLUME_RATIO_WINDOW).mean()
    df["volume_ratio"] = volume / avg_volume_20

    returns = close.pct_change(fill_method=None)
    df["volatility"] = returns.rolling(ANNUALIZATION_DAYS).std() * np.sqrt(ANNUALIZATION_DAYS)
    df["max_drawdown"] = _rolling_max_drawdown(close, ANNUALIZATION_DAYS)

    df["beta"] = _rolling_beta(df["date"], returns, benchmark_df, ANNUALIZATION_DAYS)

    result_cols = [
        "date",
        "dma_50",
        "dma_200",
        "rsi_14",
        "macd",
        "macd_signal",
        "macd_hist",
        "volume_ratio",
        "volatility",
        "beta",
        "max_drawdown",
    ]
    result = df[result_cols].replace([np.inf, -np.inf], np.nan)
    return result


def load_price_history_df(
    db: Session, stock_id: int, since: date_type | None = None
) -> pd.DataFrame:
    """`since` bounds the query to a start date. Optional and defaulting to
    unbounded so single-stock callers (stock detail, onboarding, outlook) are
    unaffected — the memory cost of one unbounded history is trivial. It
    exists for callers that loop this over the whole ~500-stock universe
    (incremental.py, universe.py): loading full history 500 times in one
    process, rather than once, is what pushed the 512MB Render instance over
    its limit — confirmed by measuring peak RSS at 412MB for a genuine full
    incremental run, unbounded, with ~100MB of headroom left on the box.
    """
    q = db.query(PriceHistory.date, PriceHistory.close, PriceHistory.volume).filter(
        PriceHistory.stock_id == stock_id
    )
    if since is not None:
        q = q.filter(PriceHistory.date >= since)
    rows = q.order_by(PriceHistory.date).all()
    return pd.DataFrame(rows, columns=["date", "close", "volume"])


def fetch_benchmark_df(period: str = "2y") -> pd.DataFrame:
    from app.services.market_data import fetch_price_history

    return fetch_price_history(BENCHMARK_SYMBOL, period=period)[["date", "close"]]
