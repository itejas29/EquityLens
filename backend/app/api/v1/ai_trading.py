from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.ai_trading_config import RUN_HOUR, RUN_MINUTE
from app.core.daily_signals_config import MAX_SIGNALS
from app.core.database import get_db
from app.core.market_hours import IST
from app.core.security import get_current_user
from app.models.ai_trading_log import AITradingLog
from app.models.ai_trading_run import AITradingRun
from app.models.paper_trading import PaperTrade
from app.models.stock import Stock
from app.models.user import User
from app.schemas.ai_trading import (
    AIAccountResponse, AIClosedTradeResponse, AIHistoryResponse, AIHistoryStats, AIHoldingResponse,
    AILastRunResponse, AILogEntryResponse, AILogResponse, AIPositionResponse, AIRegimeResponse,
    AIStatusResponse, AIToggleRequest, AITradeResponse,
)
from app.services.ai_trading import REGIME_LABEL, get_ai_trader_account, is_enabled, set_enabled
from app.services.daily_signals import compute_market_regime
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
            # None stays None: a snapshot with no NIFTY figure must not plot as 0%.
            "daily_return": float(s.daily_return) if s.daily_return is not None else None,
            "cumulative_return": float(s.cumulative_return) if s.cumulative_return is not None else None,
            "nifty_return": float(s.nifty_return) if s.nifty_return is not None else None,
            "drawdown": float(s.drawdown),
        }
        for s in snapshots
    ]


# --- Dashboard endpoints (AI Trading page) -----------------------------------
# Same float-at-the-boundary rule as above: Decimal behind, numbers in JSON.

def _f(value) -> float | None:
    return None if value is None else float(value)


def _next_run(now: datetime) -> datetime:
    """The next weekday 09:20 IST strictly after `now`, or today's if it is
    still ahead. Weekdays only — the app has no exchange-holiday calendar."""
    candidate = now.replace(hour=RUN_HOUR, minute=RUN_MINUTE, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


@router.get("/status", response_model=AIStatusResponse)
def ai_status(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AIStatusResponse:
    account = get_ai_trader_account(db)
    db.commit()
    now = datetime.now(IST)
    regime = compute_market_regime(db, now.date())
    close, ma = regime.get("nifty_close"), regime.get("nifty_200dma")
    last = db.query(AITradingRun).order_by(AITradingRun.run_date.desc()).first()
    return AIStatusResponse(
        enabled=is_enabled(db),
        paper_only=True,
        regime=AIRegimeResponse(
            label=REGIME_LABEL.get(regime.get("regime")), nifty_close=close, nifty_200dma=ma,
            distance_pct=round((close - ma) / ma * 100, 2) if close is not None and ma else None),
        last_run=None if last is None else AILastRunResponse(
            run_date=last.run_date, status=last.status, finished_at=last.finished_at,
            bought_count=last.bought_count, sold_count=last.sold_count),
        next_run_at=_next_run(now),
        max_positions=MAX_SIGNALS,
        open_positions=db.query(PaperTrade).filter(
            PaperTrade.account_id == account.id, PaperTrade.status == "open").count(),
    )


@router.post("/toggle", response_model=AIStatusResponse)
def ai_toggle(payload: AIToggleRequest, current_user: User = Depends(get_current_user),
              db: Session = Depends(get_db)) -> AIStatusResponse:
    set_enabled(db, payload.enabled, current_user.email, datetime.now(IST).date())
    db.commit()
    return ai_status(current_user, db)


@router.get("/log", response_model=AILogResponse)
def ai_log(limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0),
           current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AILogResponse:
    query = db.query(AITradingLog)
    rows = query.order_by(AITradingLog.id.desc()).offset(offset).limit(limit).all()
    return AILogResponse(total=query.count(), entries=[
        AILogEntryResponse(id=r.id, created_at=r.created_at, run_date=r.run_date, action=r.action,
                           symbol=r.symbol, quantity=r.quantity, price=_f(r.price), reason=r.reason)
        for r in rows])


@router.get("/positions", response_model=list[AIPositionResponse])
def ai_positions(current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> list[AIPositionResponse]:
    account = get_ai_trader_account(db)
    summary = get_account_summary(db, account.user_id)
    db.commit()
    today = datetime.now(IST).date()
    out = []
    for h in summary.holdings:
        t, price = h.trade, h.current_price
        entered = t.executed_at.astimezone(IST).date() if t.executed_at.tzinfo else t.executed_at.date()
        out.append(AIPositionResponse(
            symbol=h.symbol, quantity=t.quantity, entry_date=entered, entry_price=float(t.price),
            current_price=_f(price), unrealized_pnl=_f(h.unrealized_pnl),
            unrealized_pnl_pct=_f(h.unrealized_pnl_pct), days_held=(today - entered).days,
            stop_loss=_f(t.stop_loss), target_price=_f(t.target_price),
            stop_distance_pct=_f((price - t.stop_loss) / price * 100)
            if price and t.stop_loss is not None else None,
            target_distance_pct=_f((t.target_price - price) / price * 100)
            if price and t.target_price is not None else None))
    return out


@router.get("/history", response_model=AIHistoryResponse)
def ai_history(exit_reason: str | None = Query(None, pattern="^(stop|target|horizon|regime)$"),
               limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0),
               current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AIHistoryResponse:
    account = get_ai_trader_account(db)
    db.commit()
    query = (db.query(PaperTrade, Stock.symbol).join(Stock, Stock.id == PaperTrade.stock_id)
             .filter(PaperTrade.account_id == account.id, PaperTrade.status == "closed"))
    if exit_reason:
        query = query.filter(PaperTrade.exit_reason == exit_reason)
    closed = query.order_by(PaperTrade.exit_at.desc()).all()

    def view(t, sym) -> AIClosedTradeResponse:
        basis = Decimal(t.cost_basis)
        return AIClosedTradeResponse(
            symbol=sym, entry_date=t.executed_at.date(), exit_date=t.exit_at.date(),
            entry_price=float(t.price), exit_price=float(t.exit_price), pnl=float(t.pnl),
            pnl_pct=float(t.pnl / basis * 100) if basis > 0 else None,
            holding_days=(t.exit_at.date() - t.executed_at.date()).days, exit_reason=t.exit_reason)

    rows = [view(t, sym) for t, sym in closed]
    # Stats cover every closed trade that matches the filter, not just this page.
    wins = [r for r in rows if r.pnl > 0]
    losses = [r for r in rows if r.pnl < 0]
    gross_win, gross_loss = sum(r.pnl for r in wins), -sum(r.pnl for r in losses)

    def mean(values):
        values = [v for v in values if v is not None]
        return round(sum(values) / len(values), 2) if values else None

    stats = AIHistoryStats(
        total_trades=len(rows),
        win_rate_pct=round(len(wins) / len(rows) * 100, 1) if rows else None,
        avg_win_pct=mean(r.pnl_pct for r in wins), avg_loss_pct=mean(r.pnl_pct for r in losses),
        # Undefined with no losing trade: left null rather than shown as infinity.
        profit_factor=round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        avg_holding_days=mean(r.holding_days for r in rows))
    return AIHistoryResponse(stats=stats, total=len(rows), trades=rows[offset:offset + limit])
