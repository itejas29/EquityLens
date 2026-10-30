"""Integrity flags for published shortlists.

A dated shortlist is a pre-market call: built only from sessions that closed
before its date, published while its own session can still be traded, on a
day the exchange is open. Production broke each of those at least once
before the guards in daily_signals / scheduler existed, and those rows are
part of the record forever — published calls are never edited or deleted.

So they are FLAGGED here instead. Flags are computed from what is stored
(signal rows, their generated_at, and which dates have any price bars), never
written back, and only used to separate the clean record from the rest when
it is scored. A flagged day still appears in every count, by name.
"""

from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime

from app.core.daily_signals_config import PUBLISH_DEADLINE_HOUR, PUBLISH_DEADLINE_MINUTE
from app.core.market_hours import IST

# The list was built from the close of the very session it claims to precede.
SAME_DAY_DATA = "same_day_data"
# Published at or after the 15:30 close: nobody could act on it that session.
PUBLISHED_AFTER_CLOSE = "published_after_close"
# Dated on a day the exchange did not trade (no stock has a bar for it, while
# later sessions do have bars — so it is a closure, not a missing ingest).
CLOSED_DAY = "closed_day"
# Built from a session older than the one before its date. Not look-ahead —
# the call only knew less than it could have (e.g. after an outage) — so it
# stays in the clean record; reported for completeness.
STALE_REFERENCE = "stale_reference"

# The flags that take a day out of the clean record. Each one means the call
# could not have been followed as published.
EXCLUDING_FLAGS = frozenset({SAME_DAY_DATA, PUBLISHED_AFTER_CLOSE, CLOSED_DAY})


@dataclass(frozen=True)
class PublishedDay:
    date: date_type
    generated_at: datetime
    reference_dates: tuple[date_type, ...]


def flag_day(day: PublishedDay, sessions: list[date_type]) -> frozenset[str]:
    """Flags for one published date.

    `sessions` is the trading calendar as observed in stored prices: every
    date on which at least one stock has a bar, ascending. A date after the
    last observed session cannot be judged open or closed yet, so it is
    never flagged CLOSED_DAY — the call for today is not "closed" merely
    because tonight's ingest has not run.
    """
    flags: set[str] = set()

    if any(ref >= day.date for ref in day.reference_dates):
        flags.add(SAME_DAY_DATA)

    generated = day.generated_at.astimezone(IST)
    deadline = (PUBLISH_DEADLINE_HOUR, PUBLISH_DEADLINE_MINUTE)
    if generated.date() > day.date or (
        generated.date() == day.date and (generated.hour, generated.minute) >= deadline
    ):
        flags.add(PUBLISHED_AFTER_CLOSE)

    session_set = set(sessions)
    if sessions and day.date <= sessions[-1] and day.date not in session_set:
        flags.add(CLOSED_DAY)

    earlier = [s for s in sessions if s < day.date]
    if earlier and day.reference_dates and max(day.reference_dates) < earlier[-1]:
        flags.add(STALE_REFERENCE)

    return frozenset(flags)
