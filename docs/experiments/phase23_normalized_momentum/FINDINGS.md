# Phase 23 — NSE-style normalized momentum vs V1: findings

**Run 2026-10-30, 14:46–15:38 IST**, on a local lab built the same day: a
1,000-stock pool from Yahoo (today's most liquid NSE listings, 10 years of
history, no-trade placeholder bars dropped by the ingest), on the fixed
backtest engine (next-session execution, gap-aware stops; audit #19, #20).
Pre-registration: [PREREGISTRATION.md](PREREGISTRATION.md), committed before
the run and unchanged. Raw output: [results.json](results.json).

## Decision: REJECTED — V1 unchanged

| Pre-registered condition | Needed | Result |
|---|---|---|
| Folds where normalized beats raw | ≥ 10 of 16 | **10** ✓ |
| Median paired gain | > 0 | **+2.23pp** ✓ |
| Paired mean after dropping any one fold | > 0 | **−0.53pp** ✗ |
| Normalized's median excess over NIFTY 50 | > 0 | **+0.32pp** ✓ |

The mean paired gain is +0.34pp per 6-month fold and rests on a single fold:
remove the best one and it turns negative. At the statutory cost level it is
negative outright (mean −0.26pp, median +1.33pp, 9 of 16 folds better).

## What the normalized ranking actually does

Per fold, total return, primary costs:

| Fold | Test window | NIFTY 50 | V1 raw | Normalized | Max DD raw | Max DD normalized |
|---|---|---|---|---|---|---|
| 1 | 2018-04 → 2018-09 | +7.04 | −0.65 | +3.17 | −13.14 | −10.14 |
| 2 | 2018-09 → 2019-03 | +5.10 | −12.08 | −14.71 | −18.16 | −17.04 |
| 3 | 2019-03 → 2019-09 | −1.34 | −10.54 | +0.06 | −15.67 | −7.18 |
| 4 | 2019-09 → 2020-03 | −24.53 | −1.98 | +3.88 | −12.49 | −12.26 |
| 5 | 2020-03 → 2020-09 | +33.44 | +38.68 | +41.86 | −7.62 | −6.39 |
| 6 | 2020-09 → 2021-03 | +29.21 | +37.94 | +40.35 | −9.40 | −9.55 |
| 7 | 2021-03 → 2021-09 | +20.26 | +39.56 | +33.93 | −8.28 | −7.72 |
| 8 | 2021-09 → 2022-03 | −3.93 | +6.29 | +6.94 | −19.16 | −18.40 |
| 9 | 2022-03 → 2022-09 | +0.61 | +5.66 | +11.26 | −12.36 | −11.48 |
| 10 | 2022-09 → 2023-03 | −0.42 | −17.37 | −4.07 | −19.89 | −15.78 |
| 11 | 2023-03 → 2023-09 | +15.83 | +50.16 | +37.86 | −3.77 | −6.38 |
| 12 | 2023-09 → 2024-03 | +12.31 | +24.52 | +6.60 | −12.62 | −15.76 |
| 13 | 2024-03 → 2024-09 | +17.21 | +16.42 | +11.82 | −11.52 | −12.39 |
| 14 | 2024-09 → 2025-03 | −9.98 | −12.79 | −10.75 | −20.14 | −12.19 |
| 15 | 2025-03 → 2025-09 | +7.05 | +8.20 | +5.10 | −3.52 | −7.50 |
| 16 | 2025-09 → 2026-03 | −8.28 | −14.02 | −9.92 | −18.70 | −16.08 |

It loses less when momentum goes wrong (folds 3, 4, 10, 14, 16, with smaller
drawdowns in 3, 10 and 14) and gains less in strong momentum runs (7, 11, 12,
13). Dividing by volatility tilts toward steadier names, which is the trade
it was expected to make: less exposure to the wildest winners, in both
directions. It is a different risk profile, not a better return. A
"lower drawdown at similar return" hypothesis would be a separate test with
its own pre-registration; it is not claimed here.

Against MOM30IETF (an ETF on Nifty200 Momentum 30), only the last 3 folds
are comparable — Yahoo's history for it starts 2024-06-24 — so those numbers
(raw +1.70pp, normalized +2.71pp on average) say nothing either way.

## V1 itself, re-run on the fixed engine

The `v1_raw` arm is the frozen V1. Against NIFTY 50 over the same 16 folds:
**mean +3.65pp, median +3.10pp, 9 of 16 folds beaten**, and the mean stays
positive after dropping any single fold (+1.61 to +5.04pp). Phase 20 measured
the same strategy at mean −0.84pp, median −1.88pp, 6 of 16 — on the old
engine and an older data vintage.

**How much of that is the engine fix.** The same `v1_raw` arm was run on the
pre-fix engine against the same lab and folds
([attribution_old_engine.json](attribution_old_engine.json); the Phase 23 script
with only that arm, on `feat/realistic-forward-scoring`'s engine). Old engine:
mean −0.46pp, median −1.03pp, 8 of 16 — close to Phase 20, so the newer data is
not what moved the result. The fix raised V1's return in 13 of 16 folds, by
+4.12pp on average (median +4.45pp). Fold 1's trade logs show the mechanism:
the same 27 trades, the same exits except gaps, entries one session later at a
median 2.1% lower price, because the old engine bought at `entry_high` — half
an ATR above the close — at the close itself.

That is still not evidence of an edge:

- **The universe is survivors-only.** Each fold draws from today's 1,000 most
  liquid listings, which over-represents stocks that went on to do well — the
  stocks momentum buys. A 2026 study of Indian equities (Ranse,
  arXiv:2603.19380) measured survivor-only backtests overstating annual returns
  by 4.94 percentage points; at roughly half that per 6-month fold, it is
  about the size of the whole excess. The survivorship-free check remains
  unrun.
- **The benchmark is the NIFTY 50 price index**, which leaves out about 1–1.5%
  a year of dividends (~0.6pp per fold), all in the strategy's favour.
- **The live record fails** over the same period it was trading: from an
  obtainable entry, −2.44pp at 1 day and −1.80pp at 5; and scored as
  positions, it does no better than random picks from the same stocks
  (`docs/experiments/forward_exit_diagnosis/FINDINGS.md`).
