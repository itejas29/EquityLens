"""NSE symbol catalogue — loading it, and searching it.

Source is NSE's own published equity list (EQUITY_L.csv), which is the
authoritative record of what is currently listed and under which ticker.
That matters more than it sounds: symbols change (Zomato now trades as
ETERNAL), and a stale hardcoded list silently returns "no data" for a company
that is perfectly well listed. Reloading the catalogue is how that gets fixed,
rather than by editing a Python dict.

Loading the catalogue costs exactly one HTTP request for the whole exchange —
no per-symbol market-data calls — which is what makes exchange-wide search
practical without ingesting thousands of price histories up front.
"""

import csv
import io
import logging
from datetime import datetime

import requests
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.nse_symbol import NseSymbol
from app.models.stock import Stock

logger = logging.getLogger(__name__)

NSE_EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

# NSE serves this only to browser-like clients; a bare python-requests UA gets
# refused. This is a plain public CSV download, not an authenticated scrape.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/csv,*/*",
}

REQUEST_TIMEOUT_SECONDS = 30

# Rolling-settlement equity only. The other series (BE/BZ trade-for-trade, SM/ST
# SME boards) have different settlement and liquidity behaviour, and ATR-based
# levels on them would not be executable the way the rest of the app assumes.
TRADEABLE_SERIES = ("EQ",)


class CatalogueError(Exception):
    pass


def fetch_nse_equity_list() -> list[dict]:
    try:
        response = requests.get(NSE_EQUITY_LIST_URL, headers=_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise CatalogueError(f"Could not download the NSE equity list: {exc}") from exc

    reader = csv.DictReader(io.StringIO(response.text))
    rows: list[dict] = []
    for raw in reader:
        # NSE's header row carries leading spaces on every column after the
        # first, so keys are normalised rather than indexed by exact name.
        row = {(k or "").strip().upper(): (v or "").strip() for k, v in raw.items()}
        symbol = row.get("SYMBOL")
        name = row.get("NAME OF COMPANY")
        if not symbol or not name:
            continue

        listed_on = None
        raw_date = row.get("DATE OF LISTING")
        if raw_date:
            try:
                listed_on = datetime.strptime(raw_date, "%d-%b-%Y").date()
            except ValueError:
                listed_on = None  # unparseable stays NULL rather than guessed

        rows.append(
            {
                "symbol": symbol.upper(),
                "company_name": name,
                "series": row.get("SERIES") or "",
                "isin": row.get("ISIN NUMBER") or None,
                "listed_on": listed_on,
            }
        )

    if not rows:
        raise CatalogueError("NSE equity list downloaded but contained no usable rows")
    return rows


def load_catalogue(db: Session) -> dict:
    """Upsert the whole catalogue. Returns counts for reporting.

    Symbols that disappear from NSE's list are left in place rather than
    deleted — they may still be referenced by historical rows in `stocks`,
    and removing them would break those records.
    """
    rows = fetch_nse_equity_list()
    existing = {s.symbol: s for s in db.query(NseSymbol).all()}

    added = 0
    updated = 0
    for row in rows:
        current = existing.get(row["symbol"])
        if current is None:
            db.add(NseSymbol(**row))
            added += 1
            continue
        if (
            current.company_name != row["company_name"]
            or current.series != row["series"]
            or current.isin != row["isin"]
        ):
            current.company_name = row["company_name"]
            current.series = row["series"]
            current.isin = row["isin"]
            updated += 1

    db.flush()
    return {"fetched": len(rows), "added": added, "updated": updated, "total": len(existing) + added}


def search_catalogue(db: Session, query: str, limit: int = 20, tradeable_only: bool = True) -> list[dict]:
    """Symbol/name search over the catalogue, annotated with whether each hit
    has already been ingested (and is therefore analysable right now).

    Ordering puts exact symbol matches first, then symbol prefix matches, then
    everything else — typing "TCS" should not surface "TCSLTD-adjacent" names
    above TCS itself.
    """
    term = query.strip()
    if not term:
        return []

    pattern = f"%{term}%"
    prefix = f"{term}%"

    # Outer-joined rather than looked up afterwards so "already ingested" can be
    # an ORDER BY term. Typing "bajaj" should surface BAJAJ-AUTO — a stock the
    # app can analyse right now — above four smallcaps that merely sort shorter.
    q = (
        db.query(NseSymbol, Stock.id.label("stock_id"))
        .outerjoin(Stock, Stock.symbol == NseSymbol.symbol)
        .filter(or_(NseSymbol.symbol.ilike(pattern), NseSymbol.company_name.ilike(pattern)))
    )
    if tradeable_only:
        q = q.filter(NseSymbol.series.in_(TRADEABLE_SERIES))

    rows = q.order_by(
        (func.upper(NseSymbol.symbol) == term.upper()).desc(),
        Stock.id.isnot(None).desc(),
        NseSymbol.symbol.ilike(prefix).desc(),
        func.length(NseSymbol.symbol).asc(),
        NseSymbol.symbol.asc(),
    ).limit(limit).all()

    return [
        {
            "symbol": h.symbol,
            "company_name": h.company_name,
            "series": h.series,
            "isin": h.isin,
            "is_ingested": stock_id is not None,
        }
        for h, stock_id in rows
    ]


def catalogue_size(db: Session, tradeable_only: bool = True) -> int:
    q = db.query(NseSymbol)
    if tradeable_only:
        q = q.filter(NseSymbol.series.in_(TRADEABLE_SERIES))
    return q.count()


def in_catalogue(db: Session, symbol: str) -> NseSymbol | None:
    return db.query(NseSymbol).filter(NseSymbol.symbol == symbol.strip().upper()).first()
