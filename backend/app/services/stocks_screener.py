from datetime import date as date_type
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.stock import Stock
from app.models.fundamentals import Fundamentals
from app.models.indicator import Indicator
from app.models.price_history import PriceHistory
from app.services.daily_signals import _momentum_scores

def get_screener_data(db: Session) -> list[dict]:
    # Market through
    market_through = db.query(PriceHistory.date).order_by(PriceHistory.date.desc()).first()
    if not market_through:
        return []
    market_through = market_through[0]
    
    # 1. Stocks
    stocks = {s.id: s for s in db.query(Stock).filter(Stock.is_active == True).all()}
    
    # 2. Momentum
    momentum = _momentum_scores(db, list(stocks.keys()), market_through)
    
    # 3. Prices
    sql = text("""
        WITH ranked AS (
            SELECT
                p.stock_id, p.date, p.close, p.volume,
                ROW_NUMBER() OVER (PARTITION BY p.stock_id ORDER BY p.date DESC) AS rn
            FROM price_history p
            JOIN stocks s ON s.id = p.stock_id
            WHERE s.is_active = TRUE AND p.close IS NOT NULL
        ),
        latest AS (SELECT * FROM ranked WHERE rn = 1),
        prior  AS (SELECT * FROM ranked WHERE rn = 2)
        SELECT
            latest.stock_id,
            latest.close,
            latest.volume,
            prior.close AS prev_close,
            (latest.close - prior.close) / prior.close * 100 AS change_pct
        FROM latest
        LEFT JOIN prior ON prior.stock_id = latest.stock_id
    """)
    prices = db.execute(sql).fetchall()
    prices_map = {r.stock_id: r for r in prices}
    
    # 4. Fundamentals (market cap) and 52w pos
    fundamentals = db.query(Fundamentals).filter(
        Fundamentals.as_of_date <= market_through
    ).order_by(Fundamentals.stock_id, Fundamentals.as_of_date.desc()).all()
    
    fund_map = {}
    for f in fundamentals:
        if f.stock_id not in fund_map:
            fund_map[f.stock_id] = f
            
    import datetime
    start_date = market_through - datetime.timedelta(days=365)
    sql_52w = text("""
        SELECT stock_id, MAX(close) as high_52w, MIN(close) as low_52w
        FROM price_history
        WHERE date >= :start_date AND close IS NOT NULL
        GROUP BY stock_id
    """)
    high_low_52w = {r.stock_id: {"high": float(r.high_52w), "low": float(r.low_52w)} for r in db.execute(sql_52w, {"start_date": start_date}).fetchall()}
            
    # Assemble
    result = []
    for sid, stock in stocks.items():
        p = prices_map.get(sid)
        f = fund_map.get(sid)
        mom = momentum.get(sid, 0.0)
        
        if not p:
            continue
            
        hl = high_low_52w.get(sid)
        high_52w = hl["high"] if hl else None
        low_52w = hl["low"] if hl else None
        
        pos_52w = None
        if high_52w and low_52w and high_52w > low_52w:
            pos_52w = (float(p.close) - low_52w) / (high_52w - low_52w) * 100
            
        result.append({
            "symbol": stock.symbol,
            "company_name": stock.company_name,
            "sector": stock.sector,
            "price": float(p.close),
            "change_pct": round(float(p.change_pct), 2) if p.change_pct else 0.0,
            "volume": int(p.volume) if p.volume else 0,
            "momentum_percentile": round(mom, 2),
            "market_cap": float(stock.market_cap) if stock.market_cap else None,
            "high_52w": high_52w,
            "low_52w": low_52w,
            "position_52w": round(pos_52w, 2) if pos_52w else None
        })
        
    return result
