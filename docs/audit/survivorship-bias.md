# Survivorship bias in the backtest universe

Measured 2026-09-10 against the production database.

## What the engine does

`backtest.run_backtest` builds its universe with:

```python
stocks = db.query(Stock).filter(Stock.is_active == True)
```

Point-in-time discipline in this engine is real but applies to the **data**:
every rebalance scores against `df[df["date"] <= today]`, and
`compute_point_in_time_universe` is careful about it. **Membership** is not
point-in-time. The universe is the set of stocks active *today*, projected
backwards across the entire window.

## Scale of the exclusion

```
stocks: active=500 inactive=67
  is_active=False:  67 stocks, 112,385 bars, 2016-08-16 .. 2026-08-28
  is_active=True:  500 stocks, 971,835 bars, 2016-08-16 .. 2026-09-09

price_history overall: 2016-08-16 .. 2026-09-09  (1,084,220 rows)
```

67 stocks with **112,385 bars of real price history** — a tenth of the stored
data — are excluded from every backtest that has ever been run. Their history
runs to 2026-08-28, so they were tradeable members during the window and then
stopped being members.

The active universe is also not stable backwards. Active stocks with at least
one bar in each year:

```
2016: 315   2019: 355   2022: 408   2025: 487
2017: 330   2020: 366   2023: 436   2026: 500
2018: 345   2021: 391   2024: 454
```

So an early fold is not "the 500-stock universe in 2016". It is the 315 members
of today's universe that already existed in 2016 — selected, by construction,
for having survived to 2026.

## Direction

**It inflates backtested returns.** The excluded names are the ones that left:
delisted, acquired, or dropped for failing the liquidity screen. For a momentum
strategy that matters more than average, because momentum's worst outcomes are
precisely the stocks that fall hard and keep falling until they are gone.

### What this means for what has already been published

Phase 19 concluded that V1 has **no measurable edge**, and reached that
conclusion with survivorship bias working *in the strategy's favour*. Removing
the bias can only strengthen that finding. None of the published negative
conclusions are at risk from this; a positive one would have been.

## The hole that had to be closed first

Before an unbiased run could be offered at all, the daily loop treated a
missing bar as "hold, can't evaluate triggers":

```python
if row is None or pd.isna(row["close"]):
    still_open.append(pos)
```

A position whose stock *stops* having bars was therefore held forever — valued
at its last traded price on every remaining day, then liquidated there at the
end. With only active stocks that almost never fired. With inactive stocks
included it would fire constantly, and it would make an unbiased run look
**better** than the biased one it was meant to correct.

`STALE_POSITION_EXIT_DAYS = 30` now forces an exit, tagged
`exit_reason="delisted"`. The exit price is the last known close, **not zero**:
a delisting is not always a wipeout and assuming one would be inventing a
number the data does not support. That assumption is optimistic — a stock that
stops trading has usually not stopped falling — so a run with
`include_inactive` is an **upper bound**, and the count of `delisted` exits in
the trade log says how much of the result rests on it.

## Status

- `BacktestConfig.include_inactive` exists and defaults to **False**.
- The default is left biased **on purpose**, so every published phase result
  stays reproducible. Turning it on changes what the strategy could have
  bought, which is a research decision rather than a bug fix.
- **Not yet done:** re-running a fold both ways to quantify the gap in points
  of CAGR. That needs the lab instance, which is occupied by the Phase 20 sweep.
