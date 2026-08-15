from datetime import date as date_type
from datetime import datetime

from sqlalchemy import Date, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class NseSymbol(Base):
    """The searchable catalogue: every equity listed on NSE.

    Deliberately separate from `stocks`. This table is the full ~2,400-symbol
    list loaded from NSE's own EQUITY_L.csv — one cheap HTTP request, no
    per-stock market-data calls — so search and autocomplete can cover the
    whole exchange instantly. `stocks` stays the much smaller set that has
    actually been ingested (price history, fundamentals, indicators, scores).

    A row here means "this ticker exists and you can search for it". A matching
    row in `stocks` means "we have data and can analyse it". The gap between
    the two is closed on demand, when someone opens a symbol for the first time.
    """

    __tablename__ = "nse_symbols"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    # NSE series code. "EQ" is the normal rolling-settlement equity segment;
    # BE/BZ/SM/ST are restricted or SME segments that behave differently, so
    # the series is kept rather than discarded and filtered at query time.
    series: Mapped[str] = mapped_column(String(10), nullable=False)
    isin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    listed_on: Mapped[date_type | None] = mapped_column(Date, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
