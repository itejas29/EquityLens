from fastapi import APIRouter

from app.api.v1 import auth, backtest, health, paper, portfolio, recommendations, stocks, watchlist

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(stocks.router)
api_router.include_router(recommendations.scoring_router)
api_router.include_router(recommendations.recommendations_router)
api_router.include_router(portfolio.router)
api_router.include_router(backtest.router)
api_router.include_router(watchlist.router)
api_router.include_router(paper.router)
