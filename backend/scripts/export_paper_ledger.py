"""Export the AI paper account's complete trade ledger and reconcile it. READ ONLY.

    python scripts/export_paper_ledger.py > paper_ledger.json

Runs inside a READ ONLY Postgres transaction: any code path that tried to write
raises instead of changing production. That is why this does not call
paper_trading.get_account_summary — it flushes.

Reconciliation uses the identity the money-ledger tests already pin
(tests/test_money_ledger.py::test_cash_ties_out_to_the_sum_of_cash_movements):

    cash == virtual_capital - sum(every cost_basis) + sum(every proceeds)

where proceeds is not stored but is cost_basis + pnl by construction, so for
the whole ledger it reduces to

    cash == virtual_capital - sum(open cost_basis) + sum(closed pnl)

computed in Decimal, compared exactly. A residual of anything but 0 is a
finding, not rounding.

Each trade is joined to the published DailySignal for the same stock on the
same IST date, which is how the AI cycle sources its entries (it runs at 09:20
IST after the 09:15 publish). A trade with no such signal keeps empty signal
fields rather than a guessed match. Fields the schema does not store — separate
fees, slippage, gross P&L, a strategy version per trade — are exported as null:
the stored entry price is cost-loaded, and splitting it back out would be
re-deriving numbers from today's constants.
"""

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.core.ai_trading_config import AI_TRADER_EMAIL  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.market_hours import IST  # noqa: E402
from app.models.daily_signal import DailySignal  # noqa: E402
from app.models.paper_trading import PaperAccount, PaperTrade  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.models.user import User  # noqa: E402


def _s(v):
    return None if v is None else str(v)


def main() -> None:
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))

        user = db.query(User).filter(User.email == AI_TRADER_EMAIL).one()
        account = db.query(PaperAccount).filter(PaperAccount.user_id == user.id).one()
        trades = (db.query(PaperTrade, Stock)
                  .join(Stock, Stock.id == PaperTrade.stock_id)
                  .filter(PaperTrade.account_id == account.id)
                  .order_by(PaperTrade.executed_at, PaperTrade.id).all())

        rows, open_basis, closed_pnl = [], Decimal("0"), Decimal("0")
        for trade, stock in trades:
            ist_day = trade.executed_at.astimezone(IST).date()
            signal = (db.query(DailySignal)
                      .filter(DailySignal.stock_id == stock.id, DailySignal.date == ist_day)
                      .one_or_none())
            if trade.status == "open":
                open_basis += trade.cost_basis
            elif trade.pnl is not None:
                closed_pnl += trade.pnl
            rows.append({
                "trade_id": trade.id,
                "symbol": stock.symbol,
                "sector": stock.sector,
                "side": trade.side,
                "status": trade.status,
                "signal_id": signal.id if signal else None,
                "signal_date": signal.date.isoformat() if signal else None,
                "signal_timestamp": signal.generated_at.isoformat() if signal else None,
                "signal_reference_close": _s(signal.reference_close) if signal else None,
                "signal_entry_low": _s(signal.entry_low) if signal else None,
                "signal_entry_high": _s(signal.entry_high) if signal else None,
                "entry_timestamp": trade.executed_at.isoformat(),
                "entry_price_cost_loaded": _s(trade.price),
                "quantity": trade.quantity,
                "cost_basis": _s(trade.cost_basis),
                "stop_level": _s(trade.stop_loss),
                "target_price": _s(trade.target_price),
                "exit_timestamp": trade.exit_at.isoformat() if trade.exit_at else None,
                "exit_price": _s(trade.exit_price),
                "exit_reason": trade.exit_reason,
                "net_pnl": _s(trade.pnl),
            })

        expected_cash = account.virtual_capital - open_basis + closed_pnl
        print(json.dumps({
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "account": {
                "account_id": account.id,
                "virtual_capital": _s(account.virtual_capital),
                "cash": _s(account.cash),
                "created_at": account.created_at.isoformat(),
            },
            "reconciliation": {
                "identity": "cash == virtual_capital - sum(open cost_basis) + sum(closed pnl)",
                "trades": len(rows),
                "open_trades": sum(r["status"] == "open" for r in rows),
                "closed_trades": sum(r["status"] == "closed" for r in rows),
                "sum_open_cost_basis": _s(open_basis),
                "sum_closed_pnl": _s(closed_pnl),
                "expected_cash": _s(expected_cash),
                "stored_cash": _s(account.cash),
                "residual": _s(account.cash - expected_cash),
                "trades_matched_to_a_signal": sum(r["signal_id"] is not None for r in rows),
            },
            "trades": rows,
        }, indent=2))
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    main()
