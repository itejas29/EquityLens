# EquityLens — audit report

Conducted 2026-09-09 to 2026-09-10 against commit `ba84aaa`.
26 commits. Tests 2 → 207. Backend 13,688 lines; test suite 3,579 lines.

Every finding below was **reproduced before it was fixed** and, where the fix
was non-obvious, **verified failing on the pre-fix code**. Numbers quoted are
measurements, not estimates. Findings that turned out to be sound are recorded
as such — a "no defect found" is a result, and omitting them would make the
report look more productive than the audit was.

---

## The one sentence that matters

**The strategy has no measurable edge, and every correction made during this
audit moved the evidence further in that direction.** The live track record now
reads −1.60 / −2.23 / −1.18 percentage points against NIFTY at 1, 5 and 10 days
when measured from a price a buyer could actually obtain, with 3 targets hit
against 14 stops. Phases 17–20 reached the same conclusion from backtests. Two
independent methods agreeing is a real result; it is just not a favourable one.

What the audit improved is not the strategy. It is whether the platform can be
trusted to report on one.

---

## Findings by severity

### 1. Concurrent buys lost a ₹400,240 debit — CRITICAL, fixed

Reproduced on Postgres 16: two threads buying into one ₹1,000,000 account
recorded ₹800,480 of cost basis and left cash at ₹599,760 instead of ₹199,520.
Postgres defaults to READ COMMITTED; the read-check-write on `cash` is a
textbook lost update. The same interleaving also opened two positions in one
stock, defeating the no-pyramiding rule.

Fixed with `SELECT ... FOR UPDATE`, taken before the position check, **plus
`.populate_existing()`** — without which the lock is held around a value read
before the lock existed and the same ₹400,240 still vanishes. A partial unique
index (`uq_paper_trade_open_position`) is the DB-level backstop.

### 2. Survivorship bias in every backtest — HIGH, measured, not fixed by design

`Stock.is_active == True` is today's universe projected backwards. 67 inactive
stocks holding 112,385 bars are excluded from every run, and the active
universe grows from 315 names with 2016 data to 500 in 2026. It inflates
returns, and more than average for momentum.

`include_inactive` exists and defaults to False so published phases stay
reproducible. Closing the delisting hole was a prerequisite: a position whose
stock stopped having bars was held forever at its last traded price.
[survivorship-bias.md](survivorship-bias.md) · [net-bias.md](net-bias.md)

### 3. Six unauthenticated write endpoints, and a free login DoS — HIGH, fixed

`ingest`, `refresh`, `compute-indicators`, `catalogue/refresh`,
`scoring/run-universe` and `daily-signals/run` accepted anonymous callers. The
last republishes a shortlist the product describes as frozen. `ingest`'s own
docstring claimed a rate limit its decorator did not carry.

`/auth/login` had no rate limit and bcrypt costs **214ms of CPU per attempt**,
measured — roughly 9 concurrent attempts saturate both vCPUs indefinitely, free
to the attacker. Also closed: a 200× timing oracle on unknown emails.

### 4. Unadjusted splits corrupting the momentum signal — HIGH, fixed

Nine split-shaped discontinuities found in production. `TDPOWERSYS` was stored
at ₹1,507.50 for a date Yahoo now returns ₹753.75 — three weeks old, inside the
12-month momentum window. Two distinct causes: our gap-fill never revisiting
restated history, and Yahoo applying split adjustments only from 1 January of
the split's year. [price-series-discontinuities.md](price-series-discontinuities.md)

### 5. The AI cycle was not atomic — HIGH, fixed

`run.status = "failed"; db.commit()` committed every trade already flushed. A
cycle that sold two positions and then raised left the account half-rebalanced
— and, since a failed run no longer consumes the month's rebalance, the next
day rebalanced again from that partial state.

### 6. Reported risk:reward was the parameter, not the outcome — HIGH, fixed

The target is built off the close while the entry zone extends half an ATR
above it. Measured on 60 live stocks: **1.67 published as 2.00**. On the
`recommendations` defaults a worked case gives **0.50 against 2.00** — a 4×
overstatement on a BUY call. The frontend already computed the honest number in
one place and the inflated one in another.

### 7. Track Record measured returns from an unobtainable price — HIGH, fixed

Returns were measured from `reference_close`; the call says buy in the entry
zone. Across 168 signals `entry_high` sits a mean of 1.276% above it. The edge
is materially worse when measured honestly (table above).
[track-record-basis.md](track-record-basis.md)

### 8. Two ML leaks — HIGH, fixed

The train/test split cut **mid-date** on a ragged panel and never **purged** the
20-day label horizon. Six fundamentals features carried **a decade of
look-ahead** — snapshots spanning three weeks broadcast across rows back to
2016, constant per stock, functioning as a stock-identity label. The module
documented this as a *loss* of information; it was a *leak*.
[ml-leakage.md](ml-leakage.md)

### 9. Money was float — MEDIUM, fixed

Ledger drifted ₹0.02 over 1,293 randomised round trips. Small, slow, and the
kind of thing that makes a ledger stop reconciling rather than visibly wrong.
Note: the P&L identity residual was ~1e-10, not a rupee figure — I initially
led with that and it was the weaker of the two findings.

### 10. Three unbounded upstream dependencies — MEDIUM/HIGH, fixed

Redis and psycopg2 both defaulted to waiting forever on services that are
across the internet. `GET /daily-signals` — the main page, unauthenticated —
fetched `^NSEI` from yfinance **on every request**. A Redis outage returned 500s
on three pages and failed the whole trading loop, all of which have working
database fallbacks.

**This is the codebase's characteristic failure mode**: it was written assuming
its dependencies are local and free. Every one of the four historical
memory/outage incidents in the git log has that shape.

### 11. The heavy-job "mutex" was a boolean — MEDIUM, fixed

Demonstrated: 3 concurrent heavy jobs where the maximum must be 1, and the
first to finish cleared the flag for the others. `daily_price_update` and
`weekly_universe_rebuild` fire at the same minute.

### 12. Risk checks that could not run looked like they passed — MEDIUM, fixed

A held position with no price was skipped silently; one priced off the previous
close was indistinguishable from one priced live. At 09:20 IST the stored close
is *yesterday's*, so a dead fast-quote loop meant every stop was judged against
a pre-gap price.

### 13. Sortino was not Sortino — MEDIUM, fixed

Standard deviation of the negative days rather than downside deviation about
the target. Denominator 0.846× correct; |Sortino| overstated 1.18×. Affects
three published tables, no prose conclusion.
[backtest-metrics.md](backtest-metrics.md)

### 14. Notifications leaked the bot token and would drop M&M — MEDIUM, fixed

`exc_info=True` on a `requests` exception writes the URL — and Telegram's API
puts the token *in* the URL. Five active NSE symbols contain `&`
(ARE&M, GVT&D, J&KBANK, M&M, M&MFIN); unescaped, Telegram rejects the whole
message.

### 15. Two armed defects behind the ML serving gate — LOW today, fixed

The inference cross-section never expired (`invalidate_feature_cache()` had zero
callers) and stampeded on a cold cache. Dormant only because the ROC-AUC gate is
unmet. **A defect behind a feature flag is an armed defect, not a fixed one.**

---

## Checked and found sound

- **`_capture_ratios`** — Phase 19's downside-capture conclusion (154–196%)
  rests on it. Ten known-answer tests, all passing first run. **The conclusion
  stands.**
- **`_trade_metrics`**, CAGR, Sharpe, max drawdown, Calmar and their guards.
- **yfinance retry/backoff** — already has a 30s timeout and correct rate-limit
  classification. The unbounded-wait problem did not apply here.
- **The exception handler** — no stack traces leak; the catch-all is generic.
- **Frontend XSS surface** — zero `dangerouslySetInnerHTML`, `innerHTML`,
  `eval`, `document.write`. The token in `localStorage` is safe because there is
  no injection sink, which is the actual defence.
- **The 20-day track-record horizon being empty** — not the cutoff dropping
  signals; the app has simply only published since 2026-08-14.
- **`compute_market_regime`'s arithmetic** — genuinely point-in-time.
- **The ML serving gate** — the best thing in this codebase. Someone measured an
  unflattering ROC-AUC and wired the application to refuse to serve on it.

---

## Open, and owned by the user

1. **Nothing here is deployed.** 26 commits including an Alembic migration that
   runs on container start. Every fix protecting live money is theoretical
   until it ships.
2. **TDPOWERSYS needs a one-off full re-pull.** The detector prevents
   recurrence; it does not repair history already stored.
3. **Quantify survivorship** — one fold run with and without
   `include_inactive`. Needs the lab instance.
4. **Retrain the ML model.** Published metrics predate both leak fixes and the
   stored artifact was fitted on 31 features against a current default of 25.
5. **Re-run the phases** if the Sortino/benchmark-friction/universe corrections
   are wanted in the published tables — one run with all of them, so the
   numbers stay comparable.

---

## The safety net was itself verified

`.github/workflows/backend-tests.yml` now guards all of the above, so it was
run end to end from a clean virtualenv against a fresh Postgres — every step in
sequence, with the exact environment the workflow declares:

```
pip install -r requirements-dev.txt   ->  exit 0
python -m pytest -q                   ->  223 passed in 48.92s
alembic upgrade head (from empty)     ->  ... -> b7e3a91c5d24, exit 0
docker build .                        ->  exit 0
```

223 rather than 222 because the Postgres service makes the concurrent-buy
row-lock test runnable — the one guarding the ₹400,240 defect, which cannot run
on SQLite.

## Assessment

The engineering is now in reasonable shape: 207 tests where there were 2, CI
that runs them, a non-root container, bounded dependencies, and a money ledger
that reconciles exactly and survives concurrency.

The research is honest and the conclusion is negative. That combination is
worth more than it feels like: a platform that reports its own strategy's
failure accurately is a platform whose reports can be believed. The value here
is the measurement apparatus — point-in-time backtesting, forward-measured
outcomes, a serving gate that refuses to publish noise — not the momentum
strategy it currently measures.
