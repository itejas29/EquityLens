"""Tunable constants for the risk/position-sizing/portfolio engines
(app/services/levels.py, position_sizing.py, portfolio.py). Kept out of the
functions themselves so they can be audited/tuned without touching logic.
"""

# --- Levels (app/services/levels.py) ----------------------------------------

ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 2.0
ENTRY_ZONE_ATR_MULTIPLIER = 0.5
RISK_REWARD_RATIO = 2.0  # target is set to hit exactly this multiple of risk-per-share
SUPPORT_LOOKBACK_DAYS = 20

# --- Position sizing (app/services/position_sizing.py) ----------------------

RISK_PCT_PER_TRADE = {
    "low": 0.005,
    "moderate": 0.01,
    "high": 0.02,
}

# --- Portfolio construction (app/services/portfolio.py) ---------------------

MIN_SCORE_THRESHOLD = {
    "low": 70,
    "moderate": 60,
    "high": 50,
}

MAX_ALLOCATION_PCT = {
    "low": 0.15,
    "moderate": 0.20,
    "high": 0.25,
}

CASH_BUFFER_PCT = {
    "low": 0.20,
    "moderate": 0.10,
    "high": 0.05,
}

MAX_STOCKS_PER_SECTOR = 2
MIN_STOCKS_PREFERENCE = 5
MAX_STOCKS_PREFERENCE = 10
DEFAULT_MAX_STOCKS = 10

RISK_APPETITES = ("low", "moderate", "high")
