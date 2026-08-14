"""Seed the stock universe: ~40 NSE stocks across sectors, large and mid cap.

Fetches meta + 2y price history + fundamentals for each symbol and upserts
into the DB. Continues past per-symbol failures and prints a summary at the
end, including which fundamental fields were unavailable and for how many
stocks (yfinance has known gaps for Indian tickers, esp. ROE/ROCE).

Usage: python scripts/seed_universe.py
"""

import logging
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal  # noqa: E402
from app.services.ingestion import upsert_fundamentals, upsert_price_history, upsert_stock  # noqa: E402
from app.services.market_data import (  # noqa: E402
    FUNDAMENTAL_FIELDS,
    SymbolNotFoundError,
    fetch_fundamentals,
    fetch_price_history,
    fetch_stock_meta,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("seed_universe")

RATE_LIMIT_SECONDS = 1.5

UNIVERSE = {
    "Banking": ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK"],
    "IT": ["TCS", "INFY", "WIPRO", "HCLTECH", "TECHM", "COFORGE"],
    "Pharma": ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "LUPIN", "AUROPHARMA"],
    "Auto": ["MARUTI", "TMCV", "M&M", "BAJAJ-AUTO", "EICHERMOT", "HEROMOTOCO"],
    "FMCG": ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR", "GODREJCP"],
    "Energy": ["RELIANCE", "ONGC", "NTPC", "POWERGRID", "COALINDIA", "BPCL"],
    "Metals": ["TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL"],
}


def seed() -> None:
    db = SessionLocal()
    succeeded: list[str] = []
    failed: list[tuple[str, str]] = []
    missing_field_counts: dict[str, int] = {f: 0 for f in FUNDAMENTAL_FIELDS}

    symbols = [(symbol, sector) for sector, syms in UNIVERSE.items() for symbol in syms]
    total = len(symbols)

    for i, (symbol, sector) in enumerate(symbols, start=1):
        logger.info("[%d/%d] Fetching %s (%s)...", i, total, symbol, sector)
        try:
            meta = fetch_stock_meta(symbol)
            history = fetch_price_history(symbol, period="2y")
            fundamentals = fetch_fundamentals(symbol)

            stock = upsert_stock(db, symbol, meta)
            rows = upsert_price_history(db, stock.id, history)
            upsert_fundamentals(db, stock.id, fundamentals["data"], date.today())
            db.commit()

            for missing in fundamentals["missing_fields"]:
                missing_field_counts[missing] += 1

            succeeded.append(symbol)
            logger.info("  OK — %d price rows, missing fundamentals: %s", rows, fundamentals["missing_fields"] or "none")
        except SymbolNotFoundError as exc:
            db.rollback()
            failed.append((symbol, str(exc)))
            logger.error("  FAILED (not found): %s", exc)
        except Exception as exc:  # keep seeding the rest of the universe on any single-symbol failure
            db.rollback()
            failed.append((symbol, str(exc)))
            logger.error("  FAILED: %s", exc)

        if i < total:
            time.sleep(RATE_LIMIT_SECONDS)

    db.close()

    print("\n" + "=" * 60)
    print("SEED SUMMARY")
    print("=" * 60)
    print(f"Succeeded: {len(succeeded)}/{total}")
    print(f"Failed:    {len(failed)}/{total}")
    if failed:
        print("\nFailed symbols:")
        for symbol, reason in failed:
            print(f"  - {symbol}: {reason}")

    print("\nMissing fundamental fields (count of stocks missing each, out of succeeded):")
    for field_name, count in missing_field_counts.items():
        if count:
            print(f"  - {field_name}: {count}/{len(succeeded)}")
    if not any(missing_field_counts.values()):
        print("  none")
    print("=" * 60)


if __name__ == "__main__":
    seed()
