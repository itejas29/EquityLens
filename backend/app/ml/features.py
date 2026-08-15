"""Feature/target dataset construction for the ML layer.

FEATURE POINT-IN-TIME NOTE: rsi_14, macd_hist, pct_from_50dma, pct_from_200dma,
volume_ratio, volatility, and beta all come from app.models.indicator rows.
Every one of those columns is a backward-looking rolling/EMA computation —
the value at row (stock, date) is a function only of that stock's own prices
up to and including that date, never of anything after it. So reading them
straight out of the already-computed `indicators` table is exactly as
point-in-time-safe as recomputing them from a date-truncated slice would be
(same reasoning documented in app/services/backtest.py) — there is no need
to (and this module does not) redo that computation here.

FUNDAMENTALS DATA LIMITATION: pe_ratio, pb_ratio, operating_margin, debt_to_equity,
revenue_growth, and eps_growth are NOT point-in-time — `fundamentals` holds
one snapshot per stock, so the same values are broadcast across every row
for that stock regardless of date. This is a real limitation of the data
source (documented in docs/ml_results.md): the model can use these features
to tell stocks apart from each other, but not to learn how a stock's own
fundamentals evolved over time.

TARGET: 1 if the stock's forward 20-trading-day return beats the ^NSEI
benchmark's forward 20-trading-day return over the same window, else 0.
Built with a per-stock shift(-20) — the only place future data is allowed to
appear — and never mixed into any feature column.
"""

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.models.fundamentals import Fundamentals
from app.models.indicator import Indicator
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.core.universe_config import HISTORY_PERIOD
from app.services.market_data import fetch_price_history

TARGET_HORIZON_DAYS = 20

# RELATIVE-STRENGTH FEATURES — the fix for a target/feature mismatch.
#
# The target asks a RELATIVE question: does this stock beat ^NSEI over the next
# 20 days? Every original feature was ABSOLUTE — a stock's own RSI, its own
# distance from its own moving average. None of them said anything about the
# stock versus the index, so the model was asked a relative question using
# inputs that could not express one. The `rel_*` columns below are the stock's
# trailing return minus the benchmark's over the same window, which is the same
# quantity as the target, just measured backwards instead of forwards.
#
# All are strictly backward-looking: the value at (stock, date) uses only bars
# up to and including that date.
RELATIVE_WINDOWS = (5, 20, 60, 120)

# CROSS-SECTIONAL RANKS — the second half of the same fix.
#
# "Beats the index" is really "beats the average stock". Whether an absolute
# 6% one-month return is good depends entirely on what every other stock did
# that month. Ranking a feature within each date's cross-section converts it
# from "6%" to "better than 82% of the universe today", which is the form the
# target is actually stated in, and it neutralises market-wide moves that
# otherwise dominate absolute features.
CROSS_SECTIONAL_BASE = (
    "rel_20",
    "rel_60",
    "ret_20",
    "ret_60",
    "pct_from_50dma",
    "pct_from_200dma",
    "volatility",
    "dist_52w_high",
)

FEATURE_COLUMNS = [
    # Absolute, price-derived (all genuinely point-in-time)
    "rsi_14",
    "macd_hist",
    "pct_from_50dma",
    "pct_from_200dma",
    "volume_ratio",
    "volatility",
    "beta",
    # Own trailing returns
    "ret_5",
    "ret_20",
    "ret_60",
    "ret_120",
    # Trailing performance vs the benchmark — same quantity as the target
    "rel_5",
    "rel_20",
    "rel_60",
    "rel_120",
    # Position within the 1-year range
    "dist_52w_high",
    "dist_52w_low",
    # Cross-sectional percentile ranks, computed per date
    *[f"cs_{c}" for c in CROSS_SECTIONAL_BASE],
    # Fundamentals. Static snapshots broadcast across dates — they separate
    # stocks from each other but carry no time variation. See the limitation
    # note in this module's docstring.
    #
    # `roe` is deliberately absent. Measured coverage across the 500-stock
    # universe: pe 474/500 · pb 498/500 · debt_to_equity 457/500 ·
    # revenue_growth 496/500 · eps_growth 456/500 · operating_margin 500/500 ·
    # roe 78/500. Because a row is dropped when ANY feature is missing, roe
    # alone collapsed the trainable set from 500 stocks to 46 and from ~235k
    # candidate rows to 9,758. Dropped on coverage grounds, decided from those
    # counts before any model was fitted.
    "pe_ratio",
    "pb_ratio",
    "debt_to_equity",
    "revenue_growth",
    "eps_growth",
    "operating_margin",
]


def _load_panel(db: Session) -> pd.DataFrame:
    stocks = db.query(Stock).filter(Stock.is_active == True).order_by(Stock.symbol).all()  # noqa: E712

    price_rows = (
        db.query(
            PriceHistory.stock_id,
            PriceHistory.date,
            PriceHistory.close,
        )
        .filter(PriceHistory.stock_id.in_([s.id for s in stocks]))
        .all()
    )
    price_df = pd.DataFrame(price_rows, columns=["stock_id", "date", "close"])

    indicator_rows = (
        db.query(
            Indicator.stock_id,
            Indicator.date,
            Indicator.dma_50,
            Indicator.dma_200,
            Indicator.rsi_14,
            Indicator.macd_hist,
            Indicator.volume_ratio,
            Indicator.volatility,
            Indicator.beta,
        )
        .filter(Indicator.stock_id.in_([s.id for s in stocks]))
        .all()
    )
    indicator_df = pd.DataFrame(
        indicator_rows,
        columns=["stock_id", "date", "dma_50", "dma_200", "rsi_14", "macd_hist", "volume_ratio", "volatility", "beta"],
    )

    panel = price_df.merge(indicator_df, on=["stock_id", "date"], how="inner")
    for col in ["close", "dma_50", "dma_200", "rsi_14", "macd_hist", "volume_ratio", "volatility", "beta"]:
        panel[col] = panel[col].astype(float)

    panel["pct_from_50dma"] = (panel["close"] - panel["dma_50"]) / panel["dma_50"]
    panel["pct_from_200dma"] = (panel["close"] - panel["dma_200"]) / panel["dma_200"]

    fundamentals_rows = []
    for stock in stocks:
        f = (
            db.query(Fundamentals)
            .filter(Fundamentals.stock_id == stock.id)
            .order_by(Fundamentals.as_of_date.desc())
            .first()
        )
        fundamentals_rows.append(
            {
                "stock_id": stock.id,
                "symbol": stock.symbol,
                "pe_ratio": float(f.pe_ratio) if f and f.pe_ratio is not None else np.nan,
                "pb_ratio": float(f.pb_ratio) if f and f.pb_ratio is not None else np.nan,
                "operating_margin": float(f.operating_margin) if f and f.operating_margin is not None else np.nan,
                "debt_to_equity": float(f.debt_to_equity) if f and f.debt_to_equity is not None else np.nan,
                "revenue_growth": float(f.revenue_growth) if f and f.revenue_growth is not None else np.nan,
                "eps_growth": float(f.eps_growth) if f and f.eps_growth is not None else np.nan,
            }
        )
    fundamentals_df = pd.DataFrame(fundamentals_rows)

    panel = panel.merge(fundamentals_df, on="stock_id", how="left")
    panel = panel.sort_values(["stock_id", "date"]).reset_index(drop=True)
    return _add_derived_features(panel)


def _add_derived_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Trailing returns, benchmark-relative strength, 52-week position, and
    per-date cross-sectional ranks.

    Every column here is computed with backward-looking windows only. The
    groupby-shift calls use POSITIVE shifts (looking back); the single negative
    shift in this module lives in _add_target and feeds the label alone.
    """
    benchmark = (
        fetch_price_history("^NSEI", period=HISTORY_PERIOD)[["date", "close"]]
        .sort_values("date")
        .rename(columns={"close": "bench_close"})
        .reset_index(drop=True)
    )
    for window in RELATIVE_WINDOWS:
        benchmark[f"bench_ret_{window}"] = benchmark["bench_close"] / benchmark["bench_close"].shift(window) - 1

    panel = panel.merge(
        benchmark[["date"] + [f"bench_ret_{w}" for w in RELATIVE_WINDOWS]], on="date", how="left"
    )

    grouped = panel.groupby("stock_id")["close"]
    for window in RELATIVE_WINDOWS:
        panel[f"ret_{window}"] = grouped.shift(0) / grouped.shift(window) - 1
        # Same quantity the target measures, pointed backwards.
        panel[f"rel_{window}"] = panel[f"ret_{window}"] - panel[f"bench_ret_{window}"]

    # Position inside the trailing 1-year range. min_periods keeps the early
    # rows NaN rather than computing a "52-week high" from three weeks of data.
    roll_max = grouped.transform(lambda s: s.rolling(252, min_periods=200).max())
    roll_min = grouped.transform(lambda s: s.rolling(252, min_periods=200).min())
    panel["dist_52w_high"] = (panel["close"] - roll_max) / roll_max
    panel["dist_52w_low"] = (panel["close"] - roll_min) / roll_min

    # Cross-sectional percentile rank within each date. pct=True gives 0-1
    # regardless of how many stocks happen to have data that day, so the scale
    # stays stable as universe coverage changes over the history.
    for col in CROSS_SECTIONAL_BASE:
        panel[f"cs_{col}"] = panel.groupby("date")[col].rank(pct=True)

    panel = panel.drop(columns=[f"bench_ret_{w}" for w in RELATIVE_WINDOWS])
    return panel


def _add_target(panel: pd.DataFrame, db: Session) -> pd.DataFrame:
    benchmark = fetch_price_history("^NSEI", period=HISTORY_PERIOD)[["date", "close"]].sort_values("date").reset_index(drop=True)
    benchmark = benchmark.rename(columns={"close": "bench_close"})
    benchmark["bench_forward_close"] = benchmark["bench_close"].shift(-TARGET_HORIZON_DAYS)
    benchmark["bench_forward_return"] = benchmark["bench_forward_close"] / benchmark["bench_close"] - 1

    panel = panel.merge(benchmark[["date", "bench_forward_return"]], on="date", how="left")

    # shift(-20) per stock's own date-sorted series — the only look-ahead in
    # this module, and it feeds only the target, never a feature column.
    panel["forward_close"] = panel.groupby("stock_id")["close"].shift(-TARGET_HORIZON_DAYS)
    panel["stock_forward_return"] = panel["forward_close"] / panel["close"] - 1

    has_target_inputs = panel["forward_close"].notna() & panel["bench_forward_return"].notna()
    panel["target"] = np.where(
        has_target_inputs, (panel["stock_forward_return"] > panel["bench_forward_return"]).astype(float), np.nan
    )
    return panel


def build_latest_features_frame(db: Session) -> pd.DataFrame:
    """Latest feature row per stock, indexed by stock_id, for prediction.

    Built from the full panel rather than per-stock, because the cross-sectional
    rank features are undefined for a stock in isolation — "better than 82% of
    the universe today" needs the universe. Rows still missing any feature are
    dropped, so a stock without a fundamentals snapshot simply has no prediction
    rather than one computed from substituted values.
    """
    panel = _load_panel(db)
    if panel.empty:
        return pd.DataFrame()

    latest = (
        panel.sort_values("date").groupby("stock_id").tail(1).set_index("stock_id")
    )
    available = [c for c in FEATURE_COLUMNS if c in latest.columns]
    return latest.dropna(subset=available)


def build_feature_dataset(db: Session) -> pd.DataFrame:
    """Returns a DataFrame with stock_id, symbol, date, FEATURE_COLUMNS, and
    target — rows with any missing feature or an undefined target (near the
    end of available history, where there's no forward window yet) dropped.
    """
    panel = _load_panel(db)
    panel = _add_target(panel, db)

    total_rows = len(panel)
    clean = panel.dropna(subset=FEATURE_COLUMNS + ["target"]).copy()
    clean["target"] = clean["target"].astype(int)

    print(f"[features] {total_rows} candidate rows -> {len(clean)} survive after dropping missing features/target")

    return clean[["stock_id", "symbol", "date"] + FEATURE_COLUMNS + ["target"]].reset_index(drop=True)
