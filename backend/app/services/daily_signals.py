"""Daily pre-market shortlist: the dated, frozen version of the scored universe.

Differs from `recommendations` in three ways that matter:
  1. It is GATED — a stock with no usable price series is dropped rather than
     shown with a caveat (see core/daily_signals_config.py for why).
  2. It is FROZEN — levels and scores are written once for the date and never
     recomputed, so a past day's call can be reviewed as it was published.
  3. It carries a RATIONALE — plain-English clauses built from the same values
     that produced the score, so the number is never presented unexplained.

The output is presented to the group as a direct call (BUY / WAIT plus price
levels), but every number here is still derived from prior price and
fundamentals data — the wording is decisive, the evidence is not hidden, and
the `caution` clauses ship alongside the `support` ones for exactly that reason.
"""

import logging
from dataclasses import dataclass
from datetime import date as date_type
from datetime import timedelta

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.daily_signals_config import (
    HIGH_BETA,
    LOW_BETA,
    MAX_PER_SECTOR,
    MAX_REFERENCE_AGE_DAYS,
    MAX_SIGNALS,
    MIN_OVERALL_SCORE,
    REQUIRED_SUBSCORES,
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    RSI_STRONG_LOW,
    VOLUME_SURGE_RATIO,
)
from app.models.daily_signal import DailySignal
from app.models.fundamentals import Fundamentals
from app.models.indicator import Indicator
from app.models.price_history import PriceHistory
from app.models.stock import Stock
import pandas as pd

from app.core.v1_strategy import V1, V1_VERSION
from app.services.levels import Levels, compute_levels_for_stock
from app.services.market_data import fetch_price_history
from app.services.scoring import StockScore, _map_signal

logger = logging.getLogger(__name__)


@dataclass
class Candidate:
    score: StockScore
    stock: Stock
    levels: Levels
    reference_close: float
    reference_date: date_type
    rationale: list[dict]


def _pct(a: float, b: float) -> float:
    return (a - b) / b * 100


def _latest_indicator(db: Session, stock_id: int) -> Indicator | None:
    return (
        db.query(Indicator)
        .filter(Indicator.stock_id == stock_id)
        .order_by(Indicator.date.desc())
        .first()
    )


def _latest_priced_bar(db: Session, stock_id: int) -> PriceHistory | None:
    """Most recent bar that actually has a close. A NULL-close bar is skipped
    rather than treated as missing data for the whole stock — the prior bar is
    still a valid reference, it is just a day older, and MAX_REFERENCE_AGE_DAYS
    decides whether that is recent enough to publish against.
    """
    return (
        db.query(PriceHistory)
        .filter(PriceHistory.stock_id == stock_id, PriceHistory.close.isnot(None))
        .order_by(PriceHistory.date.desc())
        .first()
    )


def _sector_median_pe(db: Session, sector: str | None) -> float | None:
    if sector is None:
        return None
    rows = (
        db.query(Fundamentals.pe_ratio)
        .join(Stock, Stock.id == Fundamentals.stock_id)
        .filter(Stock.sector == sector, Stock.is_active == True, Fundamentals.pe_ratio.isnot(None))  # noqa: E712
        .all()
    )
    values = sorted(float(r[0]) for r in rows)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def _build_rationale(db: Session, stock: Stock, ind: Indicator | None, close: float) -> list[dict]:
    """Plain-English clauses, each derived from a value that actually fed the
    score. `kind` is "support" for a reason the stock ranked well and "caution"
    for a genuine negative — cautions are never suppressed, since a shortlist
    that only lists positives is not an analysis.
    """
    out: list[dict] = []

    if ind is not None:
        dma_50 = float(ind.dma_50) if ind.dma_50 is not None else None
        dma_200 = float(ind.dma_200) if ind.dma_200 is not None else None
        rsi = float(ind.rsi_14) if ind.rsi_14 is not None else None
        macd_hist = float(ind.macd_hist) if ind.macd_hist is not None else None
        vol_ratio = float(ind.volume_ratio) if ind.volume_ratio is not None else None
        beta = float(ind.beta) if ind.beta is not None else None
        mdd = float(ind.max_drawdown) if ind.max_drawdown is not None else None

        if dma_50 is not None:
            gap = _pct(close, dma_50)
            if gap >= 0:
                out.append({"kind": "support", "label": "Trend",
                            "text": f"Trading {gap:.1f}% above its 50-day average (₹{dma_50:,.2f})."})
            else:
                out.append({"kind": "caution", "label": "Trend",
                            "text": f"Trading {abs(gap):.1f}% below its 50-day average (₹{dma_50:,.2f})."})

        if dma_50 is not None and dma_200 is not None:
            if dma_50 > dma_200:
                out.append({"kind": "support", "label": "Trend",
                            "text": "50-day average sits above the 200-day — the longer-term trend is up."})
            else:
                out.append({"kind": "caution", "label": "Trend",
                            "text": "50-day average sits below the 200-day — the longer-term trend is still down."})

        if rsi is not None:
            if rsi >= RSI_OVERBOUGHT:
                out.append({"kind": "caution", "label": "Momentum",
                            "text": f"RSI {rsi:.0f} — overbought, so it may be extended in the short term."})
            elif rsi <= RSI_OVERSOLD:
                out.append({"kind": "caution", "label": "Momentum",
                            "text": f"RSI {rsi:.0f} — oversold, momentum is currently against it."})
            elif rsi >= RSI_STRONG_LOW:
                out.append({"kind": "support", "label": "Momentum",
                            "text": f"RSI {rsi:.0f} — momentum positive without being overbought."})
            else:
                out.append({"kind": "support", "label": "Momentum",
                            "text": f"RSI {rsi:.0f} — momentum neutral, with room to run before overbought."})

        if macd_hist is not None:
            if macd_hist > 0:
                out.append({"kind": "support", "label": "Momentum",
                            "text": "MACD histogram is positive — short-term momentum is improving."})
            else:
                out.append({"kind": "caution", "label": "Momentum",
                            "text": "MACD histogram is negative — short-term momentum has not turned yet."})

        if vol_ratio is not None and vol_ratio >= VOLUME_SURGE_RATIO:
            out.append({"kind": "support", "label": "Volume",
                        "text": f"Volume running {vol_ratio:.1f}× its 20-day average — the move has participation."})

        if beta is not None:
            if beta <= LOW_BETA:
                out.append({"kind": "support", "label": "Risk",
                            "text": f"Beta {beta:.2f} — historically moves less than the index."})
            elif beta >= HIGH_BETA:
                out.append({"kind": "caution", "label": "Risk",
                            "text": f"Beta {beta:.2f} — historically moves more than the index, both ways."})

        if mdd is not None:
            out.append({"kind": "caution", "label": "Risk",
                        "text": f"Has fallen {abs(mdd) * 100:.1f}% peak-to-trough over the past year."})

    fund = (
        db.query(Fundamentals)
        .filter(Fundamentals.stock_id == stock.id)
        .order_by(Fundamentals.as_of_date.desc())
        .first()
    )
    if fund is not None and fund.pe_ratio is not None:
        pe = float(fund.pe_ratio)
        median_pe = _sector_median_pe(db, stock.sector)
        if median_pe is not None and median_pe > 0:
            if pe < median_pe:
                out.append({"kind": "support", "label": "Valuation",
                            "text": f"P/E {pe:.1f} against a {stock.sector} median of {median_pe:.1f} — cheaper than its peers."})
            else:
                out.append({"kind": "caution", "label": "Valuation",
                            "text": f"P/E {pe:.1f} against a {stock.sector} median of {median_pe:.1f} — pricier than its peers."})
        else:
            out.append({"kind": "support", "label": "Valuation", "text": f"Trading at a P/E of {pe:.1f}."})

        if fund.revenue_growth is not None:
            growth = float(fund.revenue_growth) * 100
            kind = "support" if growth > 0 else "caution"
            direction = "grew" if growth > 0 else "shrank"
            out.append({"kind": kind, "label": "Growth",
                        "text": f"Revenue {direction} {abs(growth):.1f}% over the latest reported period."})

    return out


def compute_market_regime(db: Session, as_of: date_type) -> dict:
    """NIFTY 200DMA regime, point-in-time as of `as_of`.

    Same rule the backtest uses: close >= 200DMA is bull. Surfaced in the API so
    the UI can state the market state and the resulting exposure rather than
    silently publishing a full-size list during a defensive regime.
    """
    bench = fetch_price_history("^NSEI", period="2y")[["date", "close"]].sort_values("date")
    # Yahoo's row for the still-forming session (today, before intraday data
    # settles) frequently comes back with a NaN close. json.dumps cannot encode
    # NaN, so an un-dropped one here 500s the whole /daily-signals endpoint —
    # not a hypothetical, this took the endpoint down on 2026-08-19. Same
    # dropna the scheduler already applies to per-stock bars for the same reason.
    bench = bench.dropna(subset=["close"])
    bench = bench[bench["date"] <= as_of]
    if len(bench) < V1.regime_ma_days:
        return {"regime": "unknown", "exposure": 1.0, "nifty_close": None, "nifty_200dma": None}

    closes = bench["close"].astype(float)
    ma = float(closes.tail(V1.regime_ma_days).mean())
    close = float(closes.iloc[-1])
    is_bull = close >= ma
    return {
        "regime": "bull" if is_bull else "bear",
        "exposure": V1.bull_exposure if is_bull else V1.bear_exposure,
        "nifty_close": round(close, 2),
        "nifty_200dma": round(ma, 2),
        "as_of": bench["date"].iloc[-1],
    }


def _momentum_scores(db: Session, stock_ids: list[int], as_of: date_type) -> dict[int, float]:
    """12-month minus 1-month return, percentile-ranked 0-100 across the universe.

    Identical construction to app/services/backtest_scoring.py so a signal
    published in production matches what the backtest would have selected.
    """
    raw: dict[int, float] = {}
    for stock_id in stock_ids:
        rows = (
            db.query(PriceHistory.close)
            .filter(PriceHistory.stock_id == stock_id, PriceHistory.date <= as_of,
                    PriceHistory.close.isnot(None))
            .order_by(PriceHistory.date)
            .all()
        )
        closes = [float(r[0]) for r in rows]
        if len(closes) < V1.momentum_long_days + 1:
            continue
        last = closes[-1]
        long_ago = closes[-V1.momentum_long_days]
        if long_ago <= 0:
            continue
        total = last / long_ago - 1
        if V1.momentum_skip_days > 0:
            recent = closes[-V1.momentum_skip_days]
            if recent <= 0:
                continue
            total -= last / recent - 1
        raw[stock_id] = total

    if len(raw) < 2:
        return {}
    ranked = pd.Series(raw).rank(pct=True) * 100
    return {int(k): float(v) for k, v in ranked.items()}


def _gather_candidates(db: Session, as_of: date_type) -> list[Candidate]:
    """Rank the universe with EquityLens v1 (momentum), not the old composite.

    The composite ranking previously used here was measured in Phase 14 as
    close to inverted at short horizons; it is retained elsewhere for the
    research view but must not drive published signals.
    """
    stocks = {s.id: s for s in db.query(Stock).filter(Stock.is_active == True).all()}  # noqa: E712
    momentum = _momentum_scores(db, list(stocks.keys()), as_of)
    if not momentum:
        logger.warning("No momentum scores computable as of %s", as_of)
        return []

    candidates: list[Candidate] = []
    for stock_id, mom_score in sorted(momentum.items(), key=lambda kv: kv[1], reverse=True):
        if mom_score < MIN_OVERALL_SCORE:
            continue
        stock = stocks.get(stock_id)
        if stock is None:
            continue

        bar = _latest_priced_bar(db, stock_id)
        if bar is None:
            continue
        age = (as_of - bar.date).days
        if age > MAX_REFERENCE_AGE_DAYS:
            logger.info("Skipping %s — most recent close is %d days old", stock.symbol, age)
            continue

        # Frozen v1 exit geometry: 4 ATR, support stop OFF.
        levels = compute_levels_for_stock(db, stock_id, V1)
        if levels is None:
            logger.info("Skipping %s — levels not computable", stock.symbol)
            continue

        close = float(bar.close)
        score = StockScore(
            stock_id=stock_id,
            symbol=stock.symbol,
            technical_score=None,
            fundamental_score=None,
            valuation_score=None,
            risk_score=None,
            overall_score=round(mom_score, 2),
            signal=_map_signal(mom_score),
            missing_inputs={},
        )
        candidates.append(
            Candidate(
                score=score, stock=stock, levels=levels,
                reference_close=close, reference_date=bar.date,
                rationale=_build_rationale(db, stock, _latest_indicator(db, stock_id), close),
            )
        )

    return candidates


def _apply_sector_cap(candidates: list[Candidate]) -> list[Candidate]:
    """Rank by score, then take greedily under the per-sector cap. Same shape
    as portfolio construction, so the shortlist cannot be one sector deep.
    """
    ranked = sorted(candidates, key=lambda c: c.score.overall_score, reverse=True)
    per_sector: dict[str | None, int] = {}
    selected: list[Candidate] = []

    for cand in ranked:
        sector = cand.stock.sector
        if per_sector.get(sector, 0) >= MAX_PER_SECTOR:
            continue
        per_sector[sector] = per_sector.get(sector, 0) + 1
        selected.append(cand)
        if len(selected) >= MAX_SIGNALS:
            break

    return selected


def generate_daily_signals(db: Session, as_of: date_type | None = None) -> list[DailySignal]:
    """Build and persist the shortlist for `as_of` (default today, IST-agnostic
    — the caller decides the date). Re-running for a date replaces that date's
    rows, so a manual re-run after a data fix does not leave two shortlists.
    """
    as_of = as_of or date_type.today()

    candidates = _gather_candidates(db, as_of)
    selected = _apply_sector_cap(candidates)

    db.query(DailySignal).filter(DailySignal.date == as_of).delete(synchronize_session=False)

    rows: list[DailySignal] = []
    for rank, cand in enumerate(selected, start=1):
        row = DailySignal(
            date=as_of,
            stock_id=cand.stock.id,
            rank=rank,
            technical_score=cand.score.technical_score,
            fundamental_score=cand.score.fundamental_score,
            valuation_score=cand.score.valuation_score,
            risk_score=cand.score.risk_score,
            overall_score=cand.score.overall_score,
            signal=cand.score.signal,
            reference_close=round(cand.reference_close, 2),
            reference_date=cand.reference_date,
            entry_low=round(cand.levels.entry_low, 2),
            entry_high=round(cand.levels.entry_high, 2),
            stop_loss=round(cand.levels.stop_loss, 2),
            target_price=round(cand.levels.target_price, 2),
            risk_reward=round(cand.levels.risk_reward, 4),
            stop_method=cand.levels.stop_method,
            rationale=cand.rationale,
        )
        db.add(row)
        rows.append(row)

    db.flush()
    logger.info(
        "Daily signals for %s: %d scored, %d passed the gate, %d published",
        as_of, len(candidates), len(candidates), len(rows),
    )
    return rows


def get_daily_signals(db: Session, as_of: date_type | None = None) -> tuple[date_type | None, list[DailySignal]]:
    """Return the shortlist for `as_of`, or the most recent published date if
    `as_of` is None. Returns (date, rows) — the date is what was actually
    found, which may be older than today if the market has been shut.
    """
    if as_of is None:
        latest = db.query(DailySignal.date).order_by(DailySignal.date.desc()).first()
        if latest is None:
            return None, []
        as_of = latest[0]

    rows = (
        db.query(DailySignal)
        .filter(DailySignal.date == as_of)
        .order_by(DailySignal.rank.asc())
        .all()
    )
    return as_of, rows


def available_dates(db: Session, limit: int = 30) -> list[date_type]:
    rows = (
        db.query(DailySignal.date)
        .distinct()
        .order_by(DailySignal.date.desc())
        .limit(limit)
        .all()
    )
    return [r[0] for r in rows]


def trigger_state(latest_price: float | None, entry_low: float, entry_high: float) -> str:
    """Where the live price sits relative to the frozen entry zone. Computed at
    read time, not stored — the zone is the fixed part of the call, the price
    is not.
    """
    if latest_price is None:
        return "UNKNOWN"
    if latest_price < entry_low:
        return "BELOW_ZONE"
    if latest_price > entry_high:
        return "ABOVE_ZONE"
    return "IN_ZONE"


def stale_reference_cutoff(as_of: date_type) -> date_type:
    return as_of - timedelta(days=MAX_REFERENCE_AGE_DAYS)


def get_momentum_leaders(db: Session) -> tuple[date_type | None, int, list[dict]]:
    market_through = db.query(func.max(PriceHistory.date)).scalar()
    if not market_through:
        return None, 0, []

    stocks = {s.id: s for s in db.query(Stock).filter(Stock.is_active == True).all()}  # noqa: E712
    momentum = _momentum_scores(db, list(stocks.keys()), market_through)
    
    sorted_momentum = sorted(momentum.items(), key=lambda kv: kv[1], reverse=True)
    
    prices = {
        r.stock_id: float(r.close) 
        for r in db.query(PriceHistory.stock_id, PriceHistory.close)
        .filter(PriceHistory.date == market_through, PriceHistory.close.isnot(None)).all()
    }
    from app.core.cache import get_live_prices
    live = get_live_prices() or {}
    
    leaders = []
    for rank, (stock_id, mom_score) in enumerate(sorted_momentum, start=1):
        stock = stocks.get(stock_id)
        if not stock:
            continue
        quote = live.get(stock.symbol)
        price = quote["price"] if quote else prices.get(stock_id)
        
        leaders.append({
            "symbol": stock.symbol,
            "company_name": stock.company_name,
            "sector": stock.sector,
            "price": price,
            "momentum_percentile": round(mom_score, 2),
            "rank": rank
        })
        
    return market_through, len(stocks), leaders
