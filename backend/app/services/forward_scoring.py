"""The forward record, scored the way someone following it would have lived it.

compute_track_record (forward_testing.py) answers "what did prices do after
each signal". This module answers "what would following the calls have
returned", which needs five things that one does not do:

1. A FILL THAT COULD HAVE HAPPENED. The call is "buy up to entry_high" on its
   date. A limit order at entry_high, placed when the list is published
   (09:15 IST), fills at roughly the opening price if the stock opens at or
   below entry_high, at entry_high if it opens above but trades down to it,
   and not at all if it never comes back — a missed trade, which is counted
   as missed rather than scored as if it had been bought. A list published
   later than OPEN_FILL_DEADLINE gets no opening price: entry_high or nothing.

2. COSTS. ROUND_TRIP_COST is deducted from every trade.

3. ONE POSITION PER STOCK. The same stock is published day after day — 296
   signals on 2026-10-30 were 59 distinct stocks. Scoring each as a separate
   trade counts one position many times over and makes the sample look far
   larger and more independent than it is. A stock's later calls are absorbed
   into the position opened by its first filled call until that position
   exits; only then can a new call open a new one.

4. FIRST TOUCH, ONE OUTCOME. A position exits at whichever of stop or target
   it reaches first (a gap through either fills at the open), or at the
   close of its MAX_HOLD_SESSIONS-th session. When one bar touches both, the
   order inside the bar is unknowable from daily data and it is scored as
   the stop — the pessimistic reading — and counted, so the reader can see
   how often that assumption was needed. (forward_testing.py records "ever
   touched" flags with no order and no time limit, so one signal can count as
   both a stop and a target.)

5. THE SAME WINDOW FOR THE BENCHMARK. Benchmarks are measured from the open
   of the fill session to the close of the exit session — the window the
   position was actually held. (forward_testing.py measures the stock from
   the previous close but NIFTY from the call date's close, so the stock's
   window is one session longer than the index's.) Two benchmarks: NIFTY 50,
   and NIFTY200 Momentum 30 — the index a momentum investor could simply
   buy. Yahoo has no history for that index itself (NIFTY200MOMENTM30.NS
   returns one row), so MOM30IETF — ICICI Prudential's ETF on it, the most
   traded of the five that track it — stands in. An ETF is the thing an
   investor would actually have bought instead, fees and all.

Calls flagged by record_integrity (built from their own session's close,
published after the close, or dated on a closed day) are scored separately:
the headline is the clean record, and the full record is reported beside it.

Nothing here selects or changes a call. It only measures them.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.market_hours import IST
from app.models.daily_signal import DailySignal
from app.models.pipeline_run import PipelineRun
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.services.ingestion import is_no_trade_placeholder
from app.services.record_integrity import EXCLUDING_FLAGS, STALE_REFERENCE, PublishedDay, flag_day

logger = logging.getLogger(__name__)

# Statutory floor for an NSE delivery round trip, as a fraction of the trade:
# STT 0.1% on each leg (0.2%), stamp duty 0.015% on the buy, exchange
# transaction charges ~0.003% per leg, SEBI fee and GST on top — about 0.22%
# before any brokerage or slippage. 0.24% leaves a sliver for those and is
# still a FLOOR: DP charges are a fixed rupee amount per sale, so on a small
# position they alone can add another 0.05-0.1%.
ROUND_TRIP_COST = Decimal("0.0024")

# The strategy re-ranks monthly; a position is judged over about a month of
# sessions, the same 20 the forward record already uses as its longest horizon.
MAX_HOLD_SESSIONS = 20
HORIZONS = (1, 5, 10, 20)

# The list goes out at 09:15 IST, as NSE's normal session opens. Within 15
# minutes of that, a follower's order lands close enough to the open for the
# opening price to stand in for the fill; after it, it does not.
OPEN_FILL_DEADLINE = (9, 30)

BENCHMARKS = {
    "nifty50": "^NSEI",
    "nifty200_momentum30": "MOM30IETF",
}
PRIMARY_BENCHMARK = "nifty200_momentum30"
# The selection control — see BasketBenchmark.
UNIVERSE_BENCHMARK = "universe_equal_weight"

# Fixed seed: the same record must always produce the same interval.
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261030


@dataclass(frozen=True)
class Call:
    signal_id: int
    date: date_type
    stock_id: int
    symbol: str
    entry_low: Decimal
    entry_high: Decimal
    stop_loss: Decimal
    target_price: Decimal
    reference_date: date_type
    generated_at: datetime

    @property
    def atr(self) -> Decimal:
        # The published zone is reference_close ± 0.5 ATR (levels.py), so its
        # width is exactly one ATR, to the paisa it was rounded to.
        return self.entry_high - self.entry_low

    @property
    def reference_close(self) -> Decimal:
        return (self.entry_high + self.entry_low) / 2


@dataclass(frozen=True)
class Bar:
    date: date_type
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class ExitRule:
    """How a position closes. The default is the call exactly as published.

    The alternatives exist for diagnosis (scripts/forward_exit_diagnosis.py),
    never to pick a better rule from this record: every one of them is
    in-sample here.
    """
    name: str = "as_published"
    stop_atr: Decimal | None = None       # None: the published stop
    target_atr: Decimal | None = None     # None: the published target
    support_lookback: int | None = None   # stop at the N-session low before the call
    use_stop: bool = True
    use_target: bool = True
    # Exit at the close of this session if the position is below its fill.
    time_stop_sessions: int | None = None
    max_hold: int = MAX_HOLD_SESSIONS


AS_PUBLISHED = ExitRule()


@dataclass
class Campaign:
    stock_id: int
    symbol: str
    calls: list[Call]
    fill_date: date_type
    fill_price: Decimal
    fill_how: str                  # "open" | "limit"
    stop: Decimal | None
    target: Decimal | None
    exit_date: date_type | None = None
    exit_price: Decimal | None = None
    exit_reason: str = "open"      # "stop" | "target" | "time" | "time_stop" | "open"
    ambiguous: bool = False
    held_sessions: int = 0
    horizon_closes: dict[int, tuple[date_type, Decimal]] = field(default_factory=dict)
    window_low: Decimal | None = None
    window_high: Decimal | None = None
    window_sessions: int = 0

    @property
    def mae_pct(self) -> float | None:
        return None if self.window_low is None else float((self.window_low / self.fill_price - 1) * 100)

    @property
    def mfe_pct(self) -> float | None:
        return None if self.window_high is None else float((self.window_high / self.fill_price - 1) * 100)

    @property
    def first_call(self) -> Call:
        return self.calls[0]

    @property
    def resolved(self) -> bool:
        return self.exit_reason != "open"


# ------------------------------------------------------------------- core --

def _aware(dt: datetime) -> datetime:
    # Postgres returns generated_at tz-aware; SQLite hands back naive UTC.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _published_near_open(call: Call) -> bool:
    generated = _aware(call.generated_at).astimezone(IST)
    return generated.date() < call.date or (
        generated.date() == call.date and (generated.hour, generated.minute) <= OPEN_FILL_DEADLINE
    )


def _levels(call: Call, rule: ExitRule, prior: list[Bar]) -> tuple[Decimal | None, Decimal | None]:
    stop: Decimal | None = call.stop_loss
    if rule.stop_atr is not None:
        stop = call.reference_close - rule.stop_atr * call.atr
    if rule.support_lookback is not None:
        window = prior[-rule.support_lookback:]
        # Not enough history for the window: no structural stop can be drawn,
        # so the published one stands rather than a shorter, different window.
        if len(window) == rule.support_lookback:
            stop = min(b.low for b in window)
    if not rule.use_stop:
        stop = None

    target: Decimal | None = call.target_price
    if rule.target_atr is not None:
        target = call.reference_close + rule.target_atr * call.atr
    if not rule.use_target:
        target = None
    return stop, target


def _fill(call: Call, bar: Bar | None, stop: Decimal | None) -> tuple[Decimal | None, str]:
    if bar is None:
        return None, "no_bar"
    if _published_near_open(call) and bar.open <= call.entry_high:
        if stop is not None and bar.open <= stop:
            # Opened through the stop: the setup was void before it began.
            return None, "opened_below_stop"
        return bar.open, "open"
    if bar.low <= call.entry_high:
        if stop is not None and call.entry_high <= stop:
            return None, "opened_below_stop"
        return call.entry_high, "limit"
    return None, "missed"


def _manage(c: Campaign, bars: list[Bar], fill_idx: int, rule: ExitRule) -> None:
    """Walk forward from the session after the fill. The fill session itself is
    not checked against stop or target: with one bar per day there is no way
    to know whether its low or high came after the fill."""
    for k in range(1, rule.max_hold + 1):
        j = fill_idx + k
        if j >= len(bars):
            c.exit_reason = "open"
            c.held_sessions = k - 1
            return
        b = bars[j]
        exit_price, reason = None, None
        if c.stop is not None and b.open <= c.stop:
            exit_price, reason = b.open, "stop"
        elif c.target is not None and b.open >= c.target:
            exit_price, reason = b.open, "target"
        else:
            hit_stop = c.stop is not None and b.low <= c.stop
            hit_target = c.target is not None and b.high >= c.target
            if hit_stop and hit_target:
                exit_price, reason, c.ambiguous = c.stop, "stop", True
            elif hit_stop:
                exit_price, reason = c.stop, "stop"
            elif hit_target:
                exit_price, reason = c.target, "target"
        if reason is None and rule.time_stop_sessions is not None and k == rule.time_stop_sessions:
            if b.close < c.fill_price:
                exit_price, reason = b.close, "time_stop"
        if reason is None and k == rule.max_hold:
            exit_price, reason = b.close, "time"
        if reason is not None:
            c.exit_date, c.exit_price, c.exit_reason, c.held_sessions = b.date, exit_price, reason, k
            return


def _observe(c: Campaign, bars: list[Bar], fill_idx: int) -> None:
    """Exit-rule-independent measurements over the MAX_HOLD_SESSIONS after the
    fill: the close at each horizon, and the lowest and highest prices
    reached whether or not the position was still open by then."""
    window = bars[fill_idx + 1: fill_idx + 1 + MAX_HOLD_SESSIONS]
    c.window_sessions = len(window)
    for h in HORIZONS:
        if h <= len(window):
            c.horizon_closes[h] = (window[h - 1].date, window[h - 1].close)
    if window:
        c.window_low = min(b.low for b in window)
        c.window_high = max(b.high for b in window)


def build_campaigns(
    calls: list[Call],
    bars_by_stock: dict[int, list[Bar]],
    rule: ExitRule = AS_PUBLISHED,
) -> tuple[list[Campaign], dict[str, int], int]:
    """Turn calls into positions. Returns (campaigns, unfilled counts by
    reason, number of calls absorbed into an already-open position)."""
    by_stock: dict[int, list[Call]] = defaultdict(list)
    for call in sorted(calls, key=lambda c: c.date):
        by_stock[call.stock_id].append(call)

    campaigns: list[Campaign] = []
    unfilled: dict[str, int] = defaultdict(int)
    absorbed = 0
    for stock_id, stock_calls in by_stock.items():
        bars = bars_by_stock.get(stock_id, [])
        index = {b.date: i for i, b in enumerate(bars)}
        current: Campaign | None = None
        for call in stock_calls:
            if current is not None and (current.exit_date is None or call.date <= current.exit_date):
                current.calls.append(call)
                absorbed += 1
                continue
            i = index.get(call.date)
            if i is None and (not bars or call.date > bars[-1].date):
                # The call's session has not been ingested yet (today's list
                # before tonight's run). Not a miss — nothing is known yet.
                unfilled["pending"] += 1
                continue
            stop, target = _levels(call, rule, bars[:i] if i is not None else [])
            price, how = _fill(call, bars[i] if i is not None else None, stop)
            if price is None:
                unfilled[how] += 1
                continue
            current = Campaign(stock_id=stock_id, symbol=call.symbol, calls=[call], fill_date=call.date,
                               fill_price=price, fill_how=how, stop=stop, target=target)
            _manage(current, bars, i, rule)
            _observe(current, bars, i)
            campaigns.append(current)
    return campaigns, dict(unfilled), absorbed


# ------------------------------------------------------------ measurement --

def _net_pct(entry: Decimal, exit_: Decimal, cost: Decimal) -> float:
    return float((exit_ / entry - 1 - cost) * 100)


class SeriesBenchmark:
    """One instrument's daily (open, close). Window = open of the first
    session to close of the last — the window a position was held."""

    def __init__(self, bars: dict[date_type, tuple[Decimal, Decimal]]):
        self.bars = bars

    def window(self, start: date_type, end: date_type) -> float | None:
        if start not in self.bars or end not in self.bars:
            return None
        open_, _ = self.bars[start]
        _, close = self.bars[end]
        if not open_:
            return None
        return float((close / open_ - 1) * 100)


class BasketBenchmark:
    """Every stock with a bar on both dates, equal weight, bought at the open
    and held to the close — what picking from the same pool WITHOUT the
    strategy would have returned over the same window.

    This is the control that separates selection from exposure. The picks
    are mostly small and mid caps; NIFTY200 Momentum 30 holds large ones. In
    a window where smaller stocks did better, the picks beat that index with
    no selection skill at all — and against this basket they would not.
    Survivorship within a window is limited to stocks that stopped trading
    before its last day, which drop out of that window's average.

    Held as one float array per date (a column per stock, NaN where it has no
    bar) rather than a Bar per row: this is a statistic over ~600 stocks, not
    a money amount, and the API process it runs in is memory-limited.
    """

    def __init__(self, opens: dict[date_type, np.ndarray], closes: dict[date_type, np.ndarray]):
        self.opens = opens
        self.closes = closes

    @classmethod
    def from_rows(cls, rows) -> "BasketBenchmark":
        """rows: (stock_id, date, open, close), any order; None for missing."""
        rows = list(rows)
        columns = {sid: i for i, sid in enumerate(sorted({r[0] for r in rows}))}
        opens: dict[date_type, np.ndarray] = {}
        closes: dict[date_type, np.ndarray] = {}
        for sid, d, o, c in rows:
            if d not in opens:
                opens[d] = np.full(len(columns), np.nan)
                closes[d] = np.full(len(columns), np.nan)
            if o is not None:
                opens[d][columns[sid]] = float(o)
            if c is not None:
                closes[d][columns[sid]] = float(c)
        return cls(opens, closes)

    def window(self, start: date_type, end: date_type) -> float | None:
        o, c = self.opens.get(start), self.closes.get(end)
        if o is None or c is None:
            return None
        both = ~np.isnan(o) & ~np.isnan(c) & (np.nan_to_num(o) > 0)
        if not both.any():
            return None
        return float(np.mean(c[both] / o[both] - 1) * 100)


def _profit_factor(returns: list[float]) -> float | None:
    gains = sum(r for r in returns if r > 0)
    losses = -sum(r for r in returns if r < 0)
    if losses == 0:
        return None  # undefined, not infinite: with no losing trade there is nothing to divide by
    return round(gains / losses, 2)


def _stats(returns: list[float]) -> dict:
    if not returns:
        return {"sample": 0}
    arr = np.array(returns)
    return {
        "sample": len(returns),
        "mean_net_return_pct": round(float(arr.mean()), 2),
        "median_net_return_pct": round(float(np.median(arr)), 2),
        "win_rate_pct": round(100 * float((arr > 0).mean()), 1),
        "profit_factor": _profit_factor(returns),
    }


def _vs(pairs: list[tuple[float, float | None]]) -> dict:
    matched = [(r, b) for r, b in pairs if b is not None]
    if not matched:
        return {"sample": 0}
    excess = [r - b for r, b in matched]
    return {
        "sample": len(matched),
        "mean_excess_pct": round(sum(excess) / len(excess), 2),
        "beat_rate_pct": round(100 * sum(1 for e in excess if e > 0) / len(excess), 1),
    }


def bootstrap_mean_ci(values: list[float], clusters: list, resamples: int = BOOTSTRAP_RESAMPLES,
                      seed: int = BOOTSTRAP_SEED) -> tuple[float, float] | None:
    """95% interval for a mean, resampling whole clusters (here: the week the
    position was opened) instead of single trades. Positions opened in the
    same week share most of their market, so resampling them one at a time
    pretends to an independence they do not have and gives too narrow an
    interval. Still optimistic: 20-session windows overlap ACROSS weeks too."""
    groups: dict = defaultdict(list)
    for v, k in zip(values, clusters):
        groups[k].append(v)
    keys = list(groups)
    if len(keys) < 2:
        return None
    rng = np.random.default_rng(seed)
    sums = np.array([sum(groups[k]) for k in keys])
    counts = np.array([len(groups[k]) for k in keys])
    picks = rng.integers(0, len(keys), size=(resamples, len(keys)))
    means = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
    return round(float(np.percentile(means, 2.5)), 2), round(float(np.percentile(means, 97.5)), 2)


def summarize(campaigns: list[Campaign], unfilled: dict[str, int], absorbed: int,
              benchmarks: dict, cost: Decimal = ROUND_TRIP_COST) -> dict:
    attempted = len(campaigns) + sum(n for reason, n in unfilled.items() if reason != "pending")

    horizons = []
    for h in HORIZONS:
        done = [c for c in campaigns if h in c.horizon_closes]
        rets = [_net_pct(c.fill_price, c.horizon_closes[h][1], cost) for c in done]
        row = {"horizon_sessions": h, **_stats(rets), "vs": {}}
        for name, bench in benchmarks.items():
            row["vs"][name] = _vs([
                (r, bench.window(c.fill_date, c.horizon_closes[h][0])) for r, c in zip(rets, done)
            ])
        horizons.append(row)

    resolved = [c for c in campaigns if c.resolved]
    managed_rets = [_net_pct(c.fill_price, c.exit_price, cost) for c in resolved]
    reasons = defaultdict(int)
    for c in resolved:
        reasons[c.exit_reason] += 1
    managed = {
        **_stats(managed_rets),
        "exits": dict(reasons),
        "ambiguous_same_bar": sum(1 for c in resolved if c.ambiguous),
        "still_open": len(campaigns) - len(resolved),
        "mean_holding_sessions": round(sum(c.held_sessions for c in resolved) / len(resolved), 1) if resolved else None,
        "vs": {
            name: _vs([(r, bench.window(c.fill_date, c.exit_date)) for r, c in zip(managed_rets, resolved)])
            for name, bench in benchmarks.items()
        },
    }

    full = [c for c in campaigns if c.window_sessions >= MAX_HOLD_SESSIONS]
    excursions = {"sample": len(full)}
    if full:
        excursions.update({
            "median_mae_pct": round(float(np.median([c.mae_pct for c in full])), 2),
            "median_mfe_pct": round(float(np.median([c.mfe_pct for c in full])), 2),
            # How often each level was reached at all inside the window,
            # regardless of which came first — compare with managed["exits"].
            "reached_stop_pct": round(100 * sum(
                1 for c in full if c.stop is not None and c.window_low <= c.stop) / len(full), 1),
            "reached_target_pct": round(100 * sum(
                1 for c in full if c.target is not None and c.window_high >= c.target) / len(full), 1),
        })

    # Mean excess at the longest horizon, per benchmark, with an interval.
    longest = max(HORIZONS)
    uncertainty = {}
    for name, bench in benchmarks.items():
        pairs = []
        for c in campaigns:
            if longest not in c.horizon_closes:
                continue
            end_date, end_close = c.horizon_closes[longest]
            b = bench.window(c.fill_date, end_date)
            if b is not None:
                pairs.append((_net_pct(c.fill_price, end_close, cost) - b, c.fill_date.isocalendar()[:2]))
        if not pairs:
            uncertainty[name] = {"positions": 0}
            continue
        ci = bootstrap_mean_ci([v for v, _ in pairs], [k for _, k in pairs])
        uncertainty[name] = {
            "horizon_sessions": longest,
            "mean_excess_pct": round(sum(v for v, _ in pairs) / len(pairs), 2),
            "ci95_low": ci[0] if ci else None,
            "ci95_high": ci[1] if ci else None,
            "positions": len(pairs),
            "weeks": len({k for _, k in pairs}),
            "excludes_zero": bool(ci and (ci[0] > 0 or ci[1] < 0)),
        }

    return {
        "positions": {
            "opened": len(campaigns),
            "unique_stocks": len({c.stock_id for c in campaigns}),
            "calls_absorbed_into_open_positions": absorbed,
            "unfilled": dict(unfilled),
            "fill_rate_pct": round(100 * len(campaigns) / attempted, 1) if attempted else None,
            "filled_at_open": sum(1 for c in campaigns if c.fill_how == "open"),
            "filled_at_entry_high": sum(1 for c in campaigns if c.fill_how == "limit"),
        },
        "horizons": horizons,
        "managed": managed,
        "excursions": excursions,
        "uncertainty": uncertainty,
    }


def split_clean(calls: list[Call], sessions: list[date_type]) -> tuple[list[Call], dict[date_type, frozenset[str]]]:
    """(the calls on days with no excluding flag, flags for every published day)."""
    by_date: dict[date_type, list[Call]] = defaultdict(list)
    for c in calls:
        by_date[c.date].append(c)
    flags_by_date = {
        d: flag_day(
            PublishedDay(d, min(_aware(c.generated_at) for c in cs), tuple(c.reference_date for c in cs)),
            sessions,
        )
        for d, cs in by_date.items()
    }
    return [c for c in calls if not (flags_by_date[c.date] & EXCLUDING_FLAGS)], flags_by_date


def score_record(calls: list[Call], bars_by_stock: dict[int, list[Bar]], sessions: list[date_type],
                 benchmarks: dict, rule: ExitRule = AS_PUBLISHED, cost: Decimal = ROUND_TRIP_COST) -> dict:
    """Flag every published day, then score the clean record and the full one."""
    by_date: dict[date_type, list[Call]] = defaultdict(list)
    for c in calls:
        by_date[c.date].append(c)
    clean_calls, flags_by_date = split_clean(calls, sessions)
    excluded_days = sorted(d for d, f in flags_by_date.items() if f & EXCLUDING_FLAGS)

    flag_counts: dict[str, int] = defaultdict(int)
    for f in flags_by_date.values():
        for name in f:
            flag_counts[name] += 1

    clean = summarize(*build_campaigns(clean_calls, bars_by_stock, rule), benchmarks, cost)
    everything = summarize(*build_campaigns(calls, bars_by_stock, rule), benchmarks, cost)

    return {
        "exit_rule": rule.name,
        "round_trip_cost_pct": float(cost * 100),
        "max_hold_sessions": rule.max_hold,
        "benchmarks": {**BENCHMARKS, UNIVERSE_BENCHMARK: "equal-weight basket of every stock with prices"},
        "primary_benchmark": PRIMARY_BENCHMARK,
        "selection_control": UNIVERSE_BENCHMARK,
        "first_call_date": min(by_date).isoformat() if by_date else None,
        "last_call_date": max(by_date).isoformat() if by_date else None,
        "published_days": len(by_date),
        "published_calls": len(calls),
        "clean_days": len(by_date) - len(excluded_days),
        "clean_calls": len(clean_calls),
        "excluded_days": [
            {"date": d.isoformat(), "flags": sorted(flags_by_date[d] & EXCLUDING_FLAGS)} for d in excluded_days
        ],
        "flag_counts": dict(flag_counts),
        "stale_reference_days": sorted(d.isoformat() for d, f in flags_by_date.items() if STALE_REFERENCE in f),
        "clean": clean,
        "all_published": everything,
    }


# ------------------------------------------------------------- DB loading --

def _to_decimal(v) -> Decimal | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    f = float(v)
    if f != f:  # NaN
        return None
    return Decimal(str(round(f, 4)))


def load_calls(db: Session) -> list[Call]:
    rows = (
        db.query(DailySignal, Stock.symbol)
        .join(Stock, Stock.id == DailySignal.stock_id)
        .order_by(DailySignal.date, DailySignal.rank)
        .all()
    )
    return [
        Call(signal_id=s.id, date=s.date, stock_id=s.stock_id, symbol=symbol,
             entry_low=_to_decimal(s.entry_low), entry_high=_to_decimal(s.entry_high),
             stop_loss=_to_decimal(s.stop_loss), target_price=_to_decimal(s.target_price),
             reference_date=s.reference_date, generated_at=s.generated_at)
        for s, symbol in rows
    ]


def load_bars(db: Session, stock_ids: list[int], since: date_type) -> dict[int, list[Bar]]:
    rows = (
        db.query(PriceHistory.stock_id, PriceHistory.date, PriceHistory.open,
                 PriceHistory.high, PriceHistory.low, PriceHistory.close, PriceHistory.volume)
        .filter(PriceHistory.stock_id.in_(stock_ids), PriceHistory.date >= since)
        .order_by(PriceHistory.stock_id, PriceHistory.date)
        .all()
    )
    out: dict[int, list[Bar]] = defaultdict(list)
    for stock_id, d, o, h, l, c, v in rows:
        values = [_to_decimal(x) for x in (o, h, l, c)]
        # An incomplete bar is skipped, not patched: missing stays missing.
        # So is a no-trade placeholder already in the table (see
        # ingestion.is_no_trade_placeholder): a position cannot fill, or be
        # held through a "session", on a day nothing traded.
        if any(x is None for x in values) or is_no_trade_placeholder(*values, v):
            continue
        out[stock_id].append(Bar(d, *values))
    return dict(out)


def load_sessions(db: Session, since: date_type) -> list[date_type]:
    """Dates on which at least one stock really traded. A date whose only
    bars are no-trade placeholders (a holiday Yahoo filled in) is not a
    session, so a call dated on it is flagged CLOSED_DAY."""
    traded = (PriceHistory.volume > 0) | (PriceHistory.high != PriceHistory.low)
    return [
        d for (d,) in db.query(PriceHistory.date)
        .filter(PriceHistory.date >= since, traded)
        .distinct().order_by(PriceHistory.date)
    ]


def load_benchmark(symbol: str) -> SeriesBenchmark:
    """Daily (open, close) from Yahoo. A failed fetch is an empty series —
    comparisons against it come back with sample 0, not a guessed number."""
    from app.services.market_data import fetch_price_history

    try:
        df = fetch_price_history(symbol, period="2y")
    except Exception as exc:  # network, rate limit, unknown symbol: all the same to the caller
        logger.warning("forward_scoring.benchmark_unavailable symbol=%s error=%s", symbol, exc)
        return SeriesBenchmark({})
    out = {}
    for row in df.itertuples():
        o, c = _to_decimal(row.open), _to_decimal(row.close)
        if o is not None and c is not None:
            out[row.date] = (o, c)
    return SeriesBenchmark(out)


def compute_realistic_record(db: Session, rule: ExitRule = AS_PUBLISHED) -> dict:
    calls = load_calls(db)
    if not calls:
        return score_record([], {}, [], {}, rule)
    # Far enough back for a 20-session structural stop before the first call.
    first = min(c.date for c in calls)
    since = first - timedelta(days=45)
    bars = load_bars(db, sorted({c.stock_id for c in calls}), since)
    sessions = load_sessions(db, since)
    benchmarks = {name: load_benchmark(symbol) for name, symbol in BENCHMARKS.items()}
    # Every stock, not only the picked ones, from the first call on.
    basket_rows = (
        db.query(PriceHistory.stock_id, PriceHistory.date, PriceHistory.open, PriceHistory.close)
        .filter(PriceHistory.date >= first, (PriceHistory.volume > 0) | (PriceHistory.high != PriceHistory.low))
        .yield_per(5000)
    )
    benchmarks[UNIVERSE_BENCHMARK] = BasketBenchmark.from_rows(basket_rows)
    return score_record(calls, bars, sessions, benchmarks, rule)


def record_fingerprint(db: Session) -> str:
    """Changes whenever the record's inputs can: a new call, or a finished
    price run. Cheap — two small aggregates, no scan of price_history."""
    count, last = db.query(func.count(DailySignal.id), func.max(DailySignal.date)).one()
    run = (
        db.query(func.max(PipelineRun.finished_at))
        .filter(PipelineRun.run_type.in_(["incremental", "full_rebuild"]), PipelineRun.status != "running")
        .scalar()
    )
    return f"{count}:{last}:{run.isoformat() if run else None}"


def get_realistic_record(db: Session) -> dict:
    """The record for the current data, computed at most once per fingerprint
    (the nightly run warms it; see scheduler._run_incremental_update)."""
    from app.core.cache import get_realistic_record_cache, set_realistic_record_cache

    fingerprint = record_fingerprint(db)
    cached = get_realistic_record_cache(fingerprint)
    if cached is not None:
        return cached
    record = compute_realistic_record(db)
    set_realistic_record_cache(fingerprint, record)
    return record
