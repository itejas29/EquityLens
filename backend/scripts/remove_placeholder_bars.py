"""Remove no-trade placeholder bars from price_history, and the indicator rows
computed on them.

WHAT THEY ARE. For a session in which nothing traded — including a whole NSE
holiday — Yahoo returns a bar with open = high = low = close = the previous
close and zero volume. The ingest stored those until
ingestion.drop_no_trade_placeholders existed. Measured 2026-10-30: 4,265 such
rows, on 2026-01-15, 05-01, 05-28, 06-26, 09-14 and 10-02 (NSE published no
bhavcopy for any of them: closed) and on 2025-03-18 (NSE DID trade, so 632
stocks have a placeholder where the real session should be). They are not
prices — build rule 5 — and as zero-range days they drag ATR, and with it
every entry zone and stop, for weeks afterwards.

WHAT THIS DOES. Finds every date on which more than half the stored bars are
placeholders, and asks NSE's own daily bhavcopy whether the exchange traded
that day. With --apply, deletes exactly the placeholder rows on those dates
(open = high = low = close and no volume) and the indicator rows for the
same stock and date. Nothing else is touched, and nothing is written in
their place: on a closed date that is the end of it; on a date NSE traded,
the session is left MISSING. Re-fetching it from the provider
(scripts/repair_price_history.py --date) is worth a try, but on 2026-10-30
Yahoo still returned placeholders for 2025-03-18 — and the ingest now drops
those — so it stays missing, which is correct. Filling it from NSE's own
bhavcopy is not done here: bhavcopy prices are not split-adjusted, and one
unadjusted day inside an adjusted series is a worse error than a gap.

Recent indicator rows are recomputed by the nightly run; for the whole
history, run scripts/compute_indicators_universe.py afterwards.

NSE's check uses the UDiFF bhavcopy, published since 2024-07-08. Earlier
dates are reported "unverified" — the legacy archive is not reliably
reachable — and their placeholders are still placeholders either way.

Dry run by default.

    python scripts/remove_placeholder_bars.py            # report only
    python scripts/remove_placeholder_bars.py --apply
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import requests
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal  # noqa: E402
from app.services.catalogue import _HEADERS  # noqa: E402

BHAVCOPY_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"

PLACEHOLDER = "open = high AND high = low AND low = close AND coalesce(volume, 0) = 0"

DATES = text(f"""
    SELECT date, count(*) AS bars, count(*) FILTER (WHERE {PLACEHOLDER}) AS placeholders
    FROM price_history
    GROUP BY date
    HAVING count(*) FILTER (WHERE {PLACEHOLDER}) > 0.5 * count(*)
    ORDER BY date
""")
DELETE_INDICATORS = text(f"""
    DELETE FROM indicators i USING price_history p
    WHERE p.date = :d AND {PLACEHOLDER.replace('open', 'p.open').replace('high', 'p.high').replace('low', 'p.low').replace('close', 'p.close').replace('volume', 'p.volume')}
      AND i.stock_id = p.stock_id AND i.date = p.date
""")
DELETE_BARS = text(f"DELETE FROM price_history WHERE date = :d AND {PLACEHOLDER}")


UDIFF_SINCE = date(2024, 7, 8)


def nse_traded(day) -> str:
    """'traded' (bhavcopy exists), 'closed' (404), 'unverified' (before
    UDiFF), or 'unknown' (any other answer)."""
    if day < UDIFF_SINCE:
        return "unverified"
    try:
        r = requests.head(BHAVCOPY_URL.format(d=day), headers=_HEADERS, timeout=20, allow_redirects=True)
    except requests.RequestException:
        return "unknown"
    return {200: "traded", 404: "closed"}.get(r.status_code, "unknown")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="delete; without it, report only")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        rows = db.execute(DATES).all()
        if not rows:
            print("No placeholder dates found.")
            return
        total, refetch = 0, []
        print(f"{'date':12s} {'NSE':8s} {'placeholders':>12s} {'of bars':>8s}")
        for day, bars, placeholders in rows:
            status = nse_traded(day)
            print(f"{day!s:12s} {status:8s} {placeholders:>12d} {bars:>8d}")
            total += placeholders
            if status in ("traded", "unknown"):
                refetch.append(day)
            if args.apply:
                ind = db.execute(DELETE_INDICATORS, {"d": day}).rowcount
                deleted = db.execute(DELETE_BARS, {"d": day}).rowcount
                db.commit()
                print(f"    deleted {deleted} bars, {ind} indicator rows")
        print(f"\n{total} placeholder bars on {len(rows)} dates" + ("" if args.apply else " — dry run, nothing deleted"))
        for day in refetch:
            print(f"NSE traded (or could not be checked) on {day}: its real session is missing. Try "
                  f"`python scripts/repair_price_history.py --date {day} --apply`; if the provider still "
                  f"returns placeholders, it stays missing.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
