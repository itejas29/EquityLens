from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import AppError
from app.core.rate_limit import rate_limit_analysis
from app.core.security import get_current_user
from app.models.portfolio import Portfolio, PortfolioHolding
from app.models.user import User
from app.schemas.portfolio import (
    HoldingWithPnL,
    PortfolioAnalyzeRequest,
    PortfolioAnalyzeResponse,
    PortfolioHoldingResponse,
    SavedPortfolioResponse,
    SavePortfolioRequest,
)
from app.services.portfolio import PortfolioResult, build_portfolio
from app.services.saved_portfolio import value_portfolio

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


def _run_analysis(db: Session, payload: PortfolioAnalyzeRequest) -> PortfolioResult:
    return build_portfolio(
        db,
        capital=payload.capital,
        risk_appetite=payload.risk_appetite,
        horizon=payload.horizon,
        sectors=payload.sectors,
        max_stocks=payload.max_stocks,
        max_allocation_pct=payload.max_allocation_pct,
    )


@router.post("/analyze", response_model=PortfolioAnalyzeResponse, dependencies=[Depends(rate_limit_analysis)])
def analyze_portfolio(payload: PortfolioAnalyzeRequest, db: Session = Depends(get_db)) -> PortfolioAnalyzeResponse:
    result = _run_analysis(db, payload)
    return PortfolioAnalyzeResponse(
        holdings=[PortfolioHoldingResponse(**h.__dict__) for h in result.holdings],
        excluded=result.excluded,
        capital=result.capital,
        deployed_capital=result.deployed_capital,
        cash=result.cash,
        cash_pct=result.cash_pct,
        weighted_risk_score=result.weighted_risk_score,
        sector_breakdown=result.sector_breakdown,
    )


def _build_saved_response(db: Session, portfolio: Portfolio) -> SavedPortfolioResponse:
    """Translate a valuation into the response schema. The valuation itself —
    money, totals, queries — lives in services/saved_portfolio.py.

    float() is written out at every field rather than left to Pydantic's
    coercion: this is the API boundary, and it is the one place precision is
    deliberately dropped, so it should be visible.
    """
    valuation = value_portfolio(db, portfolio)

    holding_responses = [
        HoldingWithPnL(
            id=v.holding.id,
            stock_id=v.holding.stock_id,
            symbol=v.symbol,
            sector=v.sector,
            quantity=v.holding.quantity,
            entry_price=float(v.holding.entry_price),
            allocated_amount=float(v.holding.allocated_amount),
            stop_loss=float(v.holding.stop_loss) if v.holding.stop_loss is not None else None,
            target_price=float(v.holding.target_price) if v.holding.target_price is not None else None,
            status=v.holding.status,
            opened_at=v.holding.opened_at,
            current_price=float(v.current_price) if v.current_price is not None else None,
            unrealized_pnl=float(v.unrealized_pnl) if v.unrealized_pnl is not None else None,
            unrealized_pnl_pct=float(v.unrealized_pnl_pct) if v.unrealized_pnl_pct is not None else None,
        )
        for v in valuation.holdings
    ]

    return SavedPortfolioResponse(
        id=portfolio.id,
        name=portfolio.name,
        capital=float(portfolio.capital),
        risk_appetite=portfolio.risk_appetite,
        horizon=portfolio.horizon,
        created_at=portfolio.created_at,
        holdings=holding_responses,
        total_market_value=float(valuation.total_market_value),
        total_unrealized_pnl=(
            float(valuation.total_unrealized_pnl)
            if valuation.total_unrealized_pnl is not None else None
        ),
    )


@router.post("", response_model=SavedPortfolioResponse, status_code=status.HTTP_201_CREATED)
def save_portfolio(
    payload: SavePortfolioRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SavedPortfolioResponse:
    result = _run_analysis(db, payload)
    if not result.holdings:
        raise AppError("No stocks qualified for this portfolio configuration", status_code=status.HTTP_400_BAD_REQUEST)

    portfolio = Portfolio(
        user_id=current_user.id,
        name=payload.name,
        capital=payload.capital,
        risk_appetite=payload.risk_appetite,
        horizon=payload.horizon,
    )
    db.add(portfolio)
    db.flush()

    for h in result.holdings:
        db.add(
            PortfolioHolding(
                portfolio_id=portfolio.id,
                stock_id=h.stock_id,
                quantity=h.shares,
                entry_price=h.entry_high,
                allocated_amount=h.allocated_amount,
                stop_loss=h.stop_loss,
                target_price=h.target_price,
                status="open",
            )
        )
    db.commit()
    db.refresh(portfolio)

    return _build_saved_response(db, portfolio)


@router.get("", response_model=list[SavedPortfolioResponse])
def list_portfolios(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[SavedPortfolioResponse]:
    portfolios = (
        db.query(Portfolio).filter(Portfolio.user_id == current_user.id).order_by(Portfolio.created_at.desc()).all()
    )
    return [_build_saved_response(db, p) for p in portfolios]
