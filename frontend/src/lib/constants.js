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

// Public repository. The audit reports live in docs/, which is not in the backend
// image, so the verdict panel links to them here by their repository path.
export const REPO_URL = "https://github.com/itejas29/EquityLens";
