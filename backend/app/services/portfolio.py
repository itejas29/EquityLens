"""Greedy portfolio construction: score the universe, filter by appetite,
rank by overall_score, then walk the ranking allocating capital while
respecting sector caps, per-stock allocation caps, and a cash buffer.
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.portfolio_config import (
    CASH_BUFFER_PCT,
    MAX_ALLOCATION_PCT,
    MAX_STOCKS_PER_SECTOR,
    MAX_STOCKS_PREFERENCE,
    MIN_SCORE_THRESHOLD,
    MIN_STOCKS_PREFERENCE,
)
from app.models.stock import Stock
from app.services.levels import compute_levels_for_stock
from app.services.position_sizing import size_position
from app.services.scoring import get_cached_scored_universe


@dataclass
class PortfolioHolding:
    stock_id: int
    symbol: str
    sector: str | None
    overall_score: float | None
    signal: str | None
    shares: int
    entry_low: float
    entry_high: float
    stop_loss: float
    target_price: float
    risk_reward: float
    allocated_amount: float
    risk_amount: float
    allocation_capped: bool


@dataclass
class PortfolioResult:
    holdings: list[PortfolioHolding]
    excluded: list[dict] = field(default_factory=list)
    capital: float = 0.0
    deployed_capital: float = 0.0
    cash: float = 0.0
    cash_pct: float = 0.0
    weighted_risk_score: float | None = None
    sector_breakdown: dict[str, float] = field(default_factory=dict)


def _clamp_max_stocks(max_stocks: int | None) -> int:
    if max_stocks is None:
        return MAX_STOCKS_PREFERENCE
    return max(MIN_STOCKS_PREFERENCE, min(MAX_STOCKS_PREFERENCE, max_stocks))


def build_portfolio(
    db: Session,
    capital: float,
    risk_appetite: str,
    horizon: str | None = None,
    sectors: list[str] | None = None,
    max_stocks: int | None = None,
    max_allocation_pct: float | None = None,
) -> PortfolioResult:
    min_score = MIN_SCORE_THRESHOLD[risk_appetite]
    allocation_pct = max_allocation_pct if max_allocation_pct is not None else MAX_ALLOCATION_PCT[risk_appetite]
    max_allocation_per_stock = capital * allocation_pct
    cash_buffer = capital * CASH_BUFFER_PCT[risk_appetite]
    investable_capital = capital - cash_buffer
    stock_cap = _clamp_max_stocks(max_stocks)

    stocks_by_id = {s.id: s for s in db.query(Stock).filter(Stock.is_active == True)}  # noqa: E712

    scores = get_cached_scored_universe(db)
    candidates = [s for s in scores if s.overall_score is not None and s.overall_score >= min_score]
    if sectors:
        sector_set = set(sectors)
        candidates = [s for s in candidates if stocks_by_id[s.stock_id].sector in sector_set]
    candidates.sort(key=lambda s: s.overall_score, reverse=True)

    holdings: list[PortfolioHolding] = []
    excluded: list[dict] = []
    sector_counts: dict[str, int] = {}
    deployed = 0.0

    for score in candidates:
        if len(holdings) >= stock_cap:
            break

        stock = stocks_by_id[score.stock_id]
        sector = stock.sector or "Unknown"
        if sector_counts.get(sector, 0) >= MAX_STOCKS_PER_SECTOR:
            excluded.append({"symbol": score.symbol, "reason": f"sector cap reached ({sector})"})
            continue

        levels = compute_levels_for_stock(db, stock.id)
        if levels is None:
            excluded.append({"symbol": score.symbol, "reason": "no valid entry/stop levels"})
            continue

        remaining = investable_capital - deployed
        if remaining <= 0:
            excluded.append({"symbol": score.symbol, "reason": "no capital remaining"})
            continue

        position = size_position(
            entry=levels.entry_high,
            stop_loss=levels.stop_loss,
            capital=capital,
            risk_appetite=risk_appetite,
            max_allocation_per_stock=min(max_allocation_per_stock, remaining),
        )
        if not position.is_valid:
            excluded.append({"symbol": score.symbol, "reason": position.rejection_reason})
            continue

        holdings.append(
            PortfolioHolding(
                stock_id=stock.id,
                symbol=score.symbol,
                sector=stock.sector,
                overall_score=score.overall_score,
                signal=score.signal,
                shares=position.shares,
                entry_low=levels.entry_low,
                entry_high=levels.entry_high,
                stop_loss=levels.stop_loss,
                target_price=levels.target_price,
                risk_reward=levels.risk_reward,
                allocated_amount=position.allocated_amount,
                risk_amount=position.risk_amount,
                allocation_capped=position.allocation_capped,
            )
        )
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        deployed += position.allocated_amount

    sector_breakdown: dict[str, float] = {}
    for h in holdings:
        key = h.sector or "Unknown"
        sector_breakdown[key] = sector_breakdown.get(key, 0.0) + h.allocated_amount

    weighted_risk_score = None
    risk_weighted_holdings = [h for h in holdings if h.allocated_amount > 0]
    scores_by_id = {s.stock_id: s for s in scores}
    risk_scored = [
        (h.allocated_amount, scores_by_id[h.stock_id].risk_score)
        for h in risk_weighted_holdings
        if scores_by_id[h.stock_id].risk_score is not None
    ]
    if risk_scored:
        total_weight = sum(w for w, _ in risk_scored)
        weighted_risk_score = round(sum(w * r for w, r in risk_scored) / total_weight, 2)

    cash = round(capital - deployed, 2)
    return PortfolioResult(
        holdings=holdings,
        excluded=excluded,
        capital=capital,
        deployed_capital=round(deployed, 2),
        cash=cash,
        cash_pct=round(cash / capital, 4) if capital else 0.0,
        weighted_risk_score=weighted_risk_score,
        sector_breakdown={k: round(v, 2) for k, v in sector_breakdown.items()},
    )
