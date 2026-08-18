"""Tier-2 fast quote loop configuration.

Tier 1 (`price_refresh_loop`) keeps all 500 universe stocks on a ~60s cadence
and remains the source for Discover, the screener, Sectors and the market
overview. Tier 2 re-quotes only the handful of symbols a user is actually
looking at, far more often, so those read as genuinely live.

The cadence below is affordable because the fast tier reuses Tier 1's *batched*
`_download_prices` call: one HTTP request per tick regardless of how many
symbols are in the set. Going from 60s to 10s therefore costs 5 extra requests
per minute in total — not 5 per symbol.
"""

# Seconds between fast-tier fetches. The batch call measured 1.0-2.5s for ~15
# symbols, so 10s leaves several seconds of headroom even on a slow round trip
# while still giving ~6 updates a minute against Tier 1's ~1.
FAST_REFRESH_SECONDS = 10

# Hard cap on the fast set. Without it, a large watchlist would quietly turn
# this into a second full-universe poll at 6x the frequency.
MAX_FAST_SYMBOLS = 20

# The watched set is rebuilt from Postgres on this cadence rather than every
# tick. Holdings and watchlists change on human timescales; re-querying them
# every 10s is pure overhead.
SYMBOL_SET_REFRESH_SECONDS = 60

# Per-request timeout for fast-tier fetches, in seconds. Deliberately shorter
# than the cadence: when the network degrades, yfinance stops batching and
# retries tickers one at a time, so at the default 10s a set of 8 symbols can
# block this loop for minutes while the UI shows nothing new. Failing fast
# hands control to the backoff path, which is the behaviour we actually want.
FETCH_TIMEOUT_SECONDS = 6

# Hard ceiling on how long the loop will WAIT for a fetch before calling the
# tick failed. Needed because FETCH_TIMEOUT_SECONDS above is advisory: yfinance
# ignores it on the per-ticker path it falls back to when a batch fails, and
# has been observed spending 30s per symbol there. Two cadences' worth of grace.
FETCH_DEADLINE_SECONDS = FAST_REFRESH_SECONDS * 2

# Consecutive failed fetches before the loop pauses. Yahoo rate-limits in
# bursts, and backing off is what keeps the IP usable for Tier 1 as well —
# the two tiers share it.
BACKOFF_AFTER_FAILURES = 3
BACKOFF_SECONDS = 120

# A quote older than this is no longer presented as live. Three ticks of grace,
# so one slow round trip doesn't flip the whole UI to "stale".
QUOTE_STALE_AFTER_SECONDS = FAST_REFRESH_SECONDS * 3


# Index symbols always carried on the fast tier. These are full Yahoo tickers,
# not NSE equity symbols, so they bypass the .NS suffix (see _yahoo_ticker).
# NIFTY 50 is market context for every page, it rides along in the same batched
# request at no extra round trip, and without it the index level on screen could
# only ever be the frozen regime close from signal-generation time.
INDEX_SYMBOLS = ("^NSEI",)
