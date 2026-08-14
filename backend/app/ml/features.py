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

FUNDAMENTALS DATA LIMITATION: pe_ratio, pb_ratio, roe, debt_to_equity,
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
from app.services.market_data import fetch_price_history

TARGET_HORIZON_DAYS = 20

FEATURE_COLUMNS = [
    "rsi_14",
    "macd_hist",
    "pct_from_50dma",
    "pct_from_200dma",
    "volume_ratio",
    "volatility",
    "beta",
    "pe_ratio",
    "pb_ratio",
    "roe",
    "debt_to_equity",
    "revenue_growth",
    "eps_growth",
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
                "roe": float(f.roe) if f and f.roe is not None else np.nan,
                "debt_to_equity": float(f.debt_to_equity) if f and f.debt_to_equity is not None else np.nan,
                "revenue_growth": float(f.revenue_growth) if f and f.revenue_growth is not None else np.nan,
                "eps_growth": float(f.eps_growth) if f and f.eps_growth is not None else np.nan,
            }
        )
    fundamentals_df = pd.DataFrame(fundamentals_rows)

    panel = panel.merge(fundamentals_df, on="stock_id", how="left")
    return panel.sort_values(["stock_id", "date"]).reset_index(drop=True)


def _add_target(panel: pd.DataFrame, db: Session) -> pd.DataFrame:
    benchmark = fetch_price_history("^NSEI", period="2y")[["date", "close"]].sort_values("date").reset_index(drop=True)
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


def build_latest_features_row(db: Session, stock_id: int) -> dict | None:
    """Feature row for the most recent available date for one stock —
    used at prediction time, not training. Returns None if any required
    feature is missing (insufficient history, no fundamentals snapshot,
    etc.) rather than substituting a default."""
    latest_indicator = (
        db.query(Indicator).filter(Indicator.stock_id == stock_id).order_by(Indicator.date.desc()).first()
    )
    latest_price = (
        db.query(PriceHistory).filter(PriceHistory.stock_id == stock_id).order_by(PriceHistory.date.desc()).first()
    )
    if latest_indicator is None or latest_price is None or latest_price.close is None:
        return None

    close = float(latest_price.close)
    dma_50 = float(latest_indicator.dma_50) if latest_indicator.dma_50 is not None else None
    dma_200 = float(latest_indicator.dma_200) if latest_indicator.dma_200 is not None else None
    if dma_50 is None or dma_200 is None or dma_50 == 0 or dma_200 == 0:
        return None

    fundamentals = (
        db.query(Fundamentals).filter(Fundamentals.stock_id == stock_id).order_by(Fundamentals.as_of_date.desc()).first()
    )

    def _f(value) -> float | None:
        return float(value) if value is not None else None

    row = {
        "rsi_14": _f(latest_indicator.rsi_14),
        "macd_hist": _f(latest_indicator.macd_hist),
        "pct_from_50dma": (close - dma_50) / dma_50,
        "pct_from_200dma": (close - dma_200) / dma_200,
        "volume_ratio": _f(latest_indicator.volume_ratio),
        "volatility": _f(latest_indicator.volatility),
        "beta": _f(latest_indicator.beta),
        "pe_ratio": _f(fundamentals.pe_ratio) if fundamentals else None,
        "pb_ratio": _f(fundamentals.pb_ratio) if fundamentals else None,
        "roe": _f(fundamentals.roe) if fundamentals else None,
        "debt_to_equity": _f(fundamentals.debt_to_equity) if fundamentals else None,
        "revenue_growth": _f(fundamentals.revenue_growth) if fundamentals else None,
        "eps_growth": _f(fundamentals.eps_growth) if fundamentals else None,
    }

    if any(row[col] is None for col in FEATURE_COLUMNS):
        return None
    return row


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
