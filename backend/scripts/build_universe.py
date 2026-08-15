"""Rebuild the scored universe from the full NSE equity list.

Screens every tradeable listing by liquidity, then fully ingests whatever
clears the floor. Takes roughly 10-20 minutes depending on how many symbols
survive the screen — it is a script rather than an API endpoint because no
sensible HTTP timeout covers that.

Usage:
    python scripts/build_universe.py            # screen + ingest
    python scripts/build_universe.py --screen   # screen only, report, no writes
    python scripts/build_universe.py --repair   # fix stocks left without indicators
    python scripts/build_universe.py --deepen   # re-pull prices at HISTORY_PERIOD, recompute indicators
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal  # noqa: E402
from app.core.universe_config import MAX_UNIVERSE_SIZE, MIN_AVG_TRADED_VALUE  # noqa: E402
from app.services.catalogue import catalogue_size, load_catalogue  # noqa: E402
from app.services.universe import build_universe, deepen_history, repair_missing_details, screen_by_liquidity  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_universe")


def main() -> None:
    screen_only = "--screen" in sys.argv
    db = SessionLocal()

    try:
        if catalogue_size(db) == 0:
            logger.info("Catalogue empty — loading the NSE equity list first")
            report = load_catalogue(db)
            db.commit()
            logger.info("Catalogue loaded: %s", report)

        if screen_only:
            result = screen_by_liquidity(db)
            print("\n" + "=" * 64)
            print("LIQUIDITY SCREEN (no data written)")
            print("=" * 64)
            print(f"Listings considered : {result.considered}")
            print(f"Returned price data : {result.with_data}")
            print(f"Cleared ₹{MIN_AVG_TRADED_VALUE:,.0f}/day : {result.passed} (cap {MAX_UNIVERSE_SIZE})")
            print("\nTop 15 by 20-day average traded value:")
            for symbol, value in result.ranked[:15]:
                print(f"  {symbol:<14} ₹{value / 10_000_000:>10,.1f} cr/day")
            print("=" * 64)
            return

        if "--deepen" in sys.argv:
            result = deepen_history(db)
            print("\n" + "=" * 64)
            print("HISTORY DEEPENED")
            print("=" * 64)
            print(f"Stocks        : {result.ingested}")
            print(f"Failures      : {len(result.failed)}")
            print(f"Elapsed       : {result.seconds / 60:.1f} min")
            for sym, why in result.failed[:10]:
                print(f"  {sym:<14} {why[:70]}")
            print("=" * 64)
            return

        if "--repair" in sys.argv:
            result = repair_missing_details(db)
            print("\n" + "=" * 64)
            print("REPAIR")
            print("=" * 64)
            print(f"Needed repair : {result.screen.considered}")
            print(f"Fixed         : {result.ingested}")
            print(f"Still failing : {len(result.failed)}")
            print(f"Elapsed       : {result.seconds / 60:.1f} min")
            for symbol, reason in result.failed[:15]:
                print(f"  {symbol:<14} {reason[:70]}")
            print("=" * 64)
            return

        result = build_universe(db)
        print("\n" + "=" * 64)
        print("UNIVERSE REBUILD")
        print("=" * 64)
        print(f"Listings considered : {result.screen.considered}")
        print(f"Returned price data : {result.screen.with_data}")
        print(f"Cleared the floor   : {result.screen.passed}")
        print(f"Ingested (active)   : {result.ingested}")
        print(f"Deactivated         : {result.deactivated}")
        print(f"Failures            : {len(result.failed)}")
        print(f"Elapsed             : {result.seconds / 60:.1f} min")
        if result.failed:
            print("\nFirst 15 failures:")
            for symbol, reason in result.failed[:15]:
                print(f"  {symbol:<14} {reason[:70]}")
        print("=" * 64)
    finally:
        db.close()


if __name__ == "__main__":
    main()
