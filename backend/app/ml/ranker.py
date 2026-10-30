"""Phase 25's machine-learning ranker: gradient-boosted trees that rank the
stocks of one date's universe against each other for the next month.

How it differs from app/ml/train.py's classifier, and why:
- Target: a stock's percentile WITHIN its date's universe over the next 21
  sessions. train.py's target, "beats NIFTY over 20 days", mostly asks whether
  the market will rise on that date, which is not a stock-selection question.
- Price and volume features only. Point-in-time fundamentals are not available
  (train.py documents its snapshot limitation), so none are used.
- Judged by the portfolio it produces on the survivorship-free bench, not by a
  classification score.

Every setting here is fixed by docs/experiments/phase25_literature_candidates/
PREREGISTRATION.md and is not tuned.
"""

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from app.services.price_integrity import find_discontinuity
from app.services.ranking_candidates import SKIP, WINDOW, aligned_benchmark, residual_return_stats, usable_closes

FEATURES = (
    "mom_12_1", "ret_1m", "ret_6m", "resid_mom", "high_52w", "vol_1y", "vol_3m", "beta_1y",
    "max_1m", "illiquidity", "log_traded_value", "traded_value_trend", "dist_200dma",
)
HORIZON = 21  # sessions in the label window: one monthly rebalance
# The first month-end at which every lab name has a year and a day of bars:
# Yahoo-sourced history starts 2016-11-01, the NSE files 2016-06-01. Starting
# earlier would fill the first months with the NSE-file names alone — 71 of
# those 105 since delisted — and teach the model from them alone
# (pre-registration change 3).
TRAIN_FROM = date(2017, 11, 30)
# early_stopping=False: scikit-learn's default ("auto") turns early stopping on
# above 10,000 rows with a RANDOM 10% validation split, which would make the
# number of trees depend on fold size and validate on rows dated after some
# training rows. Fixed before any run; see the pre-registration's change log.
MODEL_PARAMS = dict(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=200,
                    l2_regularization=1.0, early_stopping=False, random_state=20261030)


def features_at(closes: np.ndarray, volumes: np.ndarray, bench: np.ndarray) -> dict[str, float] | None:
    """The 13 features at the last element of aligned arrays, oldest first.

    None with less than a year and a day of history. A feature whose inputs
    are missing is NaN, which the model handles natively.
    """
    n = WINDOW + 1
    if len(closes) < n:
        return None
    c, v, m = closes[-n:], volumes[-n:], bench[-n:]
    if (c <= 0).any():
        return None
    last = c[-1]
    log_r = np.diff(np.log(c))
    r = c[1:] / c[:-1] - 1
    tv = c * v  # traded value per session
    stats = residual_return_stats(c, m, SKIP)
    # Amihud illiquidity: price move per rupee traded, over sessions that traded.
    traded = tv[-63:] > 0
    tv_year = tv[-WINDOW:].mean()
    return {
        "mom_12_1": float((last / c[-WINDOW] - 1) - (last / c[-SKIP] - 1)),
        "ret_1m": float(last / c[-SKIP] - 1),
        "ret_6m": float(last / c[-126] - 1),
        "resid_mom": stats[0] if stats else np.nan,
        "high_52w": float(last / c[-WINDOW:].max()),
        "vol_1y": float(log_r.std(ddof=1)),
        "vol_3m": float(log_r[-63:].std(ddof=1)),
        "beta_1y": stats[1] if stats else np.nan,
        "max_1m": float(r[-SKIP:].max()),
        "illiquidity": float(np.mean(np.abs(r[-63:][traded]) / tv[-63:][traded])) if traded.any() else np.nan,
        "log_traded_value": float(np.log(tv[-63:].mean())) if tv[-63:].mean() > 0 else np.nan,
        "traded_value_trend": float(tv[-SKIP:].mean() / tv_year) if tv_year > 0 else np.nan,
        "dist_200dma": float(last / c[-200:].mean() - 1),
    }


def cross_section(rows: dict[int, dict[str, float]]) -> pd.DataFrame:
    """One date's feature rows -> percentiles within that date (NaN kept)."""
    return pd.DataFrame.from_dict(rows, orient="index", columns=list(FEATURES)).rank(pct=True)


@dataclass
class StockSeries:
    dates: np.ndarray       # datetime64[D], ascending
    closes: np.ndarray
    volumes: np.ndarray
    bench: np.ndarray       # NIFTY's close on the same dates
    break_at: int | None    # position of the first corporate-action step, if any


def prepare_series(frames: dict[int, pd.DataFrame], bench: pd.DataFrame) -> dict[int, StockSeries]:
    out: dict[int, StockSeries] = {}
    for sid, df in frames.items():
        df = df.dropna(subset=["close"]).sort_values("date")
        if df.empty:
            continue
        closes = df["close"].astype(float).to_numpy()
        brk = find_discontinuity(closes.tolist())
        out[sid] = StockSeries(
            dates=np.array(df["date"].tolist(), dtype="datetime64[D]"),
            closes=closes,
            volumes=df["volume"].astype(float).fillna(0.0).to_numpy(),
            bench=aligned_benchmark(bench, pd.Index(df["date"])),
            break_at=None if brk is None else brk.index,
        )
    return out


def month_ends(calendar: list[date]) -> list[date]:
    """The last session of each calendar month."""
    out: list[date] = []
    for d in calendar:
        if out and (out[-1].year, out[-1].month) == (d.year, d.month):
            out[-1] = d
        else:
            out.append(d)
    return out


def training_rows(series: dict[int, StockSeries], calendar: list[date], dates: list[date],
                  universe_for) -> pd.DataFrame:
    """One row per (month-end, stock in that month-end's universe) with full
    features: 13 feature percentiles, the label percentile, the month-end, and
    the session its label window closes on.

    The label is the return from the month-end close to the last close within
    the next HORIZON sessions: a stock that stops trading inside the window is
    scored at its last close, as the engine exits it there. A row whose history
    or label window contains a corporate-action step is left out — the same gate
    that keeps such a stock out of every ranking.
    """
    cal = np.array(calendar, dtype="datetime64[D]")
    parts = []
    for d in dates:
        i = int(np.searchsorted(cal, np.datetime64(d), side="right")) - 1
        if i < 0 or i + HORIZON >= len(cal):
            continue
        label_end = cal[i + HORIZON]
        feats: dict[int, dict[str, float]] = {}
        fwd: dict[int, float] = {}
        for sid in universe_for(d):
            s = series.get(sid)
            if s is None:
                continue
            k = int(np.searchsorted(s.dates, np.datetime64(d), side="right"))       # bars on or before d
            j = int(np.searchsorted(s.dates, label_end, side="right"))              # bars to the label end
            if k < WINDOW + 1 or (s.break_at is not None and s.break_at < j):
                continue
            f = features_at(s.closes[:k], s.volumes[:k], s.bench[:k])
            if f is None:
                continue
            feats[sid] = f
            fwd[sid] = float(s.closes[j - 1] / s.closes[k - 1] - 1)
        if len(feats) < 2:
            continue
        x = cross_section(feats)
        x["label"] = pd.Series(fwd).rank(pct=True)
        x["month_end"] = d
        x["label_end"] = pd.Timestamp(label_end).date()
        parts.append(x)
    return pd.concat(parts) if parts else pd.DataFrame(columns=[*FEATURES, "label", "month_end", "label_end"])


def rows_before(rows: pd.DataFrame, start: date) -> pd.DataFrame:
    """Training rows whose label window closes before `start`, so a fold's
    model never learns from a return that overlaps the fold."""
    return rows[rows["label_end"] < start]


def fit(rows: pd.DataFrame) -> HistGradientBoostingRegressor:
    model = HistGradientBoostingRegressor(**MODEL_PARAMS)
    model.fit(rows[list(FEATURES)].to_numpy(), rows["label"].to_numpy())
    return model


def make_score_fn(model: HistGradientBoostingRegressor):
    """A score_fn for ranking_engine="external": features from the frames as
    they stood at `as_of`, percentiled within that date, then predicted."""
    def score(frames: dict[int, pd.DataFrame], bench: pd.DataFrame, as_of) -> dict[int, float]:
        feats: dict[int, dict[str, float]] = {}
        for sid, df in frames.items():
            closes = usable_closes(df, WINDOW + 1)
            if closes is None:
                continue
            volumes = df.set_index("date")["volume"].astype(float).reindex(closes.index).fillna(0.0)
            f = features_at(closes.to_numpy(), volumes.to_numpy(), aligned_benchmark(bench, closes.index))
            if f is not None:
                feats[sid] = f
        if len(feats) < 2:
            return {}
        x = cross_section(feats)
        return dict(zip(x.index, model.predict(x.to_numpy()).tolist()))
    return score
