from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import AppError
from app.core.rate_limit import rate_limit_analysis
from app.core.security import get_current_user
from app.models.portfolio import Portfolio, PortfolioHolding
from app.models.price_history import PriceHistory
from app.models.stock import Stock
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
    holdings = db.query(PortfolioHolding).filter(PortfolioHolding.portfolio_id == portfolio.id).all()

    holding_responses: list[HoldingWithPnL] = []
    total_market_value = 0.0
    total_pnl = 0.0
    any_pnl = False

    for h in holdings:
        stock = db.query(Stock).filter(Stock.id == h.stock_id).first()
        latest_price = (
            db.query(PriceHistory).filter(PriceHistory.stock_id == h.stock_id).order_by(PriceHistory.date.desc()).first()
        )
        current_price = float(latest_price.close) if latest_price and latest_price.close is not None else None

        unrealized_pnl = None
        unrealized_pnl_pct = None
        # Only open positions get a P&L — portfolio_holdings has no exit_price
        # column, so a closed position's realized P&L genuinely can't be
        # computed without fabricating an exit price.
        if h.status == "open" and current_price is not None:
            entry_price = float(h.entry_price)
            unrealized_pnl = round((current_price - entry_price) * h.quantity, 2)
            unrealized_pnl_pct = round((current_price - entry_price) / entry_price * 100, 2)
            total_market_value += current_price * h.quantity
            total_pnl += unrealized_pnl
            any_pnl = True

        holding_responses.append(
            HoldingWithPnL(
                id=h.id,
                stock_id=h.stock_id,
                symbol=stock.symbol if stock else "?",
                sector=stock.sector if stock else None,
                quantity=h.quantity,
                entry_price=float(h.entry_price),
                allocated_amount=float(h.allocated_amount),
                stop_loss=float(h.stop_loss) if h.stop_loss is not None else None,
                target_price=float(h.target_price) if h.target_price is not None else None,
                status=h.status,
                opened_at=h.opened_at,
                current_price=current_price,
                unrealized_pnl=unrealized_pnl,
                unrealized_pnl_pct=unrealized_pnl_pct,
            )
        )

    return SavedPortfolioResponse(
        id=portfolio.id,
        name=portfolio.name,
        capital=float(portfolio.capital),
        risk_appetite=portfolio.risk_appetite,
        horizon=portfolio.horizon,
        created_at=portfolio.created_at,
        holdings=holding_responses,
        total_market_value=round(total_market_value, 2),
        total_unrealized_pnl=round(total_pnl, 2) if any_pnl else None,
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
