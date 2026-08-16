from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import AppError
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.paper_trading import (
    PaperAccountResponse,
    PaperBuyRequest,
    PaperHoldingResponse,
    PaperSellRequest,
    PaperTradeResponse,
)
from app.services.paper_trading import PaperTradingError, buy, get_account_summary, sell

router = APIRouter(prefix="/paper", tags=["paper-trading"])


def _trade_response(trade, symbol: str) -> PaperTradeResponse:
    return PaperTradeResponse(
        id=trade.id,
        symbol=symbol,
        side=trade.side,
        quantity=trade.quantity,
        price=float(trade.price),
        executed_at=trade.executed_at,
        status=trade.status,
        exit_price=float(trade.exit_price) if trade.exit_price is not None else None,
        exit_at=trade.exit_at,
        pnl=float(trade.pnl) if trade.pnl is not None else None,
    )


@router.post("/buy", response_model=PaperTradeResponse, status_code=status.HTTP_201_CREATED)
def paper_buy(
    payload: PaperBuyRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> PaperTradeResponse:
    try:
        trade = buy(db, current_user.id, payload.symbol, payload.quantity)
        db.commit()
    except PaperTradingError as exc:
        db.rollback()
        raise AppError(exc.message, status_code=status.HTTP_400_BAD_REQUEST)

    return _trade_response(trade, payload.symbol.upper())


@router.post("/sell", response_model=PaperTradeResponse)
def paper_sell(
    payload: PaperSellRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> PaperTradeResponse:
    try:
        trade = sell(db, current_user.id, payload.symbol)
        db.commit()
    except PaperTradingError as exc:
        db.rollback()
        raise AppError(exc.message, status_code=status.HTTP_400_BAD_REQUEST)

    return _trade_response(trade, payload.symbol.upper())


@router.get("/account", response_model=PaperAccountResponse)
def paper_account(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> PaperAccountResponse:
    summary = get_account_summary(db, current_user.id)
    db.commit()

    holdings = [
        PaperHoldingResponse(
            trade_id=h.trade.id,
            symbol=h.symbol,
            quantity=h.trade.quantity,
            entry_price=float(h.trade.price),
            current_price=h.current_price,
            unrealized_pnl=h.unrealized_pnl,
            unrealized_pnl_pct=h.unrealized_pnl_pct,
        )
        for h in summary.holdings
    ]

    return PaperAccountResponse(
        virtual_capital=float(summary.account.virtual_capital),
        cash=float(summary.account.cash),
        equity=summary.equity,
        market_value=summary.market_value,
        realized_pnl=summary.realized_pnl,
        unrealized_pnl=summary.unrealized_pnl,
        win_rate=summary.win_rate,
        current_drawdown_pct=summary.current_drawdown_pct,
        holdings=holdings,
    )


@router.get("/transactions", response_model=list[PaperTradeResponse])
def paper_transactions(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[PaperTradeResponse]:
    from app.models.paper_trading import PaperAccount, PaperTrade
    from app.models.stock import Stock
    
    account = db.query(PaperAccount).filter(PaperAccount.user_id == current_user.id).first()
    if not account:
        return []
        
    trades = (
        db.query(PaperTrade, Stock.symbol)
        .join(Stock, Stock.id == PaperTrade.stock_id)
        .filter(PaperTrade.account_id == account.id)
        .order_by(PaperTrade.executed_at.desc())
        .limit(100)
        .all()
    )
    
    return [_trade_response(t, sym) for t, sym in trades]


@router.get("/equity-curve")
def paper_equity_curve(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models.paper_trading import PaperAccount, PaperEquitySnapshot
    
    account = db.query(PaperAccount).filter(PaperAccount.user_id == current_user.id).first()
    if not account:
        return []
        
    snapshots = (
        db.query(PaperEquitySnapshot)
        .filter(PaperEquitySnapshot.account_id == account.id)
        .order_by(PaperEquitySnapshot.date.asc())
        .all()
    )
    
    return [
        {
            "date": s.date,
            "total_equity": float(s.total_equity),
            "cash": float(s.cash),
            "portfolio_value": float(s.portfolio_value),
            "daily_return": float(s.daily_return) if s.daily_return else 0,
            "cumulative_return": float(s.cumulative_return) if s.cumulative_return else 0,
            "nifty_return": float(s.nifty_return) if s.nifty_return else 0,
            "drawdown": float(s.drawdown)
        }
        for s in snapshots
    ]
