import os
import sys
from datetime import date as date_type

sys.path.insert(0, "/Users/tejassingh/Desktop/College/Projects/EquityLens/backend")

from app.core.database import SessionLocal
from app.services.daily_signals import _momentum_scores
from app.models.stock import Stock
from app.models.price_history import PriceHistory
from sqlalchemy import func

db = SessionLocal()
market_through = db.query(func.max(PriceHistory.date)).scalar()
print(f"market_through: {market_through}")
stocks = {s.id: s for s in db.query(Stock).filter(Stock.is_active == True).all()}
print(f"Universe size: {len(stocks)}")

momentum = _momentum_scores(db, list(stocks.keys()), market_through)
print(f"Scored {len(momentum)} stocks")

sorted_momentum = sorted(momentum.items(), key=lambda kv: kv[1], reverse=True)

print("Top 10:")
for rank, (stock_id, mom_score) in enumerate(sorted_momentum[:10], start=1):
    print(f"{rank}. {stocks[stock_id].symbol} - {mom_score:.2f}")

