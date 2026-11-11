# Code audit — 2026-11-11

Scope: the backend paths named in the audit request, plus the frontend's link
structure and error display. Audited at `3ac1104`. Fixes are on branch
`audit/2026-11-11`. Test suite before: 453 passed, 1 skipped. After: 464
passed, 1 skipped.

Three production-data checks could not be run from this session and are listed
under "Not checked".

## Bugs found and fixed

All six are one family: a price from the previous session passing as today's.
They were fixed together in `af35cf3`, with `backend/tests/test_stale_quotes.py`.

| # | Severity | Where | Bug | Fix |
|---|---|---|---|---|
| 1 | Critical | `app/core/scheduler.py`, `_download_prices` | Yahoo's `period="1d"` returns the most recent session, not today. On an NSE holiday the previous session's bars were stamped with the fetch time and published as a live quote, so the tape showed "live" with stale prices and the session snapshot was re-dated to the holiday. | A bar that is not from today (IST) is dropped. A quote carries its bar's own time. |
| 2 | Critical | `app/services/ai_trading.py`, `_buy_pass` | The entry zone is built around the previous close, so a stale price is always "in zone". With the live tape absent on a rebalance day, every shortlisted stock was bought at yesterday's close. | An entry needs a quote from today's live tape. Otherwise it is skipped, recorded, and reported in the Telegram alert. |
| 3 | Medium | `app/services/paper_trading.py`, `_marks_with_source` | Any feed quote was labelled "tape", including the frozen last-session snapshot and quotes the tape carried forward. The AI cycle's "checked against the previous close" warning therefore missed exactly those cases. | New `_quote_source`: live means the feed is live and the quote's bar time is today. |
| 4 | Medium | `app/core/scheduler.py`, `ai_trading_loop` | The loop checked only for a weekday. On a weekday holiday it ran a cycle on the last session's prices, and on the month's first weekday it spent the month's only rebalance. | The loop waits for market hours and a live tape before reserving the day's run, and logs while waiting. |
| 5 | Low | `app/services/ai_trading.py:277` | A buy the paper account refused was a bare `continue`: no log, no record. | Logged and added to the cycle's skipped entries. |
| 6 | Low | `app/services/paper_trading.py`, `_latest_close` | It took the newest bar even when that bar's close was NULL, making the stock unpriceable although the previous close existed. | It takes the newest bar that has a close. |

Consequence of fix 4, stated plainly: if the price feed is down for a whole
trading day, that day has no stop or target check at all, where before it had a
check against stale prices. The loop logs `ai_trading.waiting` every 30 minutes
while it waits.

## Checked, no defect found

- **Frozen shortlists are not overwritten.** `generate_daily_signals` has no
  delete. It raises `ShortlistAlreadyPublished` for a date that already has
  rows (`daily_signals.py:597`), and `/daily-signals/run` returns 409. The
  delete-before-insert the request describes was removed on 2026-10-30.
- **`test_backtest_execution.py` passes.** There is no `PointInTimeSnapshot`
  import error on `main`; all its tests ran green before any change here.
- **Backtest pending entries and regime sells.** Pending entries are limit
  orders for the next session only and are cleared each day; regime trims sell
  at the next open. Covered by `test_backtest_execution.py`.
- **Forward-testing return basis.** `forward_testing.py` measures from
  `reference_close`, the previous close, and re-bases to `entry_high` in
  `_from_entry` (lines 243–266). The reference-close figure is not obtainable
  by a buyer; that is documented there. The record the app now leads with is
  `forward_scoring.py`, which fills at `entry_high` or the open, deducts 0.24%
  costs, and keeps one position per stock.
- **Scheduler mutex.** `_heavy_lock` is one `asyncio.Lock` on one event loop.
  Between `heavy_job_running()` and `async with _heavy_job(...)` there is no
  `await`, so two jobs cannot both pass the check, and the lock serialises
  them regardless. It holds only within one process: the Dockerfile starts a
  single uvicorn worker, and it must stay that way.
- **Swallowed exceptions.** Every `except` that ends in `pass`, `continue` or a
  bare `return` was read. Apart from bug 5, each either logs or handles an
  expected case (a symbol missing from a batch download, a malformed WebSocket
  frame, an expired token in the rate limiter).
- **Unhandled errors.** The catch-all handler returns `Internal server error`
  with no detail. Starlette re-raises after responding, so uvicorn logs the
  traceback.
- **NULL closes in computation.** `indicators.py`, `levels.py` and `scoring.py`
  test for NaN before use and return None; nothing is backfilled.
- **Nested links and buttons.** A scan of every `.jsx` file for `<a>`, `<Link>`,
  `<NavLink>` or `<button>` opened inside another found none. The one case, the
  mobile pick card, was fixed in `3ac1104`.
- **Frontend error text.** `apiErrorMessage` shows the API's own message, or
  the HTTP client's ("Request failed with status code 500"). No traceback can
  reach the UI because the API never sends one.

## Not checked

These need read access to the production database, which this session was not
permitted to use:

1. Active symbols whose newest bar is behind the newest session (renamed or
   delisted tickers that fail to download).
2. The count and dates of `price_history` rows with a NULL close.
3. Whether bugs 1, 2 and 4 fired in production: `ai_trading_runs` rows and AI
   paper trades dated on days with no price bars.

The queries are written and read-only (SELECT only).

## Deferred

- **Manual paper buys and sells fill at a held-over price when the market is
  shut**, without saying so on the trade. This is the intended behaviour for
  trading after hours, but the fill's source is not stored. Storing it needs a
  new column and a migration.
