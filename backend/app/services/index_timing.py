"""Index timing: hold NIFTY or cash by a trend rule, with costs and Indian
capital-gains tax — Phase 26 (docs/experiments/phase26_crash_brake/).

Pure functions over a daily close series. A decision taken at a session's
close is executed at the NEXT session's close — the pre-registration gives the
reason — so the position held on any day is the one decided the session before.
Trades happen only when a rule's decision changes; exposure is not
re-balanced back to target as prices drift.
"""

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

SMA_DAYS = 200
SMA_MONTHS = 10
V1_BEAR_EXPOSURE = 0.25   # V1's regime filter: 25% invested below the average
STCG_RATE = 0.20          # listed equity units held 12 months or less (rates since July 2024)
LTCG_RATE = 0.125         # held more than 12 months
CARRY_FORWARD_YEARS = 8   # a capital loss may be carried forward eight assessment years


# --------------------------------------------------------------- signals --

def decided_exposure(closes: pd.Series, rule: str) -> pd.Series:
    """The exposure each rule decides at each close (NaN before it can decide).

    closes: daily closes indexed by date, ascending.
    """
    if rule == "BH":
        return pd.Series(1.0, index=closes.index)
    if rule in ("R1", "R3"):
        sma = closes.rolling(SMA_DAYS, min_periods=SMA_DAYS).mean()
        below = 0.0 if rule == "R1" else V1_BEAR_EXPOSURE
        return pd.Series(np.where(closes >= sma, 1.0, below), index=closes.index).where(sma.notna())
    if rule == "R2":
        month = pd.Series([d.year * 12 + d.month for d in closes.index], index=closes.index)
        is_month_end = month.ne(month.shift(-1))
        ends = closes[is_month_end]
        sma = ends.rolling(SMA_MONTHS, min_periods=SMA_MONTHS).mean()
        decision = pd.Series(np.where(ends >= sma, 1.0, 0.0), index=ends.index).where(sma.notna())
        # A month-end decision stands until the next month-end's.
        return decision.reindex(closes.index).ffill()
    raise ValueError(f"unknown rule {rule!r}")


# ------------------------------------------------------------------- tax --

def financial_year(d: date) -> int:
    """Indian financial year, labelled by the calendar year it starts in (April)."""
    return d.year if d.month >= 4 else d.year - 1


def is_long_term(bought: date, sold: date) -> bool:
    """More than 12 months: sold after the first anniversary of the purchase."""
    try:
        anniversary = bought.replace(year=bought.year + 1)
    except ValueError:  # bought on 29 February
        anniversary = bought.replace(year=bought.year + 1, day=28)
    return sold > anniversary


@dataclass
class Lot:
    bought: date
    units: float
    cost: float   # rupees paid, charges included


@dataclass
class TaxBook:
    """Realised gains per financial year and losses carried forward, set off
    the way the Income-tax Act allows: a short-term loss against any gain, a
    long-term loss only against a long-term gain, current-year losses before
    brought-forward ones, a loss kept for at most eight years."""
    short: dict[int, float] = field(default_factory=dict)
    long: dict[int, float] = field(default_factory=dict)
    carried: list[list] = field(default_factory=list)   # [year, "short"|"long", amount]

    def realise(self, fy: int, gain: float, long_term: bool) -> None:
        book = self.long if long_term else self.short
        book[fy] = book.get(fy, 0.0) + gain

    def settle(self, fy: int) -> float:
        """Tax due for `fy`; updates the losses carried forward."""
        st, lt = self.short.get(fy, 0.0), self.long.get(fy, 0.0)
        # Current-year set-off: a short-term loss may absorb long-term gains.
        if st < 0 < lt:
            used = min(-st, lt)
            st, lt = st + used, lt - used
        # Brought-forward losses, oldest first, within their eight years.
        self.carried = [c for c in self.carried if fy - c[0] <= CARRY_FORWARD_YEARS]
        for c in sorted(self.carried, key=lambda c: c[0]):
            if c[1] == "short":
                for head in ("st", "lt"):
                    gain = st if head == "st" else lt
                    used = min(c[2], max(gain, 0.0))
                    c[2] -= used
                    if head == "st":
                        st -= used
                    else:
                        lt -= used
            else:
                used = min(c[2], max(lt, 0.0))
                c[2] -= used
                lt -= used
        self.carried = [c for c in self.carried if c[2] > 1e-9]
        # This year's unabsorbed losses go forward.
        if st < 0:
            self.carried.append([fy, "short", -st])
            st = 0.0
        if lt < 0:
            self.carried.append([fy, "long", -lt])
            lt = 0.0
        return STCG_RATE * st + LTCG_RATE * lt


# ------------------------------------------------------------- simulate --

@dataclass
class Run:
    values: pd.Series        # portfolio value at each close, before the final sale
    final_value: float       # after the final sale (and, if taxed, the final tax)
    switches: int
    invested_share: float    # mean exposure held, 0..1
    tax_paid: float


def simulate(closes: pd.Series, decided: pd.Series, start: date, capital: float = 1_000_000.0,
             cost: float = 0.001, cash_yield: float = 0.0, taxed: bool = False) -> Run:
    """Run one rule from `start`: at each close, first pay any tax due for the
    financial year just ended (selling units if cash is short), then execute the
    decision taken at the previous close."""
    dates = [d for d in closes.index if d >= start]
    prices = closes.loc[dates].to_numpy(dtype=float)
    # The decision in force at each close is the one taken at the previous close.
    decisions = decided.reindex(closes.index).shift(1).loc[dates].to_numpy(dtype=float)
    cash, lots, book = capital, [], TaxBook()
    exposure, switches, tax_paid = None, 0, 0.0
    values, held = [], []

    def units_held():
        return sum(lot.units for lot in lots)

    def buy(amount, d, px):
        nonlocal cash
        if amount <= 0:
            return
        lots.append(Lot(d, amount / (px * (1 + cost)), amount))
        cash -= amount

    def sell(units, d, px):
        nonlocal cash
        remaining = units
        while remaining > 1e-12 and lots:
            lot = lots[0]
            take = min(remaining, lot.units)
            basis = lot.cost * take / lot.units
            proceeds = take * px * (1 - cost)
            if taxed:
                book.realise(financial_year(d), proceeds - basis, is_long_term(lot.bought, d))
            lot.units -= take
            lot.cost -= basis
            cash += proceeds
            remaining -= take
            if lot.units <= 1e-12:
                lots.pop(0)

    for i, (d, px) in enumerate(zip(dates, prices)):
        if i > 0 and cash_yield:
            # Accrues by calendar day, as a liquid fund's does, weekends included.
            cash *= (1 + cash_yield) ** ((d - dates[i - 1]).days / 365)
        if taxed and i > 0 and financial_year(d) != financial_year(dates[i - 1]):
            due = book.settle(financial_year(dates[i - 1]))
            if due > cash:
                sell(min(units_held(), (due - cash) / (px * (1 - cost))), d, px)
            cash -= due
            tax_paid += due
        target = decisions[i]
        if not np.isnan(target) and target != exposure:
            value = cash + units_held() * px
            invested = units_held() * px
            if target > (invested / value if value else 0):
                buy(cash if target >= 1.0 else max(0.0, target * value - invested), d, px)
            else:
                sell(units_held() if target <= 0 else max(0.0, (invested - target * value) / px), d, px)
            switches += exposure is not None
            exposure = target
        values.append(cash + units_held() * px)
        held.append(units_held() * px / values[-1] if values[-1] else 0.0)

    last_day, last_px = dates[-1], prices[-1]
    sell(units_held(), last_day, last_px)
    if taxed:
        due = book.settle(financial_year(last_day))
        cash -= due
        tax_paid += due
    return Run(pd.Series(values, index=dates), cash, switches, float(np.mean(held)), tax_paid)


# --------------------------------------------------------------- metrics --

def max_drawdown(values: pd.Series) -> float:
    return float((values / values.cummax() - 1).min())


def cagr(start_value: float, end_value: float, start: date, end: date) -> float:
    years = (end - start).days / 365.25
    return (end_value / start_value) ** (1 / years) - 1


def metrics(run: Run, capital: float = 1_000_000.0) -> dict:
    v = run.values
    r = v.pct_change().dropna()
    g = cagr(capital, run.final_value, v.index[0], v.index[-1])
    dd = max_drawdown(v)
    years = (v.index[-1] - v.index[0]).days / 365.25
    return {
        "cagr_pct": g * 100, "max_drawdown_pct": dd * 100,
        "volatility_pct": float(r.std(ddof=1) * np.sqrt(252) * 100),
        "sharpe": float(r.mean() / r.std(ddof=1) * np.sqrt(252)) if r.std(ddof=1) > 0 else None,
        "calmar": g / abs(dd) if dd < 0 else None,
        "switches_per_year": run.switches / years, "invested_share_pct": run.invested_share * 100,
        "final_value": run.final_value, "tax_paid": run.tax_paid,
    }


def sub_period(values: pd.Series, first: date, last: date) -> dict:
    """CAGR, drawdown and Calmar inside [first, last] of one run, the peak reset at `first`."""
    v = values.loc[first:last]
    g = cagr(float(v.iloc[0]), float(v.iloc[-1]), v.index[0], v.index[-1])
    dd = max_drawdown(v)
    return {"cagr_pct": g * 100, "max_drawdown_pct": dd * 100, "calmar": g / abs(dd) if dd < 0 else None}


def downside_capture(values: pd.Series, bh_values: pd.Series) -> float:
    """Mean monthly return in the months buy-and-hold fell, as % of buy-and-hold's."""
    def monthly(v):
        ends = v.groupby([d.year * 12 + d.month for d in v.index]).last()
        return ends.pct_change().dropna()
    m, b = monthly(values), monthly(bh_values)
    down = b[b < 0].index
    return float(m.loc[down].mean() / b.loc[down].mean() * 100)


def drawdown_episodes(values: pd.Series, deeper_than: float) -> list[tuple[date, date, float]]:
    """(peak, trough, depth) for each fall from a high deeper than `deeper_than`
    (e.g. -0.15) before a new high was set."""
    out, peak_d, peak_v, trough_d, trough_v = [], values.index[0], float(values.iloc[0]), None, None
    for d, x in values.items():
        x = float(x)
        if x >= peak_v:
            if trough_v is not None and trough_v / peak_v - 1 <= deeper_than:
                out.append((peak_d, trough_d, trough_v / peak_v - 1))
            peak_d, peak_v, trough_d, trough_v = d, x, None, None
        elif trough_v is None or x < trough_v:
            trough_d, trough_v = d, x
    if trough_v is not None and trough_v / peak_v - 1 <= deeper_than:
        out.append((peak_d, trough_d, trough_v / peak_v - 1))
    return out
