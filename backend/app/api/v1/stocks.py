from datetime import date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.cache import (
    get_stock_detail_cache,
    invalidate_scored_universe_cache,
    invalidate_stock_detail_cache,
    set_stock_detail_cache,
)
from app.core.database import get_db
from app.core.exceptions import AppError
from app.core.rate_limit import rate_limit_analysis
from app.models.fundamentals import Fundamentals
from app.models.indicator import Indicator
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.schemas.indicator import ComputeIndicatorsResponse, IndicatorPoint
from app.schemas.recommendation import RecommendationResponse
from app.schemas.stock import (
    CatalogueLoadResponse,
    IngestResponse,
    PricePoint,
    RefreshResponse,
    SearchHit,
    SearchResponse,
    StockDetail,
    StockListResponse,
)
from app.services.catalogue import CatalogueError, catalogue_size, in_catalogue, load_catalogue, search_catalogue
from app.services.onboarding import IngestionError, ingest_symbol
from app.services.indicators import compute_indicators, fetch_benchmark_df, load_price_history_df
from app.services.levels import compute_levels_for_stock
from app.ml.predict import predict_probability
from app.services.ingestion import (
    create_recommendation,
    upsert_fundamentals,
    upsert_indicators,
    upsert_price_history,
    upsert_stock,
)
from app.services.market_data import SymbolNotFoundError, fetch_fundamentals, fetch_price_history, fetch_stock_meta
from app.services.scoring import get_cached_scored_universe

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("", response_model=StockListResponse)
def list_stocks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sector: str | None = Query(None),
    db: Session = Depends(get_db),
) -> StockListResponse:
    query = db.query(Stock)
    if sector:
        query = query.filter(Stock.sector == sector)

    total = query.count()
    items = query.order_by(Stock.symbol).offset((page - 1) * page_size).limit(page_size).all()
    return StockListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/screener")
def get_screener(db: Session = Depends(get_db)):
    from app.services.stocks_screener import get_screener_data
    return get_screener_data(db)


# NOTE: /search and /catalogue must be declared before /{symbol}, or FastAPI
# matches them as a symbol named "search" and returns a 404 instead.
@router.get("/search", response_model=SearchResponse)
def search_stocks(
    q: str = Query(..., min_length=1, description="Symbol or company name"),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
) -> SearchResponse:
    """Search every NSE-listed equity, not just the ingested ones.

    Hits are annotated with `is_ingested` so the client can distinguish "we
    have data on this" from "this exists and can be pulled on demand".
    """
    results = search_catalogue(db, q, limit=limit)
    return SearchResponse(
        query=q,
        count=len(results),
        catalogue_size=catalogue_size(db),
        results=[SearchHit(**r) for r in results],
    )


@router.post("/{symbol}/ingest", response_model=IngestResponse, status_code=status.HTTP_201_CREATED)
def ingest_stock(symbol: str, db: Session = Depends(get_db)) -> IngestResponse:
    """Pull a symbol's history on demand and make it analysable.

    Idempotent: re-ingesting an existing symbol just refreshes it. Rate-limited
    with the other market-data-hitting endpoints, since each call is several
    upstream requests.
    """
    symbol = symbol.upper()
    if in_catalogue(db, symbol) is None:
        raise AppError(
            f"'{symbol}' is not in the NSE equity catalogue. Check the ticker, or refresh the "
            "catalogue if it is a recent listing.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    try:
        _, report = ingest_symbol(db, symbol)
    except IngestionError as exc:
        db.rollback()
        raise AppError(exc.message, status_code=status.HTTP_404_NOT_FOUND)

    db.commit()
    invalidate_stock_detail_cache(symbol)
    # A new stock changes the population every percentile-based sub-score is
    # measured against, so the cached universe must not survive this.
    invalidate_scored_universe_cache()

    return IngestResponse(**report)


@router.post("/catalogue/refresh", response_model=CatalogueLoadResponse)
def refresh_catalogue(db: Session = Depends(get_db)) -> CatalogueLoadResponse:
    """Reload the NSE equity list. Cheap (one CSV) and the only way renamed or
    newly-listed tickers become searchable.
    """
    try:
        report = load_catalogue(db)
    except CatalogueError as exc:
        raise AppError(str(exc), status_code=status.HTTP_502_BAD_GATEWAY)
    db.commit()
    return CatalogueLoadResponse(**report)


def _get_stock_or_404(db: Session, symbol: str) -> Stock:
    stock = db.query(Stock).filter(Stock.symbol == symbol.upper()).first()
    if stock is None:
        # Distinguish "not a real ticker" from "real ticker, no data yet" —
        # the second is actionable by the client, the first is a typo.
        if in_catalogue(db, symbol) is not None:
            raise AppError(
                f"'{symbol.upper()}' is listed on NSE but has not been ingested yet. "
                f"POST /stocks/{symbol.upper()}/ingest to pull its history and analyse it.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        raise AppError(f"Stock '{symbol}' not found", status_code=status.HTTP_404_NOT_FOUND)
    return stock


@router.get("/{symbol}", response_model=StockDetail)
def get_stock(symbol: str, db: Session = Depends(get_db)) -> StockDetail:
    cached = get_stock_detail_cache(symbol)
    if cached is not None:
        return StockDetail.model_validate(cached)

    stock = _get_stock_or_404(db, symbol)

    latest_price = (
        db.query(PriceHistory).filter(PriceHistory.stock_id == stock.id).order_by(PriceHistory.date.desc()).first()
    )
    latest_fundamentals = (
        db.query(Fundamentals)
        .filter(Fundamentals.stock_id == stock.id)
        .order_by(Fundamentals.as_of_date.desc())
        .first()
    )

    detail = StockDetail(
        **{k: getattr(stock, k) for k in ("id", "symbol", "company_name", "sector", "industry", "market_cap", "is_active")},
        latest_price=latest_price,
        latest_fundamentals=latest_fundamentals,
    )
    set_stock_detail_cache(stock.symbol, detail.model_dump(mode="json"))
    return detail


@router.get("/{symbol}/prices", response_model=list[PricePoint])
def get_prices(
    symbol: str,
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None),
    db: Session = Depends(get_db),
) -> list[PriceHistory]:
    stock = _get_stock_or_404(db, symbol)

    query = db.query(PriceHistory).filter(PriceHistory.stock_id == stock.id)
    if from_:
        query = query.filter(PriceHistory.date >= from_)
    if to:
        query = query.filter(PriceHistory.date <= to)

    return query.order_by(PriceHistory.date).all()


@router.post("/{symbol}/refresh", response_model=RefreshResponse)
def refresh_stock(symbol: str, db: Session = Depends(get_db)) -> RefreshResponse:
    symbol = symbol.upper()
    try:
        meta = fetch_stock_meta(symbol)
        history = fetch_price_history(symbol)
        fundamentals = fetch_fundamentals(symbol)
    except SymbolNotFoundError:
        raise AppError(f"Symbol '{symbol}' not found on the market data provider", status_code=status.HTTP_404_NOT_FOUND)

    stock = upsert_stock(db, symbol, meta)
    rows_upserted = upsert_price_history(db, stock.id, history)
    upsert_fundamentals(db, stock.id, fundamentals["data"], date.today())
    db.commit()

    invalidate_stock_detail_cache(symbol)
    invalidate_scored_universe_cache()

    return RefreshResponse(
        symbol=symbol,
        price_rows_upserted=rows_upserted,
        fundamentals_missing_fields=fundamentals["missing_fields"],
    )


@router.post("/{symbol}/compute-indicators", response_model=ComputeIndicatorsResponse)
def compute_indicators_endpoint(symbol: str, db: Session = Depends(get_db)) -> ComputeIndicatorsResponse:
    stock = _get_stock_or_404(db, symbol)

    price_df = load_price_history_df(db, stock.id)
    if price_df.empty:
        raise AppError(f"No price history for '{symbol}' — refresh it first", status_code=status.HTTP_400_BAD_REQUEST)

    benchmark_df = fetch_benchmark_df()
    result = compute_indicators(price_df, benchmark_df)
    rows = upsert_indicators(db, stock.id, result)
    db.commit()

    invalidate_stock_detail_cache(symbol)
    invalidate_scored_universe_cache()

    return ComputeIndicatorsResponse(symbol=stock.symbol, rows_upserted=rows)


@router.get("/{symbol}/indicators", response_model=list[IndicatorPoint])
def get_indicators(
    symbol: str,
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None),
    db: Session = Depends(get_db),
) -> list[Indicator]:
    stock = _get_stock_or_404(db, symbol)

    query = db.query(Indicator).filter(Indicator.stock_id == stock.id)
    if from_:
        query = query.filter(Indicator.date >= from_)
    if to:
        query = query.filter(Indicator.date <= to)

    return query.order_by(Indicator.date).all()


@router.post("/{symbol}/score", response_model=RecommendationResponse, dependencies=[Depends(rate_limit_analysis)])
def score_stock(symbol: str, db: Session = Depends(get_db)) -> RecommendationResponse:
    stock = _get_stock_or_404(db, symbol)

    # Percentiles need the whole active universe as context, so scoring one
    # stock still scores everyone — only this stock's result gets persisted.
    scores_by_id = {s.stock_id: s for s in get_cached_scored_universe(db)}
    score = scores_by_id.get(stock.id)
    if score is None:
        raise AppError(f"'{symbol}' has no data to score", status_code=status.HTTP_400_BAD_REQUEST)

    levels = compute_levels_for_stock(db, stock.id)
    rec = create_recommendation(db, score, levels)
    db.commit()

    return RecommendationResponse(
        stock_id=rec.stock_id,
        symbol=stock.symbol,
        sector=stock.sector,
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
        ml_probability=predict_probability(db, stock.id),
    )
