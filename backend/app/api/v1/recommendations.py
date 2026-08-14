from collections import Counter

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.recommendation import Recommendation
from app.models.stock import Stock
from app.schemas.recommendation import RecommendationResponse, RunUniverseResponse
from app.core.cache import invalidate_scored_universe_cache
from app.core.rate_limit import rate_limit_analysis
from app.ml.predict import predict_probability
from app.services.ingestion import create_recommendation
from app.services.levels import compute_levels_for_stock
from app.services.scoring import score_universe

scoring_router = APIRouter(prefix="/scoring", tags=["scoring"])
recommendations_router = APIRouter(tags=["recommendations"])


def _to_response(db: Session, rec: Recommendation, symbol: str, sector: str | None) -> RecommendationResponse:
    return RecommendationResponse(
        stock_id=rec.stock_id,
        symbol=symbol,
        sector=sector,
        generated_at=rec.generated_at,
        technical_score=rec.technical_score,
        fundamental_score=rec.fundamental_score,
        valuation_score=rec.valuation_score,
        risk_score=rec.risk_score,
        overall_score=rec.overall_score,
        signal=rec.signal,
        entry_low=rec.entry_low,
        entry_high=rec.entry_high,
        target_price=rec.target_price,
        stop_loss=rec.stop_loss,
        risk_reward=rec.risk_reward,
        stop_method=rec.stop_method,
        missing_inputs=rec.missing_inputs,
        ml_probability=predict_probability(db, rec.stock_id),
    )


@scoring_router.post("/run-universe", response_model=RunUniverseResponse, dependencies=[Depends(rate_limit_analysis)])
def run_universe(db: Session = Depends(get_db)) -> RunUniverseResponse:
    scores = score_universe(db)
    for score in scores:
        levels = compute_levels_for_stock(db, score.stock_id)
        create_recommendation(db, score, levels)
    db.commit()
    # Fresh scores just got written — drop the 1h cache so subsequent reads
    # (score_stock, portfolio/analyze) see this run, not a stale one.
    invalidate_scored_universe_cache()

    signal_distribution = Counter(s.signal or "NULL" for s in scores)
    return RunUniverseResponse(scored=len(scores), signal_distribution=dict(signal_distribution))


@recommendations_router.get(
    "/recommendations", response_model=list[RecommendationResponse], dependencies=[Depends(rate_limit_analysis)]
)
def list_recommendations(
    min_score: float | None = Query(None, ge=0, le=100),
    sector: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[RecommendationResponse]:
    # Latest recommendation per stock: join each stock to the max(generated_at)
    # row for it, rather than returning full history.
    latest_per_stock = (
        db.query(Recommendation.stock_id, func.max(Recommendation.generated_at).label("max_generated_at"))
        .group_by(Recommendation.stock_id)
        .subquery()
    )

    query = (
        db.query(Recommendation, Stock.symbol, Stock.sector)
        .join(Stock, Stock.id == Recommendation.stock_id)
        .join(
            latest_per_stock,
            (Recommendation.stock_id == latest_per_stock.c.stock_id)
            & (Recommendation.generated_at == latest_per_stock.c.max_generated_at),
        )
    )

    if min_score is not None:
        query = query.filter(Recommendation.overall_score >= min_score)
    if sector:
        query = query.filter(Stock.sector == sector)

    rows = query.order_by(Recommendation.overall_score.desc().nullslast()).limit(limit).all()
    return [_to_response(db, rec, symbol, sector_val) for rec, symbol, sector_val in rows]
