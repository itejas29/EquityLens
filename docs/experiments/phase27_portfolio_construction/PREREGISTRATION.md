# Phase 27 — portfolio construction for the AI paper account: pre-registration

**Written 2026-11-13, before the script existed or any run.** The commit adding
this file is the proof of timing; any later change is recorded at the end with
its reason.

## Question

The user is restarting the AI paper account with ₹15 lakh and wants it to keep
its money working and spread across more stocks. Live, the account holds at
most 8 names and enters only at the monthly rebalance, so cash freed by a stop
or a target waits up to a month. Two changes are proposed:

1. more positions: 12 or 15 instead of 8;
2. refilling weekly instead of monthly.

Does either make the account better or worse on the survivorship-free bench?

This is a choice of configuration for a paper account, **not a search for an
edge**. Phases 24 and 25 found none: V1 at 10 stocks has a median of −0.03pp
per fold over NIFTY 50, and V1's ranking at 30 stocks (Phase 25, arm M0) did
not qualify either. Phase 20 found that trading faster made everything worse,
but every faster arm there also shortened the holding period (90 days down to
20, 10, 5 or 1) and ran on the engine before audit fixes #19 and #20.
Refilling weekly while keeping the 90-day horizon has not been measured.

## Bench (fixed, from Phases 24 and 25)

- Universe B per fold, unchanged:
  `docs/experiments/phase24_survivorship/universes.json` (the top 500 NSE
  company shares by traded value before each fold, delisted names included),
  and the same 16 folds of 6 months, 2018-04 → 2026-03.
- The fixed engine, existing costs (12 bps round trip plus 5 bps slippage per
  fill), and the NIFTY 50 price index as benchmark, downloaded once and shared
  by every arm and fold.
- V1's engine in every arm: momentum ranking (12 months minus 1 month), the
  regime filter (200-day average, 25% exposure in a bear regime), 4 ATR stop,
  5-day trend gate on entries, equal weight, 90-day horizon, no cash buffer.

## Arms — only positions, sector cap and refill frequency differ

| Arm | Positions | Max per sector | Rebalance |
|---|---|---|---|
| **N8_monthly** (reference: the live account) | 8 | 2 | monthly |
| N8_weekly | 8 | 2 | weekly |
| N12_monthly | 12 | 3 | monthly |
| N12_weekly | 12 | 3 | weekly |
| N15_monthly | 15 | 3 | monthly |
| N15_weekly | 15 | 3 | weekly |

- **Sector cap:** 20% of positions, rounded up (V1's own cap is 2 of 10).
- **"Weekly":** the engine's weekly rebalance, on the first trading day of each
  ISO week. Both the regime trim and new entries happen on rebalance days, as
  they do monthly.
- **Every session, in every arm:** stops, targets and the 90-day horizon.

## Measures per fold

- total return;
- excess over NIFTY 50;
- maximum drawdown;
- average share of capital deployed;
- number of trades;
- costs paid.

## Decision rule

For each arm, the paired per-fold difference against the reference:
d_k = return(arm) − return(N8_monthly), over all 16 folds.

- **Better:** all of the following hold:
  - the median of d is above 0;
  - the mean of d is above 0;
  - the mean of d stays above 0 after dropping any one fold;
  - the arm's mean maximum drawdown is no more than 2.0 points deeper than
    the reference's.
- **No measured cost:** the arm is not Better, but all of the following hold:
  - the median of d is at least −0.5pp;
  - the mean of d is at least −0.5pp;
  - the drawdown condition above still holds.
- **Worse:** anything else.

The outcome:

- **If any arm is Better:** the AI account adopts the Better arm with the
  highest median excess over NIFTY.
- **Otherwise, if any arm is No measured cost:** the account may adopt the one
  with the most positions, preferring weekly to monthly between equals. That is
  the user's stated preference (more spread, more money at work). It is
  described as "no measured cost", never as "better".
- **Otherwise:** the account stays at 8 positions, monthly.

No outcome here is evidence of an edge over NIFTY. The report gives each arm's
excess over NIFTY next to the comparison. Choosing among five arms on the same
16 folds favours whichever got lucky, which is why the bar includes the
drop-one-fold check.

## What adoption would mean (stated before the result)

The public shortlist stays at 8 names. An adopted arm with 12 or 15 positions
needs the AI account to draw on a ranked candidate list that long. That is an
implementation change, made only if such an arm is adopted.

## Execution

The folds are split across worker processes. Within a fold, every arm shares
one indicator cache. That is valid because all arms in a fold use the same
universe, which is the condition audit #18 requires. The results are identical
to a serial run.

## Changes after registration

(none)
