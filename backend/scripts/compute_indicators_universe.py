"""Compute indicators for every active stock in the seeded universe.

Fetches the ^NSEI benchmark once (shared across all stocks for beta), then
computes and upserts indicators per stock. Prints the latest indicator row
for 5 stocks at the end so the values can be spot-checked against a public
chart (e.g. TradingView/Screener) by hand.

Usage: python scripts/compute_indicators_universe.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.services.indicators import compute_indicators, fetch_benchmark_df, load_price_history_df  # noqa: E402
from app.services.ingestion import upsert_indicators  # noqa: E402

SPOT_CHECK_SYMBOLS = ["TCS", "RELIANCE", "HDFCBANK", "MARUTI", "SUNPHARMA"]


def run() -> None:
    db = SessionLocal()
    print("Fetching ^NSEI benchmark history...")
    benchmark_df = fetch_benchmark_df()
    print(f"  {len(benchmark_df)} benchmark rows")

    stocks = db.query(Stock).filter(Stock.is_active == True).order_by(Stock.symbol).all()  # noqa: E712
    total = len(stocks)
    succeeded, failed = 0, []

    for i, stock in enumerate(stocks, start=1):
        price_df = load_price_history_df(db, stock.id)
        if price_df.empty:
            failed.append((stock.symbol, "no price history"))
            print(f"[{i}/{total}] {stock.symbol}: SKIPPED (no price history)")
            continue

        try:
            result = compute_indicators(price_df, benchmark_df)
            upsert_indicators(db, stock.id, result)
            db.commit()
            succeeded += 1
            print(f"[{i}/{total}] {stock.symbol}: OK ({len(price_df)} price rows)")
        except Exception as exc:
            db.rollback()
            failed.append((stock.symbol, str(exc)))
            print(f"[{i}/{total}] {stock.symbol}: FAILED ({exc})")

    print(f"\nDone: {succeeded}/{total} succeeded")
    if failed:
        print("Failed:")
        for symbol, reason in failed:
            print(f"  - {symbol}: {reason}")

    print("\n" + "=" * 100)
    print("SPOT-CHECK — latest indicator values")
    print("=" * 100)

    cols = ["date", "dma_50", "dma_200", "rsi_14", "macd", "macd_signal", "macd_hist", "volume_ratio", "volatility", "beta", "max_drawdown"]
    header = f"{'symbol':<12}" + "".join(f"{c:>12}" for c in cols)
    print(header)

    for symbol in SPOT_CHECK_SYMBOLS:
        stock = db.query(Stock).filter(Stock.symbol == symbol).first()
        if stock is None:
            print(f"{symbol:<12} (not in universe)")
            continue
        from app.models.indicator import Indicator

        latest = db.query(Indicator).filter(Indicator.stock_id == stock.id).order_by(Indicator.date.desc()).first()
        if latest is None:
            print(f"{symbol:<12} (no indicators)")
            continue

        row = f"{symbol:<12}"
        for col in cols:
            val = getattr(latest, col)
            row += f"{str(val):>12}"
        print(row)

    print("=" * 100)
    db.close()


if __name__ == "__main__":
    run()
