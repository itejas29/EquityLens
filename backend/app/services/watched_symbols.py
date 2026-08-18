"""Builds the Tier-2 fast-quote symbol set.

This is a DISPLAY concern only. Nothing here feeds signal generation, scoring,
position sizing or backtesting — the set decides which symbols get re-quoted
more often and nothing else. Adding or removing a symbol changes refresh
cadence, never a trading decision.

Order matters: MAX_FAST_SYMBOLS truncates the tail, so the list is built
highest-priority first.
"""

from collections.abc import Iterable

from sqlalchemy import func

from app.core.database import SessionLocal
from app.core.fast_quotes_config import INDEX_SYMBOLS, MAX_FAST_SYMBOLS
from app.models.daily_signal import DailySignal
from app.models.paper_trading import PaperTrade
from app.models.stock import Stock
from app.models.watchlist import Watchlist


def build_watched_symbols(viewed: Iterable[str] = ()) -> list[str]:
    """Symbols worth quoting on the fast tier, most important first.

    Priority:
      1. Whatever a client currently has open on Stock Detail. If someone is
         staring at one symbol, that is the one that must be live, even when
         the cap truncates everything after it.
      2. The published shortlist — the picks page is the reason this exists.
      3. Open paper positions, so holdings mark against a live price.
      4. Watchlist, which is the largest and least time-critical of the four.
    """
    db = SessionLocal()
    try:
        ordered: list[str] = []
        seen: set[str] = set()

        def add(symbols: Iterable[str]) -> None:
            for symbol in symbols:
                if not symbol:
                    continue
                upper = symbol.upper()
                if upper not in seen:
                    seen.add(upper)
                    ordered.append(upper)

        # Index first: it is the market context every page shows, and it costs
        # nothing extra since the whole set goes out in one batched request.
        add(INDEX_SYMBOLS)

        add(viewed)

        # The most recent published shortlist rather than strictly "today":
        # matches what /daily-signals serves by default, so the fast tier
        # covers exactly the rows the page is actually rendering.
        latest_signal_date = db.query(func.max(DailySignal.date)).scalar()
        if latest_signal_date is not None:
            add(
                r.symbol
                for r in db.query(Stock.symbol)
                .join(DailySignal, DailySignal.stock_id == Stock.id)
                .filter(DailySignal.date == latest_signal_date)
                .order_by(DailySignal.rank)
                .all()
            )

        add(
            r.symbol
            for r in db.query(Stock.symbol)
            .join(PaperTrade, PaperTrade.stock_id == Stock.id)
            .filter(PaperTrade.status == "open")
            .distinct()
            .all()
        )

        add(
            r.symbol
            for r in db.query(Stock.symbol)
            .join(Watchlist, Watchlist.stock_id == Stock.id)
            .distinct()
            .all()
        )

        return ordered[:MAX_FAST_SYMBOLS]
    finally:
        db.close()
