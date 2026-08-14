from datetime import date as date_type

from pydantic import BaseModel, ConfigDict


class StockListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    company_name: str | None
    sector: str | None
    industry: str | None
    market_cap: float | None
    is_active: bool


class StockListResponse(BaseModel):
    items: list[StockListItem]
    total: int
    page: int
    page_size: int


class LatestPrice(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date_type
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None


class LatestFundamentals(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    as_of_date: date_type
    pe_ratio: float | None
    pb_ratio: float | None
    roe: float | None
    roce: float | None
    debt_to_equity: float | None
    revenue_growth: float | None
    eps_growth: float | None
    profit_growth: float | None
    operating_margin: float | None


class PricePoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date_type
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None


class StockDetail(StockListItem):
    latest_price: LatestPrice | None
    latest_fundamentals: LatestFundamentals | None


class RefreshResponse(BaseModel):
    symbol: str
    price_rows_upserted: int
    fundamentals_missing_fields: list[str]
