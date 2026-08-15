"""Universe construction: which of NSE's ~2,100 tradeable listings get scored.

WHY NOT ALL OF THEM. Two independent reasons, both measured rather than assumed:

1. Cost. Price history batches cheaply (~2.6 min for all 2,126 symbols), but
   yfinance exposes no batch endpoint for fundamentals — `Ticker.info` is one
   HTTP round trip per symbol at ~0.83s, so the full exchange is ~29 minutes of
   serial fetching every run. Screening to the most liquid names first cuts that
   to a few minutes without losing anything that could realistically be traded.

2. Comparability. The fundamental and valuation sub-scores are percentile ranks
   against the scored universe. Ranking a ₹50cr microcap's P/E against Reliance's
   produces a confident number that means nothing — they are not peers, and the
   ATR entry/stop levels the app publishes would not be executable on the
   microcap at any size. A liquidity floor makes the peer group real.

So the universe is built in two stages: a cheap price-only screen over the whole
exchange, then a full ingest of whatever survives it.
"""

# --- Stage 1: liquidity screen (whole exchange, prices only, nothing stored) --

# Short window is enough to rank by liquidity and keeps the stage-1 pull small;
# the full history is only fetched for names that survive.
SCREEN_PERIOD = "3mo"
LIQUIDITY_WINDOW_DAYS = 20

# Minimum 20-day average daily traded value (price x volume), in rupees.
# ₹5 crore/day is the rough level below which a retail-sized position starts
# moving the price and the published entry zone stops being realistic.
MIN_AVG_TRADED_VALUE = 50_000_000.0

# Hard cap on the scored universe. Bounded so one run's cost is predictable and
# the percentile population stays a coherent peer group. The floor above usually
# binds first; this is the backstop.
MAX_UNIVERSE_SIZE = 500

# Minimum bars in the screen window before a symbol is judged — a stock listed
# last week has no meaningful 20-day average and is not rejected on liquidity,
# it is rejected for having no history.
MIN_SCREEN_BARS = 30

# --- Stage 2: full ingest of survivors ---------------------------------------

# yfinance batches price history by ticker list. 60 keeps each response
# parseable and each failure small; larger batches fail as a whole more often.
DOWNLOAD_BATCH_SIZE = 60

# Full history period for scored stocks. Needs to cover the 200-day moving
# average and the 1-year beta/drawdown windows with room to spare.
#
# Raised 2y -> 5y for the ML layer, then 5y -> 10y for Phase 12 regime
# validation: 5y contained only two correction periods, too few to test a
# downside-protection filter. 10y adds the 2018 correction, the 2020 COVID
# crash and the 2022 drawdown. At 2y (~501 bars) the 252-day beta
# warm-up left only 243 usable dates — under one year of distinct observations,
# all from a single market regime, which is not enough for a model to learn
# anything that generalises. 5y leaves roughly 1,000 usable dates spanning
# several regimes. Costs nothing extra in requests: yfinance returns the whole
# range in the same batched call, only the payload is larger.
HISTORY_PERIOD = "10y"

# Pause between individual fundamentals calls. Measured the hard way: 0.4s ran
# straight into YFRateLimitError at the start of stage 2, and because
# fetch_stock_meta turns an exhausted retry into SymbolNotFoundError, ~60
# symbols were wrongly recorded as "no data" rather than "throttled". 0.9s
# clears the throttle at a cost of ~4 extra minutes on a 500-symbol run.
FUNDAMENTALS_DELAY_SECONDS = 0.9

# Extra pause before stage 2 begins. Stage 1 hammers the same host for ~14
# minutes; going straight into per-symbol calls is what triggered the throttle.
STAGE_GAP_SECONDS = 30

# --- Scheduling --------------------------------------------------------------

# IST hour for the daily incremental price update and the weekly universe rebuild.
# Runs well after the 15:40 close so the day's bars are final, and well before
# the 09:15 shortlist so it always has a freshly-built universe to pick from.
REBUILD_HOUR = 20
REBUILD_MINUTE = 0

# Day of the week for the full universe membership rebuild (0 = Monday).
# Saturday avoids interfering with any market-hours operations.
FULL_REBUILD_DAY = 5
