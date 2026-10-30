"""Phase 25's candidate rankings: residual momentum, nearness to the 52-week
high, and momentum blended with low volatility.

Pure functions over frames the caller has already cut at the decision date
(compute_point_in_time_universe passes frames bounded to `as_of`), so nothing
here can read a later bar. Each returns {stock_id: score}, higher ranks higher.
A stock without the full window a score needs, or with an unadjusted
corporate-action break, is left out rather than scored on a shorter window —
the rule normalized_momentum_scores and V1's momentum follow.

Definitions are fixed by docs/experiments/phase25_literature_candidates/
PREREGISTRATION.md.
"""

import numpy as np
import pandas as pd

from app.services.price_integrity import find_discontinuity

WINDOW = 252  # one year of sessions
SKIP = 21     # the latest month, left out of momentum: it carries short-term reversal


def usable_closes(df: pd.DataFrame, need: int) -> pd.Series | None:
    """The frame's closes indexed by date, or None if there are fewer than
    `need`, any is not positive, or the series has a corporate-action break."""
    closes = df.set_index("date")["close"].astype(float).dropna()
    if len(closes) < need or not bool((closes > 0).all()):
        return None
    if find_discontinuity(closes.tolist()) is not None:
        return None
    return closes


def aligned_benchmark(bench: pd.DataFrame, dates) -> np.ndarray:
    """NIFTY's close on each of `dates`, carrying the last close forward over a
    date NIFTY lacks, so a stock's return and the index's cover the same days."""
    s = bench.set_index("date")["close"].astype(float).sort_index()
    return s.reindex(s.index.union(dates)).ffill().reindex(dates).to_numpy()


def residual_return_stats(closes: np.ndarray, bench: np.ndarray, skip: int = SKIP) -> tuple[float, float] | None:
    """(residual momentum, beta) from aligned closes covering one window.

    β is the OLS slope of the stock's daily log return on NIFTY's. The residual
    is r − β·r_NIFTY with the intercept left in: subtracting a fitted intercept
    would force the residuals over the window to sum to zero, which would turn
    the score into minus the latest month — a reversal signal, not momentum.
    Score = sum of the residuals before the latest `skip` sessions divided by
    their standard deviation (Blitz, Huij & Martens 2011).
    """
    if not np.isfinite(bench).all() or (bench <= 0).any():
        return None
    r, rm = np.diff(np.log(closes)), np.diff(np.log(bench))
    var_m = rm.var(ddof=1)
    if not var_m > 0:
        return None
    beta = float(np.cov(r, rm, ddof=1)[0, 1] / var_m)
    e = r - beta * rm
    if skip > 0:
        e = e[:-skip]
    sd = e.std(ddof=1)
    if not sd > 0:
        return None
    return float(e.sum() / sd), beta


def residual_momentum_scores(frames: dict[int, pd.DataFrame], bench: pd.DataFrame,
                             window: int = WINDOW, skip: int = SKIP) -> dict[int, float]:
    out: dict[int, float] = {}
    for sid, df in frames.items():
        closes = usable_closes(df, window + 1)
        if closes is None:
            continue
        tail = closes.iloc[-(window + 1):]
        stats = residual_return_stats(tail.to_numpy(), aligned_benchmark(bench, tail.index), skip)
        if stats is not None:
            out[sid] = stats[0]
    return out


def high_52w_scores(frames: dict[int, pd.DataFrame], window: int = WINDOW) -> dict[int, float]:
    """Close divided by the highest close of the last `window` sessions
    (George & Hwang 2004). Closes, not intraday highs, so one spike does not
    set the reference."""
    out: dict[int, float] = {}
    for sid, df in frames.items():
        closes = usable_closes(df, window)
        if closes is None:
            continue
        tail = closes.iloc[-window:]
        out[sid] = float(tail.iloc[-1] / tail.max())
    return out


def volatility_scores(frames: dict[int, pd.DataFrame], window: int = WINDOW) -> dict[int, float]:
    """Standard deviation of daily log returns over the last `window` sessions."""
    out: dict[int, float] = {}
    for sid, df in frames.items():
        closes = usable_closes(df, window + 1)
        if closes is None:
            continue
        sd = float(np.diff(np.log(closes.iloc[-(window + 1):].to_numpy())).std(ddof=1))
        if sd > 0:
            out[sid] = sd
    return out


def momentum_lowvol_blend(momentum: dict[int, float], volatility: dict[int, float]) -> dict[int, float]:
    """NSE's NIFTY Alpha Low-Volatility 30 construction with momentum as the
    return leg: the mean of the momentum percentile and the low-volatility
    percentile, over the stocks that have both."""
    both = sorted(set(momentum) & set(volatility))
    if len(both) < 2:
        return {}
    mom = pd.Series({s: momentum[s] for s in both}).rank(pct=True)
    low = (-pd.Series({s: volatility[s] for s in both})).rank(pct=True)
    return ((mom + low) / 2).to_dict()
