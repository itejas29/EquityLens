"""Backtest defaults and backtest-specific scoring weights.

DATA LIMITATION — why fundamentals are excluded from backtest scoring:
`fundamentals` holds exactly one snapshot per stock (as of whenever it was
last refreshed), not a historical time series. Using that single snapshot
to score a rebalance date a year in the past would mean the strategy "knew"
today's ROE/revenue growth back then — textbook look-ahead bias. Rather than
fake a historical fundamentals series, the backtest composite is built from
only the technical and risk sub-scores, both of which are fully derivable
from price_history bounded to each rebalance date. This means backtest
results measure a price-action/technical+risk strategy, not the full
technical+fundamental+valuation+risk strategy used for live recommendations
— documented here and repeated in the results payload.
"""

DEFAULT_TRANSACTION_COST_PCT = 0.0012  # round-trip; half applied per fill (buy leg + sell leg)
DEFAULT_SLIPPAGE_PCT = 0.0005  # applied per fill, adverse direction
DEFAULT_RISK_FREE_RATE = 0.065  # annualized, for Sharpe/Sortino
DEFAULT_HORIZON_DAYS = 90
DEFAULT_REBALANCE_FREQUENCY = "monthly"

TRADING_DAYS_PER_YEAR = 252

# Renormalized from the live COMPOSITE_WEIGHTS (technical=0.30, risk=0.20)
# with fundamental/valuation dropped: 0.30/0.50=0.6, 0.20/0.50=0.4.
BACKTEST_COMPOSITE_WEIGHTS = {
    "technical": 0.6,
    "risk": 0.4,
}

MIN_PRICE_ROWS_FOR_SCORING = 60  # need at least this many point-in-time rows before scoring a stock
