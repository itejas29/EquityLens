"""NSE trading-hours helper, shared by the schedulers and the API layer.

Extracted from `scheduler.py` so the WebSocket route can label a payload
live-or-closed without importing a module full of background loops. The window
and timezone are unchanged from the original definition.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Deliberately wider than NSE's 09:15-15:30 continuous session: opening a few
# minutes early catches the pre-open call auction settling, and closing late
# lets the last regular-session bar land before polling stops.
MARKET_OPEN = (9, 10)   # HH, MM
MARKET_CLOSE = (15, 40)


def is_market_hours() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    curr = now.hour * 60 + now.minute
    return (MARKET_OPEN[0] * 60 + MARKET_OPEN[1]) <= curr <= (MARKET_CLOSE[0] * 60 + MARKET_CLOSE[1])
