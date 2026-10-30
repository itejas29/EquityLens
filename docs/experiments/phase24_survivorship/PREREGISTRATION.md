# Phase 24 — survivorship: pre-registration

**Written 2026-10-30, before any universe was built or backtest run.** The
commit adding this file is the proof of timing; any later change is recorded
at the end with its reason.

## Question

On the fixed engine, frozen V1 beat NIFTY 50 by a mean +3.65pp per 6-month
fold (median +3.10pp) — but every fold drew from today's 1,000 most liquid
listings, which leaves out every company that later went bust, merged away or
faded. How much of that excess is survivorship, and does V1 still beat NIFTY 50
on a universe built the way the market actually looked at each fold start?

## Universes, per fold (Phase 20/23's 16 folds)

Both are ranked identically — the top 500 NSE `EQ` securities by mean daily
traded value over the 20 trading days up to the fold start, from NSE's own
daily bhavcopies — so membership is the only difference:

- **B, survivorship-free:** every security that traded then, whether or not it
  exists today.
- **A, survivors-only:** the same ranking restricted to securities in today's
  1,000-stock pool (the Phase 23 lab).

A security is followed across renames (same ISIN) and face-value splits (same
symbol). Prices: pool stocks and other names still listed today come from
Yahoo, split- and bonus-adjusted, as in every phase before; names no longer
listed come from NSE's bhavcopies, adjusted for bonuses and splits **from NSE's
corporate-actions list only** — never inferred from a price move, because a
genuine 50% crash looks exactly like a 1:1 bonus, and crashes are the point.

## Fixed

Frozen V1 (Phase 23's `v1_raw` parameters), the fixed engine (audit #19, #20),
existing costs (12 bps round trip + 5 bps slippage per fill), NIFTY 50 price
index as benchmark.

## Primary measurements

1. **Survivorship gap:** per fold, A's total return minus B's — mean and median.
2. **Survivorship-free result:** B's excess over NIFTY 50 — mean, median, folds
   beaten, and the mean after dropping each fold in turn.

## Decision rule

The backtest edge **survives** the survivorship correction only if B's
**median** excess over NIFTY 50 is above 0 **and** B's mean excess stays above
0 after dropping any single fold. Otherwise the Phase 23 backtest excess is
attributed, at least in part, to survivorship. Either way the result becomes
eligible evidence for the verdict's `survivorship_free_confirmation` check.

## Known limits, stated in advance

- **A lower bound.** A held position whose stock stops trading exits after 30
  days at its last traded price. For companies that went through insolvency
  shareholders often got far less, so B still flatters the strategy.
- **Sector metadata** is unavailable for names no longer listed. Each gets its
  own sector bucket in the primary run. If such names exceed 5% of any fold's
  universe B, a sensitivity run with all of them in one shared bucket is
  reported alongside.
- NSE-only: securities listed only on BSE are absent from both universes.
- Rights issues and dividends are not adjusted — consistent with the Yahoo
  closes used everywhere else.
- Bhavcopies start 2016-06-01, which covers fold 1's one-year lookback.

## Changes after registration

1. **2026-10-30, after building the universes, before any backtest ran: company
   shares only.** The first build counted every NSE `EQ`-series security, and
   NSE trades ETFs in that series too — NIFTYBEES, GOLDBEES, LIQUIDBEES,
   CPSEETF, the Bharat Bond ETFs and others appeared among the "no longer
   listed" names, with traded values high enough to take top-500 slots. Today's
   pool is built from NSE's equity list, which has no ETFs, so universe B would
   have differed from A by more than survivorship. Both universes now keep
   only securities whose ISIN starts `INE` (company equity; mutual-fund units,
   ETFs included, start `INF`). No backtest result existed when this was
   changed.
