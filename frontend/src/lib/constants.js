// yfinance reports NSE sectors using GICS-style names; this is the set the
// ingestion pipeline actually stores, so the same list drives every sector filter.
export const SECTORS = [
  "Financial Services",
  "Technology",
  "Healthcare",
  "Consumer Cyclical",
  "Consumer Defensive",
  "Energy",
  "Basic Materials",
  "Utilities",
  "Industrials",
  "Communication Services",
  "Real Estate",
];
