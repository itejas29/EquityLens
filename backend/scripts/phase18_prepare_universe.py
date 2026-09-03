"""Phase 18 data prep — widen a NON-PRODUCTION database to 1000 stocks.

phase18_universe_size.py sweeps universe_top_n across 300/500/750/1000, but
universe_top_n only NARROWS an existing set. Against the live 500-stock DB the
deep arms silently collapse onto top500, so the sweep needs a database that
actually holds 1000 ingested names first. This builds that.

WHAT IT INGESTS, AND WHAT IT DELIBERATELY SKIPS

The backtest imports only Stock and PriceHistory — every indicator is
recomputed point-in-time from bounded price frames (see
backtest_scoring.compute_indicator_snapshot), and fundamentals are excluded
from backtest scoring entirely to avoid look-ahead bias (backtest_config.py
documents why). So this skips both the fundamentals fetch and the indicators
table, which is where the cost lives: universe.py measures fundamentals at
~0.9s/symbol serial, ~15 min for 1000 names, all of it useless here.

Metadata is NOT skipped, and that is not an optimisation oversight. backtest.py
buckets candidates by `stock.sector or "Unknown"` and enforces
max_stocks_per_sector (2). Ingesting 500 new stocks with a NULL sector would
drop every one of them into a single "Unknown" bucket, so the portfolio could
hold at most 2 of them at any time — the deep arms would come back looking flat
and the honest-seeming conclusion "depth adds nothing" would be an artifact of
missing metadata, not a property of the strategy. Sector is load-bearing.

Usage (against the experiment branch, never production):
    DATABASE_URL=postgresql://...ep-<branch>... python scripts/phase18_prepare_universe.py
"""

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.universe_config import FUNDAMENTALS_DELAY_SECONDS, STAGE_GAP_SECONDS  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.services import universe as universe_mod  # noqa: E402
from app.services.catalogue import catalogue_size, load_catalogue  # noqa: E402
from app.services.ingestion import upsert_stock  # noqa: E402
from app.services.market_data import fetch_stock_meta  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p18prep")

TARGET_UNIVERSE = 1000

# The live database's endpoint. This script marks stocks active and pulls a
# decade of history for 500 new names; doing that to production would change
# the next morning's shortlist before the experiment has said whether depth
# helps, which is the exact ordering Phase 18 exists to avoid.
PRODUCTION_HOST_FRAGMENT = "ep-aged-scene-a6dw1x3w"


def guard_not_production() -> None:
    url = settings.database_url
    if PRODUCTION_HOST_FRAGMENT in url:
        raise SystemExit(
            "\nRefusing to run: DATABASE_URL points at the production endpoint.\n"
            "This script activates ~500 new stocks, which would change the live\n"
            "shortlist before Phase 18 has validated whether depth helps.\n"
            "Point DATABASE_URL at the experiment branch and re-run.\n")
    log.info("target database: %s", url.split("@")[-1].split("/")[0])


def main() -> None:
    guard_not_production()
    db = SessionLocal()
    try:
        if catalogue_size(db) == 0:
            log.info("catalogue empty — loading NSE equity list")
            load_catalogue(db)
            db.commit()

        before = db.query(Stock).filter(Stock.is_active == True).count()  # noqa: E712
        log.info("active stocks before: %d", before)

        # Raise the cap for this process only. Patched on the universe module
        # rather than the config module because universe.py binds the constant
        # into its own namespace at import time.
        universe_mod.MAX_UNIVERSE_SIZE = TARGET_UNIVERSE
        log.info("screening exchange with cap raised to %d (this stage takes ~14 min)", TARGET_UNIVERSE)

        screen = universe_mod.screen_by_liquidity(db)
        ranked_symbols = [sym for sym, _ in screen.ranked]
        log.info("screen: %d considered, %d with data, %d cleared the floor",
                 screen.considered, screen.with_data, screen.passed)

        if screen.passed < TARGET_UNIVERSE:
            log.warning("only %d names cleared the ₹5cr/day floor — the deepest arm will be "
                        "capped at that, not %d. Not an error; the floor simply binds first.",
                        screen.passed, TARGET_UNIVERSE)

        existing = {s.symbol for s in db.query(Stock).filter(Stock.is_active == True).all()}  # noqa: E712
        new_symbols = [s for s in ranked_symbols if s not in existing]
        log.info("new symbols to ingest: %d", len(new_symbols))

        if not new_symbols:
            log.info("nothing to add — database already covers the ranked set")
            return

        log.info("pausing %ds before stage 2 (stage 1 hammered the same host)", STAGE_GAP_SECONDS)
        time.sleep(STAGE_GAP_SECONDS)

        log.info("stage 2a — batched %s price history for %d symbols",
                 universe_mod.HISTORY_PERIOD, len(new_symbols))
        stored, price_failures = universe_mod._ingest_prices_batched(db, new_symbols)
        log.info("prices stored for %d symbols, %d failures", len(stored), len(price_failures))

        # Metadata only — see the module docstring on why sector matters and why
        # fundamentals/indicators are skipped.
        priced = [s for s in new_symbols if s in stored]
        log.info("stage 2b — metadata (sector/name) for %d symbols at %.1fs each, ~%.0f min",
                 len(priced), FUNDAMENTALS_DELAY_SECONDS,
                 len(priced) * FUNDAMENTALS_DELAY_SECONDS / 60)
        meta_failures = []
        for i, symbol in enumerate(priced, start=1):
            try:
                upsert_stock(db, symbol, fetch_stock_meta(symbol))
                db.commit()
            except Exception as exc:
                db.rollback()
                meta_failures.append((symbol, str(exc)[:80]))
            if i % 50 == 0:
                log.info("  metadata: %d/%d", i, len(priced))
            time.sleep(FUNDAMENTALS_DELAY_SECONDS)

        after = db.query(Stock).filter(Stock.is_active == True).count()  # noqa: E712
        no_sector = (db.query(Stock)
                     .filter(Stock.is_active == True, Stock.sector.is_(None))  # noqa: E712
                     .count())

        print("\n" + "=" * 64)
        print("PHASE 18 UNIVERSE PREP")
        print("=" * 64)
        print(f"Active before      : {before}")
        print(f"Active after       : {after}")
        print(f"Newly ingested     : {len(stored)}")
        print(f"Price failures     : {len(price_failures)}")
        print(f"Metadata failures  : {len(meta_failures)}")
        print(f"Active w/o sector  : {no_sector}   <- must be ~0, see docstring")
        print("=" * 64)
        for sym, why in (price_failures + meta_failures)[:10]:
            print(f"  {sym:<14} {why[:70]}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
