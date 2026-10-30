# Phase 23 — NSE-style normalized momentum vs V1: pre-registration

**Written 2026-10-30, before any run.** The commit that adds this file is the
proof of timing. Any change after results are seen is recorded below, with
the reason, and the original rule stays visible.

## Question

V1 ranks by raw 12-month-minus-1-month return. NSE's Nifty200 Momentum 30
divides 6- and 12-month price returns by one year of daily volatility and
averages the two z-scores. Does swapping **only** that ranking — everything
else in V1 frozen — improve V1 out of sample?

## Why this, and why only this

It is the main difference between V1 and the most widely used momentum
product in India, and it is a single, isolated change. Volatility-scaled
exposure, semi-annual reconstitution and multi-factor combinations are
separate questions, deliberately not bundled in, so a result can be
attributed.

## Design

- **Arms:** `v1_raw` (ranking_engine `momentum`, V1 as it runs) and
  `v1_normalized` (`momentum_normalized`: 126 and 252 sessions, 252-session
  volatility of daily log returns, no skipped month, no winsorizing — ranking
  only).
- **Frozen:** universe, 5-day trend gate, 4-ATR stop and 2R target, 90-day
  horizon, monthly rebalance, 200-DMA regime filter at 25% bear exposure,
  equal-weight sizing, max positions, sector cap.
- **Engine:** next-session execution with gap-aware stops (audit #19, #20
  fixed on `fix/backtest-next-session-execution`).
- **Folds:** Phase 20's: test windows of 6 months, rolled by 6 months, from
  2016-10 to 2026-08 (16 folds). Nothing is fitted on the preceding span.
- **Universe:** point-in-time top 500 by 20-day traded value in each fold,
  from a pool of at least 1,000 stocks (`phase18_prepare_universe.py`).
  Survivor-only, like every earlier phase.
- **Costs:** primary at the existing model (12 bps round trip + 5 bps
  slippage per fill); secondary at the statutory floor (24 bps + 5 bps).

## Primary outcome and decision rule

On the paired per-fold difference in total return (`v1_normalized` −
`v1_raw`, primary costs), the challenger is a **candidate for further
testing** only if **all** of these hold:

1. it beats `v1_raw` in **at least 10 of 16** folds;
2. the **median** paired gain is **above 0**;
3. the paired mean stays **above 0 after dropping any single fold**;
4. its own **median** excess return over NIFTY 50 is **above 0**.

Otherwise it is **rejected and V1 is unchanged**. A candidate does not go to
production: it would next need the same test on a survivorship-reduced
universe and forward confirmation in the live record.

## Reported but not decisive

- Both arms against NIFTY 50 at both cost levels: mean, median, folds beaten.
- Both arms against MOM30IETF (an ETF on Nifty200 Momentum 30) for the folds
  that lie entirely inside its listed history.
- Trades, costs paid, missed next-session entries.

## Known limits, stated in advance

- 16 folds is a small sample; the rule above requires consistency, not just
  a positive average, for that reason.
- The universe is survivor-only. A ranking change can still look better or
  worse for reasons survivorship would erase.
- The `v1_raw` arm also serves as the re-run of V1's folds on the fixed
  engine, but on this lab's data vintage, not Phase 20's.
- NIFTY 50 here is the price index, which leaves out about 1–1.5% a year of
  dividends. Both arms carry the same handicap, so the paired comparison is
  unaffected.

## Changes after registration

None yet.
