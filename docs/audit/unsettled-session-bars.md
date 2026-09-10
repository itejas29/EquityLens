# In-progress session bars stored as daily closes

Found 2026-09-10 while re-examining the nine split-shaped discontinuities in
[`price-series-discontinuities.md`](price-series-discontinuities.md). Three
unrelated symbols each disagreed with the provider on **exactly one date** and
no other, across thousands of bars:

```
CGCL         2026-08-25   243.79 -> 239.67   x1.0172   (1 of 2492 bars)
PARAS        2026-08-25  1413.80 -> 1445.20  x0.9783   (1 of 1224 bars)
TDPOWERSYS   2026-08-25   744.10 ->  729.40  x1.0202
```

One shared date across three unrelated companies is not three corporate
actions. It is one bad ingest.

## What the scan found

Every active stock compared against the provider for 2026-08-18 → 2026-09-05:

```
date          compared  disagree >1%
2026-08-25         500           172      <- 34.4%
every other day    500             0
```

At a tolerance tight enough to catch any real difference rather than only the
detector's 1% threshold:

```
2026-08-25, 500 stocks compared
  exact match     13
  <= 0.1%         83
  0.1 - 1%       232
  1 - 3%         152
  > 3%            20
  stored volume below 90% of the session's true volume:  434 / 500
```

**487 of 500 stored closes for that date are wrong.** The volume column is what
makes it conclusive — a finished bar carries the whole session's volume.

Spot check against the settled bars:

```
symbol       stored close  true close   true low  true high  in range   stored vol   true vol   vol %
ABB               7466.00     7627.00    7435.00    7627.00      yes        16,565    178,245    9.3%
RELIANCE          1301.50     1317.00    1300.00    1317.10      yes       855,067  7,115,355   12.0%
INFY              1127.30     1144.00    1119.90    1144.00      yes     1,361,162  8,121,237   16.8%
HDFCBANK           727.50      727.50     722.00     728.70      yes     1,943,767 17,511,874   11.1%
```

Every stored close sits inside the true day's range, and every stored volume is
a fraction of the real one. These are snapshots of a session in progress,
written as if they were that session's close.

## How it happened

`pipeline_runs` for the day:

```
run_date=2026-08-24  incremental  incomplete  started 2026-08-25 09:58 IST  finished 10:07 IST
run_date=2026-08-24  incremental  complete    started 2026-08-25 10:18 IST  finished 10:18 IST
run_date=2026-08-25  incremental  complete    started 2026-08-25 20:01 IST  finished 20:01 IST
```

2026-08-24 was the day the backend moved from Render to EC2, so that evening's
scheduled ingest never ran. The catch-up runs for it were executed the next
morning — **63 minutes into the 2026-08-25 session**. yfinance answers a request
covering the current day with an in-progress bar, and nothing in the pipeline
declined it.

`daily_price_update_loop` cannot do this on its own; it refuses to run before
20:00 IST. Any out-of-band run can, and did.

### Why it never healed

`incremental_price_update` step 3: *"If latest_date >= today → already current,
skip."* Once the partial bar for today was on disk, every stock looked current,
and the real 20:00 IST run that evening **finished in 1.4 seconds for 501
stocks** — it fetched nothing at all. The partial bars became permanent.

The restatement overlap added earlier in this audit would now catch this the
*following* day for the 172 bars wrong by more than
`RESTATEMENT_TOLERANCE_PCT`, treating it as a corporate action and re-pulling.
That is the right repair reached by the wrong diagnosis, and it does nothing
for the 315 bars wrong by less than 1%.

## Downstream

- **567 indicator rows** dated 2026-08-25 were computed from these bars.
  RSI and MACD are recursive, so the error propagates forward from that date.
- **16 published signals** — 8 dated 2026-08-25 and 8 dated 2026-08-26 — carry
  `reference_date = 2026-08-25`. Entry zones, stops, targets and risk/reward on
  two consecutive published shortlists were derived from a price that was never
  a close.

Those 16 signals are **not** being rewritten. `DailySignal` is frozen at
generation time by design — *"the whole point of a dated call is that it does
not move after the fact"* — and back-dating them would destroy exactly the
property that makes the forward track record meaningful. They stand as
published, with this note as the record of what they rest on.

## Fixed

`_drop_unsettled_session` in `services/incremental.py`, applied on both the
full-pull and incremental paths, refuses to store a bar for a session that has
not settled. Cutoff is **16:00 IST** — deliberately later than the 15:40 in
`core.market_hours`, which is tuned for live price polling where a late tick is
harmless. A daily bar has to be final, so this leaves 30 minutes past the 15:30
close for the provider to settle it. The 20:00 IST nightly ingest is unaffected.

The drop happens *before* the restatement comparison, so an in-progress bar can
never be mistaken for the provider restating history.

All times are IST throughout. The server runs UTC, where `date.today()` is
still yesterday until 05:30 IST — deciding "has today settled?" against the
wrong calendar day is the same class of mistake.

Six regression tests in `tests/test_incremental.py`, including an end-to-end
one asserting a partial bar never reaches the database. Verified by negative
control: reverting the guard fails four of them.

## Repaired

`scripts/repair_price_history.py`, dry-run by default. Every value written
comes from the provider — nothing is back-adjusted from our own stored series,
which would be deriving prices.

- `--date 2026-08-25` re-fetches that session for every active stock.
- `--symbols TDPOWERSYS` re-pulls the full history at `period="max"`.

`period="max"` rather than `HISTORY_PERIOD`: a 10y pull for TDPOWERSYS starts
2016-09-12 while our stored series starts 2016-08-16, so it would leave 18 bars
on the old basis and *move* the discontinuity instead of removing it. `"max"`
reaches 2011 and covers everything. The script warns loudly if any stored bar
still predates what the provider will return.

Indicators are recomputed unbounded for every repaired stock, not bounded to
the 252-day lookback the nightly ingest uses, because a full re-pull changes
the price basis of the whole series.
