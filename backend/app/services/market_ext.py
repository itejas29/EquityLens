from datetime import date as date_type
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.services.market import _SESSION_SQL, _row_to_mover
from app.models.stock import Stock
from app.models.fundamentals import Fundamentals
from app.models.indicator import Indicator
from app.services.daily_signals import _momentum_scores

def get_sectors(db: Session) -> list[dict]:
    rows = db.execute(_SESSION_SQL).fetchall()
    movers = [_row_to_mover(r) for r in rows]
    
    market_through = max((r.as_of for r in rows), default=None)
    if not market_through:
        return []
        
    stocks = {s.id: s for s in db.query(Stock).filter(Stock.is_active == True).all()}
    momentum = _momentum_scores(db, list(stocks.keys()), market_through)
    
    sectors_data = {}
    for m in movers:
        if not m.sector:
            continue
        if m.sector not in sectors_data:
            sectors_data[m.sector] = {
                "sector": m.sector,
                "stocks_count": 0,
                "advancing": 0,
                "declining": 0,
                "sum_change_pct": 0.0,
                "movers": []
            }
        
        sd = sectors_data[m.sector]
        sd["stocks_count"] += 1
        if m.change_pct > 0:
            sd["advancing"] += 1
        elif m.change_pct < 0:
            sd["declining"] += 1
        sd["sum_change_pct"] += m.change_pct
        
        stock_id = next((k for k, v in stocks.items() if v.symbol == m.symbol), None)
        mom = momentum.get(stock_id, 0.0) if stock_id else 0.0
        
        sd["movers"].append({
            "symbol": m.symbol,
            "company_name": m.company_name,
            "price": m.close,
            "momentum_percentile": round(mom, 2)
        })
        
    result = []
    for sec, data in sectors_data.items():
        avg_ret = data["sum_change_pct"] / data["stocks_count"] if data["stocks_count"] > 0 else 0.0
        sorted_movers = sorted(data["movers"], key=lambda x: x["momentum_percentile"], reverse=True)
        top_3 = sorted_movers[:3]
        
        result.append({
            "sector": sec,
            "stocks_count": data["stocks_count"],
            "advancing": data["advancing"],
            "declining": data["declining"],
            "average_change_pct": round(avg_ret, 2),
            "top_momentum_stocks": top_3
        })
        
    return sorted(result, key=lambda x: x["average_change_pct"], reverse=True)


def get_discover_sections(db: Session, limit: int = 20) -> dict:
    rows = db.execute(_SESSION_SQL).fetchall()
    movers = [_row_to_mover(r) for r in rows]
    
    market_through = max((r.as_of for r in rows), default=None)
    if not market_through:
        return {}
        
    stocks = {s.id: s for s in db.query(Stock).filter(Stock.is_active == True).all()}
    momentum = _momentum_scores(db, list(stocks.keys()), market_through)
    
    # Pull volume ratios from latest indicators
    indicators = db.query(Indicator.stock_id, Indicator.volume_ratio).filter(
        Indicator.date == market_through
    ).all()
    vol_ratios = {r[0]: float(r[1]) if r[1] else 0.0 for r in indicators}
    
    import datetime
    start_date = market_through - datetime.timedelta(days=365)
    sql = text("""
        SELECT stock_id, MAX(close) as high_52w
        FROM price_history
        WHERE date >= :start_date AND close IS NOT NULL
        GROUP BY stock_id
    """)
    highs = {r.stock_id: float(r.high_52w) for r in db.execute(sql, {"start_date": start_date}).fetchall()}
            
    # Compute
    near_52w = []
    strong_mom_vol = []
    
    for m in movers:
        stock_id = next((k for k, v in stocks.items() if v.symbol == m.symbol), None)
        if not stock_id:
            continue
            
        mom = momentum.get(stock_id, 0.0)
        vr = vol_ratios.get(stock_id, 0.0)
        high_52w = highs.get(stock_id)
        
        # Near 52W High (within 5%)
        if high_52w and high_52w > 0:
            dist = (high_52w - m.close) / high_52w
            if 0 <= dist <= 0.05:
                near_52w.append({
                    "symbol": m.symbol,
                    "company_name": m.company_name,
                    "sector": m.sector,
                    "price": m.close,
                    "distance_pct": round(dist * 100, 2),
                    "momentum_percentile": round(mom, 2)
                })
                
        # Strong Mom + Vol (Mom > 80, Vol > 1.5)
        if mom > 80 and vr > 1.5:
            strong_mom_vol.append({
                "symbol": m.symbol,
                "company_name": m.company_name,
                "sector": m.sector,
                "price": m.close,
                "volume_ratio": round(vr, 2),
                "momentum_percentile": round(mom, 2)
            })
            
    near_52w = sorted(near_52w, key=lambda x: x["distance_pct"])[:limit]
    strong_mom_vol = sorted(strong_mom_vol, key=lambda x: x["momentum_percentile"], reverse=True)[:limit]
    
    return {
        "near_52w_high": near_52w,
        "strong_momentum_volume": strong_mom_vol
    }

