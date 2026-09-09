"""Detecting corporate-action discontinuities in a stored close series.

WHY THIS EXISTS. A split, bonus or consolidation changes the price level
without changing anything about the company. A price series that contains an
unadjusted one is not a return series any more — it has a step in it, and
every statistic computed across that step is wrong. For V1 specifically:

  * 12-month momentum reads the step as a return. A downward step ranks the
    stock last for a year (never bought — the harmless direction); an upward
    step, which is what a consolidation produces, ranks it FIRST and buys it
    on a move that never happened.
  * ATR, volatility, beta and max drawdown are all computed across the step. A
    4-ATR stop derived from a series containing a 50% single-day move is not a
    stop.

Measured in production on 2026-09-10: nine such steps in the stored universe,
two of them inside the current 12-month window. Details, including which are
ours and which are the provider's, in docs/audit/price-series-discontinuities.md.

WHAT IS AND IS NOT DETECTED. Real stocks fall 30% in a day. The discriminator
is not the SIZE of the move but its RATIO: a corporate action moves the price
by a small rational factor — 2:1, 3:1, 3:2 — because that is what the action
does. All nine production cases land within 2% of such a ratio. A genuine
crash landing that precisely on 2.0000 is possible, and would cost this stock
its momentum score for a year; that is the conservative direction (a missed
entry, never a bad one) and the reason the check errs this way rather than the
other.

No network calls. This runs inside _momentum_scores on a list of closes that
is already in memory, so it costs one pass over data already loaded.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Ratios a corporate action produces. Stored as the factor by which the price
# DIVIDES (a 2:1 split halves the price, so prev/curr == 2). Consolidations are
# the same ratios inverted and are checked in both directions.
CORPORATE_ACTION_RATIOS = (2.0, 3.0, 4.0, 5.0, 10.0, 1.5, 2.5, 20.0)

# How close an observed ratio must sit to one of the above. 2% is wide enough
# to absorb the intraday drift that separates two consecutive closes around the
# ex-date (the nine production cases range from x1.4936 to x3.9391 against
# nominal 1.5 and 4.0) and narrow enough that ordinary volatility does not
# reach it.
RATIO_TOLERANCE = 0.02

# A floor, so the tolerance band around 1.5 cannot be reached by a normal day.
# The smallest ratio checked is 1.5, i.e. a 33% move; nothing below 25% is
# considered regardless.
MIN_MOVE_PCT = 25.0


@dataclass(frozen=True)
class Discontinuity:
    index: int          # position in the closes list of the SECOND bar
    previous: float
    current: float
    ratio: float        # >1 means the price fell by that factor
    matched: float      # the nominal corporate-action ratio it matched


def _match_ratio(previous: float, current: float) -> Discontinuity | None:
    if previous <= 0 or current <= 0:
        return None
    move_pct = abs(current - previous) / previous * 100
    if move_pct < MIN_MOVE_PCT:
        return None

    # Checked in both directions: a split divides the price, a consolidation
    # multiplies it, and only the second one is dangerous for a momentum rank.
    for ratio in (previous / current, current / previous):
        for nominal in CORPORATE_ACTION_RATIOS:
            if abs(ratio - nominal) / nominal <= RATIO_TOLERANCE:
                return Discontinuity(0, previous, current, previous / current, nominal)
    return None


def find_discontinuity(closes: list[float]) -> Discontinuity | None:
    """First corporate-action-shaped step in a close series, or None.

    Returns on the first hit rather than collecting all of them: the caller's
    only decision is whether this series is usable, and one step is enough to
    answer that.
    """
    for i in range(1, len(closes)):
        found = _match_ratio(closes[i - 1], closes[i])
        if found is not None:
            return Discontinuity(i, found.previous, found.current, found.ratio, found.matched)
    return None
