"""Market overview: movers and turnover across the active universe.

Everything here is computed from stored bars, not from a live feed, for one
reason: the live-price cache only holds data while NSE is open. A dashboard
that renders "top movers" from an empty cache outside market hours would show
nothing at all, or worse, stale numbers with no indication of their age. So the
session comparison is always last-close vs previous-close out of price_history,
and `as_of` states exactly which session that was. Live prices, when the market
is open and the refresher has them, are layered on top by the caller.

The two-close comparison is done in SQL rather than pandas — 500 stocks x 500
bars is 250k rows, and only the last two per stock are needed.
"""

import logging
from dataclasses import asdict, dataclass
from datetime import date as date_type

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.cache import (
    get_fast_quotes,
    get_live_prices,
    get_market_overview_cache,
    get_session_snapshot,
    set_market_overview_cache,
)
from app.models.price_history import PriceHistory

logger = logging.getLogger(__name__)


@dataclass
class PriceFeed:
    """What prices to show, and how to honestly describe them.

    `status` is the whole point of this type:
      "live"   — the refresher is ticking; prices are moving right now.
      "closed" — the market is shut; these are the last traded prices of
                 `session_date`, held rather than dropped. The tape freezes
                 where the market froze instead of emptying out.
      "stale"  — nothing captured recently enough to stand behind.
    """

    prices: dict
    status: str
    captured_at: str | None = None
    session_date: str | None = None


def get_price_feed() -> PriceFeed:
    """Single source of truth for the live-vs-frozen decision.

    Live prices win when present. When they are not — which is every minute
    outside 09:10-15:40 IST — the last session's snapshot is served instead of
    nothing, so a user opening the app at 9pm sees the day's closing prices
    labelled as closed, not an empty ticker.
    """
    # Tier-2 quotes cover ~10-20 watched symbols at a much faster cadence, so
    # where they exist they are strictly fresher than the 60s universe tape and
    # win. They are read up front, not inside the live branch: the two tiers are
    # independent loops against independent keys, and Tier 1's key expires 90s
    # after its last write. Gating Tier 2 on Tier 1 would silently drop fresh
    # quotes for the watched set whenever the 500-stock fetch was slow or
    # rate-limited — exactly when they matter most.
    fast = (get_fast_quotes() or {}).get("quotes") or {}

    live = get_live_prices()
    if live:
        return PriceFeed(prices={**live, **fast} if fast else live, status="live")

    snapshot = get_session_snapshot()
    base = snapshot["prices"] if snapshot and snapshot.get("prices") else {}

    if base or fast:
        return PriceFeed(
            # Overlaying live quotes onto the frozen base can only make it
            # fresher, and `status` stays "closed" so nothing here is presented
            # as a live tape. Per-symbol timestamps ride along on each quote,
            # which is what the UI actually labels freshness from — the frozen
            # majority is never relabelled as live.
            prices={**base, **fast} if fast else base,
            status="closed",
            captured_at=snapshot.get("captured_at") if snapshot else None,
            session_date=snapshot.get("session_date") if snapshot else None,
        )

    return PriceFeed(prices={}, status="stale")

# A mover needs a real prior close to compare against. Stocks whose previous
# bar is missing are excluded rather than shown with a null change.
_SESSION_SQL = text(
    """
    WITH ranked AS (
        SELECT
            p.stock_id,
            p.date,
            p.close,
            p.volume,
            ROW_NUMBER() OVER (PARTITION BY p.stock_id ORDER BY p.date DESC) AS rn
        FROM price_history p
        JOIN stocks s ON s.id = p.stock_id
        WHERE s.is_active = TRUE AND p.close IS NOT NULL
    ),
    latest AS (SELECT * FROM ranked WHERE rn = 1),
    prior  AS (SELECT * FROM ranked WHERE rn = 2)
    SELECT
        s.symbol,
        s.company_name,
        s.sector,
        latest.date          AS as_of,
        latest.close         AS close,
        prior.close          AS prev_close,
        latest.volume        AS volume,
        (latest.close - prior.close) / prior.close * 100 AS change_pct,
        latest.close * latest.volume                     AS traded_value
    FROM latest
    JOIN prior  ON prior.stock_id = latest.stock_id
    JOIN stocks s ON s.id = latest.stock_id
    WHERE prior.close > 0
    """
)


@dataclass
class Mover:
    symbol: str
    company_name: str | None
    sector: str | None
    close: float
    prev_close: float
    change: float
    change_pct: float
    volume: int | None
    traded_value: float | None


@dataclass
class MarketOverview:
    as_of: date_type | None
    universe_size: int
    gainers: list[Mover]
    losers: list[Mover]
    most_traded: list[Mover]
    advancing: int
    declining: int
    unchanged: int


def _row_to_mover(row) -> Mover:
    close = float(row.close)
    prev = float(row.prev_close)
    return Mover(
        symbol=row.symbol,
        company_name=row.company_name,
        sector=row.sector,
        close=close,
        prev_close=prev,
        change=round(close - prev, 2),
        change_pct=round(float(row.change_pct), 2),
        volume=int(row.volume) if row.volume is not None else None,
        traded_value=float(row.traded_value) if row.traded_value is not None else None,
    )


def get_market_overview(db: Session, limit: int = 6) -> MarketOverview:
    # Cheap indexed probe first: the heavy CTE below scans every stock's last two
    # bars and cost ~500ms on every Home load, even though its inputs only change
    # when the 20:00 ingestion writes a new session. Keying the cache on that date
    # means a new session invalidates it immediately, with no staleness window.
    latest = db.query(func.max(PriceHistory.date)).scalar()
    if latest is not None:
        cached = get_market_overview_cache(latest.isoformat(), limit)
        if cached is not None:
            return MarketOverview(
                as_of=date_type.fromisoformat(cached["as_of"]) if cached["as_of"] else None,
                universe_size=cached["universe_size"],
                gainers=[Mover(**m) for m in cached["gainers"]],
                losers=[Mover(**m) for m in cached["losers"]],
                most_traded=[Mover(**m) for m in cached["most_traded"]],
                advancing=cached["advancing"],
                declining=cached["declining"],
                unchanged=cached["unchanged"],
            )

    rows = db.execute(_SESSION_SQL).fetchall()
    if not rows:
        return MarketOverview(
            as_of=None, universe_size=0, gainers=[], losers=[], most_traded=[],
            advancing=0, declining=0, unchanged=0,
        )

    movers = [_row_to_mover(r) for r in rows]

    # The universe's latest bar, not per-stock — a single stock lagging by a day
    # should not make the whole dashboard claim an older session.
    as_of = max(r.as_of for r in rows)

    by_change = sorted(movers, key=lambda m: m.change_pct, reverse=True)
    by_turnover = sorted(
        (m for m in movers if m.traded_value is not None), key=lambda m: m.traded_value, reverse=True
    )

    overview = MarketOverview(
        as_of=as_of,
        universe_size=len(movers),
        gainers=by_change[:limit],
        losers=list(reversed(by_change[-limit:])),
        most_traded=by_turnover[:limit],
        advancing=sum(1 for m in movers if m.change_pct > 0),
        declining=sum(1 for m in movers if m.change_pct < 0),
        unchanged=sum(1 for m in movers if m.change_pct == 0),
    )

    set_market_overview_cache(as_of.isoformat(), limit, {
        "as_of": as_of.isoformat(),
        "universe_size": overview.universe_size,
        "gainers": [asdict(m) for m in overview.gainers],
        "losers": [asdict(m) for m in overview.losers],
        "most_traded": [asdict(m) for m in overview.most_traded],
        "advancing": overview.advancing,
        "declining": overview.declining,
        "unchanged": overview.unchanged,
    })
    return overview


def get_sparkline(db: Session, symbol: str, days: int = 30) -> list[float]:
    """Closing prices for a small inline chart, oldest first.

    Returns [] rather than a placeholder series when there is no data — a
    sparkline with nothing behind it must render as nothing, not as a shape.
    """
    rows = db.execute(
        text(
            """
            SELECT p.close FROM price_history p
            JOIN stocks s ON s.id = p.stock_id
            WHERE s.symbol = :symbol AND p.close IS NOT NULL
            ORDER BY p.date DESC LIMIT :days
            """
        ),
        {"symbol": symbol.upper(), "days": days},
    ).fetchall()
    return [float(r.close) for r in reversed(rows)]
