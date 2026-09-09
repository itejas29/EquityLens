from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.ai_trading import AIAccountResponse, AIHoldingResponse, AITradeResponse
from app.services.ai_trading import get_ai_trader_account
from app.services.paper_trading import get_account_summary

router = APIRouter(prefix="/ai-trading", tags=["ai-trading"])


@router.get("/account", response_model=AIAccountResponse)
def ai_account(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AIAccountResponse:
    account = get_ai_trader_account(db)
    summary = get_account_summary(db, account.user_id)
    db.commit()

    # float() here, explicitly, is the API boundary: money is Decimal
    # everywhere behind it (see services/paper_trading.py). Pydantic would
    # coerce a Decimal into these float fields silently, which is exactly why
    # the conversion is written out — so the one place precision is dropped is
    # visible, and JSON stays a number rather than a quoted string.
    holdings = [
        AIHoldingResponse(
            trade_id=h.trade.id,
            symbol=h.symbol,
            quantity=h.trade.quantity,
            entry_price=float(h.trade.price),
            current_price=float(h.current_price) if h.current_price is not None else None,
            unrealized_pnl=float(h.unrealized_pnl) if h.unrealized_pnl is not None else None,
            unrealized_pnl_pct=float(h.unrealized_pnl_pct) if h.unrealized_pnl_pct is not None else None,
            stop_loss=float(h.trade.stop_loss) if h.trade.stop_loss is not None else None,
            target_price=float(h.trade.target_price) if h.trade.target_price is not None else None,
        )
        for h in summary.holdings
    ]

    return AIAccountResponse(
        virtual_capital=float(summary.account.virtual_capital),
        cash=float(summary.account.cash),
        equity=float(summary.equity),
        market_value=float(summary.market_value),
        realized_pnl=float(summary.realized_pnl),
        unrealized_pnl=float(summary.unrealized_pnl),
        win_rate=float(summary.win_rate) if summary.win_rate is not None else None,
        current_drawdown_pct=float(summary.current_drawdown_pct),
        holdings=holdings,
    )


@router.get("/transactions", response_model=list[AITradeResponse])
def ai_transactions(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[AITradeResponse]:
    from app.models.paper_trading import PaperTrade
    from app.models.stock import Stock

    account = get_ai_trader_account(db)
    db.commit()

    trades = (
        db.query(PaperTrade, Stock.symbol)
        .join(Stock, Stock.id == PaperTrade.stock_id)
        .filter(PaperTrade.account_id == account.id)
        .order_by(PaperTrade.executed_at.desc())
        .limit(100)
        .all()
    )

    return [
        AITradeResponse(
            id=t.id,
            symbol=sym,
            quantity=t.quantity,
            price=float(t.price),
            executed_at=t.executed_at,
            status=t.status,
            exit_price=float(t.exit_price) if t.exit_price is not None else None,
            exit_at=t.exit_at,
            pnl=float(t.pnl) if t.pnl is not None else None,
            stop_loss=float(t.stop_loss) if t.stop_loss is not None else None,
            target_price=float(t.target_price) if t.target_price is not None else None,
            entry_score=float(t.entry_score) if t.entry_score is not None else None,
            exit_reason=t.exit_reason,
        )
        for t, sym in trades
    ]


@router.get("/equity-curve")
def ai_equity_curve(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models.paper_trading import PaperEquitySnapshot

    account = get_ai_trader_account(db)
    db.commit()

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
            "drawdown": float(s.drawdown),
        }
        for s in snapshots
    ]
