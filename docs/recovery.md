# System Recovery & Backfill

If the EquityLens backend is offline for several days (e.g., your Mac is asleep over a long weekend), this document explains how the system catches up when turned back on.

## Price Data

**Automatic.** The next time 20:00 IST rolls around (or immediately if the system boots after 20:00 but before midnight), the incremental price pipeline will run. It will see that the latest stored date is several days old, and it will fetch all missing days in a single batched call. 

No manual intervention is required.

## Technical Indicators

**Automatic.** Indicators are recomputed in full from the stored price series every time the incremental price pipeline runs. When the price gap is filled, the indicators are immediately correct.

## Fundamentals

**Automatic.** Fundamentals are fetched once a month on the 1st at 21:00 IST. If the machine is off at that exact hour, it will run on the next available hour. It records its completion in the `pipeline_runs` table, ensuring it only succeeds once per month.

## Daily Signals

**Not Backfilled.** This is intentional.

Signals are a point-in-time snapshot representing what the system *would have told you to do* before the market opened. Generating them retroactively after seeing the day's price action is look-ahead contamination.

If the machine is off, no signals are generated for those dates. The `GET /api/v1/health/pipeline` endpoint will accurately report that the last signals generated are stale. When the machine comes back online, it will generate *today's* signals at 09:15, and the historical record will correctly show a gap for the days the system was down.
