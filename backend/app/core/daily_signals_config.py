"""Thresholds for the daily pre-market shortlist (app/services/daily_signals.py).

COMPLETENESS GATE — why a stock can be scored yet still excluded here:
the scoring engine deliberately renormalizes around missing inputs, so a
stock with no usable price data can still produce an `overall_score` from
fundamentals and valuation alone. That is the right behaviour for a research
table, but not for a dated entry call: without technical and risk sub-scores
there is no momentum evidence and no volatility measure, and without a close
there are no ATR levels to enter or exit against. Those stocks are dropped
from the shortlist entirely rather than shown with a caveat.
"""

# Both price-derived sub-scores must be present. Fundamental/valuation may be
# partially renormalized (yfinance gaps on Indian tickers are routine), but a
# missing technical or risk sub-score means the price series itself failed.
REQUIRED_SUBSCORES = ("technical", "risk")

# Floor for inclusion — the ACCUMULATE band. Below this the composite is not
# expressing a positive view, so it does not belong on a shortlist.
MIN_OVERALL_SCORE = 60.0

# Shortlist length. Kept deliberately short: the page is meant to be read in
# full in under a minute, and a 40-row table is the thing being replaced.
MAX_SIGNALS = 8

# At most this many names from any one sector, so a single sector rallying
# cannot fill the entire shortlist.
MAX_PER_SECTOR = 2

# Reference bar must be no older than this many calendar days, otherwise the
# levels describe a stale price and the stock is dropped.
MAX_REFERENCE_AGE_DAYS = 7

# IST wall-clock time the scheduled generation runs. 09:15 is the NSE open —
# the snapshot is built from the previous session's completed bar, which is
# the last fully-formed candle available at that moment.
GENERATION_HOUR = 9
GENERATION_MINUTE = 15

# --- Rationale thresholds ---------------------------------------------------
# Bands used to turn a computed value into a plain-English clause. Chosen to
# match the scoring engine's own bands so the wording never contradicts the
# number it is explaining.
RSI_OVERSOLD = 30.0
RSI_OVERBOUGHT = 70.0
RSI_STRONG_LOW = 50.0  # above this, momentum reads positive
VOLUME_SURGE_RATIO = 1.2  # 20-day average volume multiple worth calling out
LOW_BETA = 0.9
HIGH_BETA = 1.1
