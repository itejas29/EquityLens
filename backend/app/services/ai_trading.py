"""Automated execution of the V1 momentum strategy against a dedicated paper
account — no manual clicks, no LLM. Every decision here is one V1 already
makes deterministically (the shortlist, its entry zone, its stop/target, the
regime exposure rule); this module only decides WHEN to act on them.

Run once daily via scheduler.ai_trading_loop, shortly after the 09:15 IST
DailySignal publish. Sell pass first (frees cash/slots), then buy pass —
see AITradingCycleResult for what a caller gets back.
"""

from dataclasses import dataclass, field
from datetime import date as date_type

from sqlalchemy.orm import Session

from app.core.ai_trading_config import AI_TRADER_EMAIL, AI_TRADER_NAME
from app.core.daily_signals_config import MAX_SIGNALS
from app.core.security import hash_password
from app.core.v1_strategy import V1
from app.models.paper_trading import PaperAccount, PaperTrade
from app.models.stock import Stock
from app.models.user import User
from app.services.daily_signals import compute_market_regime, get_daily_signals, trigger_state
from app.services.paper_trading import (
    PaperTradingError,
    _mark_prices,
    buy,
    get_or_create_account,
    sell,
)


@dataclass
class AITradingCycleResult:
    as_of: date_type
    regime: str
    rebalanced: bool = False
    bought: list[dict] = field(default_factory=list)
    sold: list[dict] = field(default_factory=list)


def _is_rebalance_day(db: Session, as_of: date_type) -> bool:
    """V1 rebalances MONTHLY, and that cadence is load-bearing.

    backtest._rebalance_dates takes the first trading day of each month, and
    backtest.py gates BOTH the bear-regime exposure trim and all new entries
    behind it; only stop/target/horizon are evaluated every bar.

    The first live implementation ran the trim and the entries daily, on the
    reasoning that checking an exposure cap more often is strictly safer. It is
    not. With bear exposure capped at 25% and both legs firing daily, the loop
    becomes: trim to the cap, buy back under the cap, let marks drift, trim
    again — every day. Live did exactly that for nine consecutive bear-regime
    sessions (bought=1 sold=1 each day), churning the same names and closing
    HFCL at 223.87 against a 212.71 stop that never triggered, for -15,585.
    Positions could never reach their targets because the trim reached them
    first. It also meant production was running a cadence no backtest had ever
    validated.

    First run of a calendar month is the rebalance. Derived from AITradingRun
    rather than the calendar so a missed day (holiday, outage) doesn't skip the
    month's only rebalance — whichever day the loop first runs takes it. The
    caller creates this cycle's own row before calling, hence `< as_of`.
    """
    from app.models.ai_trading_run import AITradingRun

    prior_this_month = (
        db.query(AITradingRun)
        .filter(AITradingRun.run_date >= as_of.replace(day=1), AITradingRun.run_date < as_of)
        .first()
    )
    return prior_this_month is None


def get_ai_trader_account(db: Session) -> PaperAccount:
    """Get-or-create the system user + account the AI loop trades against.

    A dedicated user rather than a flag on an existing account: PaperAccount.user_id
    is unique=True (one account per user), so there is no column to repurpose without
    a schema change — a synthetic user needs none. The password hash is random and
    never surfaced; nobody is meant to log in as this account.
    """
    user = db.query(User).filter(User.email == AI_TRADER_EMAIL).first()
    if user is None:
        import secrets

        user = User(name=AI_TRADER_NAME, email=AI_TRADER_EMAIL, password_hash=hash_password(secrets.token_hex(32)))
        db.add(user)
        db.flush()
    return get_or_create_account(db, user.id)


def _open_trades(db: Session, account: PaperAccount) -> list[PaperTrade]:
    return db.query(PaperTrade).filter(PaperTrade.account_id == account.id, PaperTrade.status == "open").all()


def _sell_pass(db: Session, account: PaperAccount, user_id: int, as_of: date_type,
               regime: dict, is_rebalance: bool) -> list[dict]:
    results: list[dict] = []

    # Pass 1: each position's own stop/target/horizon — independent of every other holding.
    for t in _open_trades(db, account):
        stock = db.query(Stock).filter(Stock.id == t.stock_id).first()
        if stock is None:
            continue
        price = _mark_prices(db, [t.stock_id]).get(t.stock_id)
        if price is None:
            continue

        reason = None
        if t.stop_loss is not None and price <= float(t.stop_loss):
            reason = "stop"
        elif t.target_price is not None and price >= float(t.target_price):
            reason = "target"
        elif (as_of - t.executed_at.date()).days >= V1.horizon_days:
            reason = "horizon"

        if reason is not None:
            trade = sell(db, user_id, stock.symbol)
            trade.exit_reason = reason
            results.append({"symbol": stock.symbol, "reason": reason, "pnl": float(trade.pnl)})

    # Pass 2: regime exposure trim, weakest entry_score first, only if still over the
    # cap after pass 1 — and only on a rebalance day, matching backtest.py, which gates
    # this behind `today in rebalance_dates`. See _is_rebalance_day for what running it
    # daily actually cost.
    exposure_target = regime.get("exposure", 1.0)
    if is_rebalance and exposure_target < 1.0:
        open_trades = _open_trades(db, account)
        if open_trades:
            marks = _mark_prices(db, [t.stock_id for t in open_trades])
            invested = sum(marks.get(t.stock_id, 0.0) * t.quantity for t in open_trades)
            equity = float(account.cash) + invested
            target_invested = equity * exposure_target
            ordered = sorted(
                open_trades,
                key=lambda t: (t.entry_score is None, float(t.entry_score) if t.entry_score is not None else -1.0),
            )
            for t in ordered:
                if invested <= target_invested:
                    break
                stock = db.query(Stock).filter(Stock.id == t.stock_id).first()
                if stock is None:
                    continue
                price = marks.get(t.stock_id)
                trade = sell(db, user_id, stock.symbol)
                trade.exit_reason = "regime"
                invested -= (price or 0.0) * t.quantity
                results.append({"symbol": stock.symbol, "reason": "regime", "pnl": float(trade.pnl)})

    return results


def _buy_pass(db: Session, account: PaperAccount, user_id: int, as_of: date_type, regime: dict) -> list[dict]:
    signal_date, rows = get_daily_signals(db, as_of)
    if not rows:
        return []

    held_stock_ids = {t.stock_id for t in _open_trades(db, account)}
    slots_available = MAX_SIGNALS - len(held_stock_ids)
    if slots_available <= 0:
        return []

    open_trades = _open_trades(db, account)
    marks = _mark_prices(db, [t.stock_id for t in open_trades])
    invested = sum(marks.get(t.stock_id, 0.0) * t.quantity for t in open_trades)
    equity = float(account.cash) + invested
    exposure_target = regime.get("exposure", 1.0)
    target_invested = equity * exposure_target
    per_position_budget = equity / MAX_SIGNALS

    stocks = {s.id: s for s in db.query(Stock).filter(Stock.id.in_([r.stock_id for r in rows])).all()}
    candidate_prices = _mark_prices(db, [r.stock_id for r in rows if r.stock_id not in held_stock_ids])

    results: list[dict] = []
    for row in sorted(rows, key=lambda r: r.rank):
        if slots_available <= 0 or invested >= target_invested:
            break
        if row.stock_id in held_stock_ids:
            continue
        stock = stocks.get(row.stock_id)
        if stock is None:
            continue

        price = candidate_prices.get(row.stock_id)
        state = trigger_state(price, float(row.entry_low), float(row.entry_high))
        if state != "IN_ZONE" or price is None:
            continue

        qty = int(per_position_budget // price)
        if qty <= 0:
            continue

        try:
            trade = buy(db, user_id, stock.symbol, qty)
        except PaperTradingError:
            continue

        trade.stop_loss = row.stop_loss
        trade.target_price = row.target_price
        trade.entry_score = row.overall_score
        invested += price * qty
        slots_available -= 1
        results.append({"symbol": stock.symbol, "quantity": qty, "price": price})

    return results


def run_ai_trading_cycle(db: Session, as_of: date_type) -> AITradingCycleResult:
    account = get_ai_trader_account(db)
    user_id = db.query(PaperAccount.user_id).filter(PaperAccount.id == account.id).scalar()
    is_rebalance = _is_rebalance_day(db, as_of)

    regime = compute_market_regime(db, as_of)
    # Stop / target / horizon are per-position rules the backtest evaluates on every
    # bar, so they run every cycle. The regime trim and new entries are rebalance-only.
    sold = _sell_pass(db, account, user_id, as_of, regime, is_rebalance)
    bought = _buy_pass(db, account, user_id, as_of, regime) if is_rebalance else []

    return AITradingCycleResult(as_of=as_of, regime=regime.get("regime", "unknown"),
                                rebalanced=is_rebalance, bought=bought, sold=sold)
