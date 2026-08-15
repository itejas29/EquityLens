"""Forward-looking analysis for a single stock, built only from its own history.

WHAT THIS IS NOT: a price prediction. Nothing here forecasts where a stock will
go. Every number below is one of two things —

  1. A DISPERSION estimate. Realized volatility says how far this stock has
     typically travelled over a given number of days. Projecting that forward
     gives a range, not a direction: "a move of this size would be ordinary for
     this stock" is a statement about its past behaviour, not about its future.

  2. A HISTORICAL FREQUENCY. Given the actual entry/target/stop levels, how
     often did this stock, from every comparable point in its own past, reach
     the target before the stop inside the horizon? That is a backtest of one
     setup shape on one stock, reported as a sample count and a rate.

Both are descriptive. Neither implies the next trade resolves the same way, and
the sample sizes are always returned so a 3-sample "80% hit rate" cannot be
mistaken for a reliable one.
"""

import logging
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.models.indicator import Indicator
from app.models.stock import Stock
from app.services.indicators import load_price_history_df
from app.services.levels import compute_levels_for_stock

logger = logging.getLogger(__name__)

TRADING_DAYS_PER_YEAR = 252

# Below this many historical entry points the hit rate is reported but flagged
# as unreliable — with a 90-day horizon on 2y of data the windows overlap
# heavily, so the effective independent sample count is far lower than the raw
# count suggests.
MIN_RELIABLE_SAMPLES = 30


@dataclass
class ExpectedMove:
    horizon_days: int
    annualised_vol_pct: float
    # One standard deviation of the horizon return. Historically ~68% of moves
    # land inside this band, ~95% inside two.
    sigma_1_pct: float
    sigma_1_low: float
    sigma_1_high: float
    sigma_2_low: float
    sigma_2_high: float
    # Typical single-day range, in rupees and as a share of price.
    atr: float | None
    atr_pct: float | None
    # How many ordinary days of movement the target and stop sit away.
    atr_days_to_target: float | None
    atr_days_to_stop: float | None


@dataclass
class HistoricalOutcome:
    samples: int
    hit_target_first: int
    hit_stop_first: int
    resolved_neither: int
    hit_rate_pct: float | None
    avg_days_to_target: float | None
    reliable: bool
    horizon_days: int
    # Average return per resolved trade if this stock's own history repeated:
    #   hit_rate x target_return + (1 - hit_rate) x stop_return
    # This is the number that decides whether a setup is worth taking. A 1:2
    # reward:risk needs better than a 33.3% hit rate to break even, so a
    # win rate that sounds poor can still be positive — and one that sounds
    # decent can still be negative.
    expectancy_pct: float | None
    breakeven_hit_rate_pct: float | None


@dataclass
class Outlook:
    symbol: str
    reference_close: float
    expected_move: ExpectedMove | None
    historical: HistoricalOutcome | None
    note: str


def _latest_indicator(db: Session, stock_id: int) -> Indicator | None:
    return db.query(Indicator).filter(Indicator.stock_id == stock_id).order_by(Indicator.date.desc()).first()


def _expected_move(
    close: float,
    annual_vol: float,
    horizon_days: int,
    atr: float | None,
    target: float | None,
    stop: float | None,
) -> ExpectedMove:
    """Scale annualised volatility to the horizon by the square root of time."""
    sigma = annual_vol * math.sqrt(horizon_days / TRADING_DAYS_PER_YEAR)
    move = close * sigma

    atr_pct = (atr / close * 100) if atr else None
    days_target = abs(target - close) / atr if (atr and target) else None
    days_stop = abs(close - stop) / atr if (atr and stop) else None

    return ExpectedMove(
        horizon_days=horizon_days,
        annualised_vol_pct=round(annual_vol * 100, 2),
        sigma_1_pct=round(sigma * 100, 2),
        sigma_1_low=round(close - move, 2),
        sigma_1_high=round(close + move, 2),
        sigma_2_low=round(close - 2 * move, 2),
        sigma_2_high=round(close + 2 * move, 2),
        atr=round(atr, 2) if atr else None,
        atr_pct=round(atr_pct, 2) if atr_pct else None,
        atr_days_to_target=round(days_target, 1) if days_target else None,
        atr_days_to_stop=round(days_stop, 1) if days_stop else None,
    )


def _historical_outcome(
    price_df: pd.DataFrame, target_pct: float, stop_pct: float, horizon_days: int
) -> HistoricalOutcome | None:
    """Walk every historical entry point and see which level came first.

    For each day, the trade is "entered" at that close and the following
    `horizon_days` of intraday highs and lows are scanned in order. Whichever of
    the target or stop is touched first decides the outcome; if neither is
    touched the window resolves as "neither". Using highs and lows rather than
    closes matters — a stop is hit when price trades through it, not only when
    it settles below it.

    Windows overlap, so consecutive samples are not independent. That is why
    `reliable` exists and why the raw sample count is always returned.
    """
    if price_df.empty or len(price_df) < horizon_days + 20:
        return None

    df = price_df.dropna(subset=["close"]).reset_index(drop=True)
    if len(df) < horizon_days + 20:
        return None

    closes = df["close"].astype(float).to_numpy()
    highs = df["high"].astype(float).to_numpy() if "high" in df else closes
    lows = df["low"].astype(float).to_numpy() if "low" in df else closes

    n = len(closes)
    last_entry = n - horizon_days - 1
    if last_entry <= 0:
        return None

    hit_target = 0
    hit_stop = 0
    neither = 0
    days_to_target: list[int] = []

    for i in range(last_entry):
        entry = closes[i]
        if not np.isfinite(entry) or entry <= 0:
            continue
        target_price = entry * (1 + target_pct)
        stop_price = entry * (1 + stop_pct)  # stop_pct is negative

        outcome = None
        for j in range(i + 1, min(i + 1 + horizon_days, n)):
            hi, lo = highs[j], lows[j]
            # Stop checked first: on a day whose range spans both levels there
            # is no way to know which printed first, and assuming the target
            # would flatter every result.
            if np.isfinite(lo) and lo <= stop_price:
                outcome = "stop"
                break
            if np.isfinite(hi) and hi >= target_price:
                outcome = "target"
                days_to_target.append(j - i)
                break

        if outcome == "target":
            hit_target += 1
        elif outcome == "stop":
            hit_stop += 1
        else:
            neither += 1

    samples = hit_target + hit_stop + neither
    if samples == 0:
        return None

    resolved = hit_target + hit_stop
    # Rate is over RESOLVED trades — a setup that expired flat is neither a win
    # nor a loss, and counting it as a loss would understate the edge.
    hit_rate = (hit_target / resolved) if resolved else None

    expectancy = None
    if hit_rate is not None:
        expectancy = (hit_rate * target_pct + (1 - hit_rate) * stop_pct) * 100

    # Hit rate at which the setup breaks even, from its reward:risk alone.
    risk = abs(stop_pct)
    breakeven = (risk / (target_pct + risk) * 100) if (target_pct + risk) > 0 else None

    return HistoricalOutcome(
        samples=samples,
        hit_target_first=hit_target,
        hit_stop_first=hit_stop,
        resolved_neither=neither,
        hit_rate_pct=round(hit_rate * 100, 1) if hit_rate is not None else None,
        avg_days_to_target=round(float(np.mean(days_to_target)), 1) if days_to_target else None,
        reliable=samples >= MIN_RELIABLE_SAMPLES,
        horizon_days=horizon_days,
        expectancy_pct=round(expectancy, 2) if expectancy is not None else None,
        breakeven_hit_rate_pct=round(breakeven, 1) if breakeven is not None else None,
    )


def get_outlook(
    db: Session,
    symbol: str,
    horizon_days: int = 30,
    target: float | None = None,
    stop: float | None = None,
) -> Outlook | None:
    stock = db.query(Stock).filter(Stock.symbol == symbol.upper()).first()
    if stock is None:
        return None

    price_df = load_price_history_df(db, stock.id)
    if price_df.empty:
        return None

    closes = price_df["close"].dropna()
    if closes.empty:
        return None
    close = float(closes.iloc[-1])

    levels = compute_levels_for_stock(db, stock.id)
    if target is None and levels is not None:
        target = levels.target_price
    if stop is None and levels is not None:
        stop = levels.stop_loss

    indicator = _latest_indicator(db, stock.id)
    annual_vol = float(indicator.volatility) if indicator and indicator.volatility is not None else None

    # Read straight off Levels. This previously back-computed the ATR as
    # `entry_high - entry_low`, but the zone is [close - 0.5*ATR, close], so
    # that width is half an ATR — every ATR-relative distance came out 2x too
    # large, and the "typical daily range" shown to users was half the real one.
    atr = levels.atr if levels is not None else None

    expected = (
        _expected_move(close, annual_vol, horizon_days, atr, target, stop) if annual_vol is not None else None
    )

    historical = None
    if target and stop and close > 0:
        historical = _historical_outcome(
            price_df, (target - close) / close, (stop - close) / close, horizon_days
        )

    return Outlook(
        symbol=stock.symbol,
        reference_close=round(close, 2),
        expected_move=expected,
        historical=historical,
        note=(
            "Ranges are realized-volatility dispersion, not forecasts. Hit rates are how "
            "this stock resolved the same setup shape in its own past, on overlapping windows."
        ),
    )
