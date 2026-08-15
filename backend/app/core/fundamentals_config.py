"""Fundamentals refresh schedule.

Fundamentals data (P/E, P/B, ROE, etc.) changes at most quarterly when
companies file results. Fetching it nightly was 500 × 0.9s = 7.5 min of
serial HTTP calls for unchanged data.

This config governs a separate monthly refresh, decoupled from the daily
price pipeline.
"""

# Day of month to run the fundamentals refresh (1-28 to avoid month-length issues).
FUNDAMENTALS_REFRESH_DAY = 1

# IST hour/minute for the refresh. After market close, before next-day signals.
FUNDAMENTALS_REFRESH_HOUR = 21
FUNDAMENTALS_REFRESH_MINUTE = 0
