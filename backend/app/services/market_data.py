import logging
import time

import pandas as pd
import yfinance as yf

from app.core.ingestion_status import IngestionStatus

logger = logging.getLogger(__name__)

NSE_SUFFIX = ".NS"

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 2.0

# Fields pulled directly from yfinance's Ticker.info. Indian large/mid-caps
# frequently have gaps here (esp. returnOnEquity, returnOnAssets) — any field
# not present or None from yfinance stays None per the no-synthetic-data rule.
FUNDAMENTAL_FIELDS = (
    "pe_ratio",
    "pb_ratio",
    "roe",
    "roce",
    "debt_to_equity",
    "revenue_growth",
    "eps_growth",
    "profit_growth",
    "operating_margin",
)


class SymbolNotFoundError(Exception):
    def __init__(self, symbol: str):
        self.symbol = symbol
        super().__init__(f"No data found for symbol '{symbol}'")


def _to_nse_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if symbol.startswith("^") or symbol.endswith(NSE_SUFFIX):
        return symbol
    return f"{symbol}{NSE_SUFFIX}"


class MarketDataError(Exception):
    def __init__(self, message: str, status: IngestionStatus):
        super().__init__(message)
        self.status = status

def _with_retry(fn, *args, **kwargs):
    import requests
    from yfinance.exceptions import YFRateLimitError
    
    last_exc: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # yfinance raises a mix of requests/JSON errors
            last_exc = exc
            if attempt < RETRY_ATTEMPTS - 1:
                # Add extra delay for rate limits
                is_rate_limit = isinstance(exc, YFRateLimitError) or "429" in str(exc)
                multiplier = 4 if is_rate_limit else 1
                delay = RETRY_BASE_DELAY_SECONDS * (2**attempt) * multiplier
                logger.warning("yfinance call failed (attempt %d/%d): %s. Retrying in %.1fs", attempt + 1, RETRY_ATTEMPTS, exc, delay)
                time.sleep(delay)
    
    # Classify the final failure
    from app.core.ingestion_status import IngestionStatus
    status = IngestionStatus.PROVIDER_ERROR
    
    if isinstance(last_exc, YFRateLimitError) or "429" in str(last_exc):
        status = IngestionStatus.RATE_LIMITED
    elif isinstance(last_exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        status = IngestionStatus.NETWORK_ERROR
        
    raise MarketDataError(str(last_exc), status) from last_exc


def download_batch_with_retry(tickers: list[str], **kwargs) -> pd.DataFrame | dict:
    """Wrapper around yf.download with the same robust backoff policy."""
    import requests
    from yfinance.exceptions import YFRateLimitError
    
    last_exc: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            # yf.download intercepts Exceptions inside and returns empty DFs sometimes, 
            # but for 429s or ConnectionErrors it often raises.
            # We explicitly set progress=False, threads=True by default.
            kwargs.setdefault("progress", False)
            kwargs.setdefault("threads", True)
            
            result = yf.download(tickers=tickers, **kwargs)
            return result
        except Exception as exc:
            last_exc = exc
            if attempt < RETRY_ATTEMPTS - 1:
                is_rate_limit = isinstance(exc, YFRateLimitError) or "429" in str(exc)
                multiplier = 4 if is_rate_limit else 1
                delay = RETRY_BASE_DELAY_SECONDS * (2**attempt) * multiplier
                logger.warning("yfinance batch download failed (attempt %d/%d): %s. Retrying in %.1fs", attempt + 1, RETRY_ATTEMPTS, exc, delay)
                time.sleep(delay)
    
    # Let the caller handle the final exception, or just raise it.
    raise last_exc



def fetch_price_history(symbol: str, period: str = "2y") -> pd.DataFrame:
    nse_symbol = _to_nse_symbol(symbol)
    ticker = yf.Ticker(nse_symbol)
    history = _with_retry(ticker.history, period=period, auto_adjust=False)

    if history is None or history.empty:
        raise SymbolNotFoundError(symbol)

    history = history.reset_index()
    history["Date"] = pd.to_datetime(history["Date"]).dt.date
    return history[["Date", "Open", "High", "Low", "Close", "Volume"]].rename(
        columns={"Date": "date", "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
    )


def fetch_stock_meta(symbol: str) -> dict:
    nse_symbol = _to_nse_symbol(symbol)
    ticker = yf.Ticker(nse_symbol)
    info = _with_retry(lambda: ticker.info)

    if not info or (info.get("regularMarketPrice") is None and info.get("longName") is None):
        raise SymbolNotFoundError(symbol)

    return {
        "company_name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": info.get("marketCap"),
    }


def fetch_fundamentals(symbol: str) -> dict:
    """Pull fundamentals from yfinance .info.

    ROCE has no equivalent field in yfinance and is always None — there is no
    reliable way to derive it without balance-sheet data yfinance doesn't
    expose consistently for NSE tickers, so it is left out rather than
    approximated.

    eps_growth is derived from forwardEps vs trailingEps (a real yfinance
    figure, not synthetic) since yfinance has no direct "EPS growth" field.
    profit_growth uses yfinance's own trailing earningsGrowth. debt_to_equity
    is divided by 100 since yfinance reports it as a percentage.
    """
    nse_symbol = _to_nse_symbol(symbol)
    ticker = yf.Ticker(nse_symbol)
    info = _with_retry(lambda: ticker.info)

    if not info:
        raise SymbolNotFoundError(symbol)

    trailing_eps = info.get("trailingEps")
    forward_eps = info.get("forwardEps")
    eps_growth = None
    if trailing_eps not in (None, 0) and forward_eps is not None:
        eps_growth = (forward_eps - trailing_eps) / abs(trailing_eps)

    debt_to_equity = info.get("debtToEquity")
    if debt_to_equity is not None:
        debt_to_equity = debt_to_equity / 100

    data = {
        "pe_ratio": info.get("trailingPE"),
        "pb_ratio": info.get("priceToBook"),
        "roe": info.get("returnOnEquity"),
        "roce": None,
        "debt_to_equity": debt_to_equity,
        "revenue_growth": info.get("revenueGrowth"),
        "eps_growth": eps_growth,
        "profit_growth": info.get("earningsGrowth"),
        "operating_margin": info.get("operatingMargins"),
    }

    missing_fields = [field for field in FUNDAMENTAL_FIELDS if data.get(field) is None]
    return {"data": data, "missing_fields": missing_fields}
