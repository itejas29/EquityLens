from datetime import date

import pandas as pd
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.fundamentals import Fundamentals
from app.models.indicator import Indicator
from app.models.price_history import PriceHistory
from app.models.recommendation import Recommendation
from app.models.stock import Stock
from app.services.scoring import StockScore

INDICATOR_COLUMNS = (
    "dma_50",
    "dma_200",
    "rsi_14",
    "macd",
    "macd_signal",
    "macd_hist",
    "volume_ratio",
    "volatility",
    "beta",
    "max_drawdown",
)


def upsert_stock(db: Session, symbol: str, meta: dict) -> Stock:
    stock = db.query(Stock).filter(Stock.symbol == symbol).first()
    if stock is None:
        stock = Stock(symbol=symbol, is_active=True)
        db.add(stock)

    stock.company_name = meta.get("company_name")
    stock.sector = meta.get("sector")
    stock.industry = meta.get("industry")
    stock.market_cap = meta.get("market_cap")
    db.flush()
    return stock


def upsert_price_history(db: Session, stock_id: int, history: pd.DataFrame) -> int:
    if history.empty:
        return 0

    rows = [
        {
            "stock_id": stock_id,
            "date": row.date,
            "open": _clean_float(row.open),
            "high": _clean_float(row.high),
            "low": _clean_float(row.low),
            "close": _clean_float(row.close),
            "volume": _clean_int(row.volume),
        }
        for row in history.itertuples(index=False)
    ]

    stmt = insert(PriceHistory).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["stock_id", "date"],
        set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "volume": stmt.excluded.volume,
        },
    )
    db.execute(stmt)
    db.flush()
    return len(rows)


def upsert_fundamentals(db: Session, stock_id: int, data: dict, as_of_date: date) -> None:
    row = {"stock_id": stock_id, "as_of_date": as_of_date, **data}
    stmt = insert(Fundamentals).values(**row)
    stmt = stmt.on_conflict_do_update(
        index_elements=["stock_id", "as_of_date"],
        set_={key: stmt.excluded[key] for key in data},
    )
    db.execute(stmt)
    db.flush()


def create_recommendation(db: Session, score: StockScore, levels=None) -> Recommendation:
    """Append a new recommendation row (recommendations has no unique
    constraint — each scoring run is a point-in-time snapshot kept in
    history, not upserted over the previous one). `levels` is an optional
    app.services.levels.Levels — entry/stop/target stay NULL if it wasn't
    computable (e.g. today's close missing) rather than left half-filled."""
    rec = Recommendation(
        stock_id=score.stock_id,
        technical_score=score.technical_score,
        fundamental_score=score.fundamental_score,
        valuation_score=score.valuation_score,
        risk_score=score.risk_score,
        overall_score=score.overall_score,
        signal=score.signal,
        missing_inputs=score.missing_inputs,
        entry_low=levels.entry_low if levels else None,
        entry_high=levels.entry_high if levels else None,
        target_price=levels.target_price if levels else None,
        stop_loss=levels.stop_loss if levels else None,
        risk_reward=levels.risk_reward if levels else None,
        stop_method=levels.stop_method if levels else None,
    )
    db.add(rec)
    db.flush()
    return rec


def upsert_indicators(db: Session, stock_id: int, indicators: pd.DataFrame) -> int:
    if indicators.empty:
        return 0

    rows = [
        {
            "stock_id": stock_id,
            "date": row.date,
            **{col: _clean_float(getattr(row, col)) for col in INDICATOR_COLUMNS},
        }
        for row in indicators.itertuples(index=False)
    ]

    stmt = insert(Indicator).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["stock_id", "date"],
        set_={col: getattr(stmt.excluded, col) for col in INDICATOR_COLUMNS},
    )
    db.execute(stmt)
    db.flush()
    return len(rows)


def _clean_float(value) -> float | None:
    return None if pd.isna(value) else float(value)


def _clean_int(value) -> int | None:
    return None if pd.isna(value) else int(value)
