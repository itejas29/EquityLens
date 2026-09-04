# Phase 18 — universe depth (500 → 1000). Verdict: do not ship.

Question: the scored universe is capped at `MAX_UNIVERSE_SIZE = 500` and that cap
binds (2,406 catalogue symbols, exactly 500 active). Momentum is documented to be
stronger in smaller names, so do ranks 501-1000 hold alpha v1 never sees?

Answer: the aggregate says yes, the decomposition says no. **Keep the cap at 500.**

## Setup

16-fold walk-forward, 2016-10-01 → 2026-08-01, TRAIN 18mo / TEST 6mo / ROLL 6mo —
identical to Phase 16/17 so baselines stay comparable. Baseline arm is live v1 as
it stands after Phase 17 (momentum + equal weight + 4-ATR stop + regime filter +
5-day trend gate); only `universe_top_n` varies.

Ingested 1000 stocks (10y prices + sector metadata, 0 failures) on a throwaway
`m7i-flex.large`. All 1000 clear the ₹5cr/day liquidity floor — the cap binds, not
the floor, so ranks 501-1000 are not illiquid by this project's own standard.

## Aggregate (looks like a win)

| arm | ret% | Sharpe | Sortino | maxDD% | win% | mean ex | med ex | trades | beat |
|---|---|---|---|---|---|---|---|---|---|
| V1_top500 | 4.48 | 0.07 | 0.20 | -14.20 | 46.42 | -1.74 | -3.25 | 27.0 | 5/16 |
| V1_top750 | 6.32 | 0.27 | 0.45 | -13.87 | 47.76 | +0.10 | -1.89 | 27.9 | 8/16 |
| V1_top1000 | 7.46 | 0.36 | 0.60 | -14.26 | 47.20 | +1.24 | -1.09 | 28.6 | 8/16 |
| V1_top1000 @25bps | 6.92 | 0.30 | 0.53 | -14.52 | 46.73 | +0.70 | -1.36 | 28.7 | 8/16 |
| V1_top1000 @40bps | 7.08 | 0.32 | 0.63 | -14.60 | 46.37 | +0.86 | -0.96 | 28.8 | 8/16 |
| NIFTY 50 | 6.22 | | | | | | | | |

Monotonic in depth (4.48 → 6.32 → 7.46), Sharpe 0.07 → 0.36, drawdown flat, and
the edge appears to survive the cost stress (+2.98 at 12bps, +2.44 at 25bps,
+2.60 at 40bps). On this table alone you would ship it.

## Why it does not ship

**One fold is the entire result.**

| | |
|---|---|
| better in | **7 of 16 folds** (worse in 9) |
| mean delta | +2.98 pp |
| median delta | **−0.67 pp** |
| largest single fold | +38.36 pp (fold 8) |
| mean delta excluding fold 8 | **+0.62 pp** |

Fold 8 is a correction regime where top500 returned −11.40% and top1000 returned
+26.96%. Remove it and the edge collapses from +2.98 to +0.62 — inside noise for
a 16-fold sample. Mean sits above median, and the deeper universe is *worse* in
the majority of folds.

This is the exact test Phase 17 was required to pass and did: "better in 13 of 16
folds with median improvement (+3.17) above the mean (+2.11), so it is not one
fold carrying the result." Phase 18 shows the mirror image and fails it.

**The cost stress is not measuring what it claims.** 40bps (+2.60) beat 25bps
(+2.44) on the same universe and same signals. Higher costs cannot mechanically
raise returns; they only shift which trades clear thresholds, changing the equity
path. That inversion means fold variance swamps the cost effect, so "survives
40bps" is far weaker evidence than it reads.

## Separate finding, arguably more important

`V1_top500` — the **current live configuration** — returned 4.48% mean OOS against
NIFTY's 6.22%, beating the index in only 5 of 16 folds, with median excess −3.25pp.
On this harness the live strategy underperforms its benchmark. That is a bigger
question than universe depth and is not addressed by this experiment.

Caveat on reading that too harshly: the backtest scores technicals + risk only
(fundamentals are excluded to avoid look-ahead, see `backtest_config.py`), so it
does not measure the full live recommendation stack.

## Reproduce

```
python scripts/phase18_prepare_universe.py    # non-production DB only; hard-refuses prod
python scripts/phase18_universe_size.py       # refuses below 1000 active stocks
```

Raw per-fold metrics: `results.json`.

## Note on the first run

An earlier run returned all depth arms byte-identical across all 16 folds.
`compute_point_in_time_scores` caches on `as_of` alone with no reference to which
stocks were in scope, and the script shared one cache across arms — so the first
arm to reach each rebalance date populated it and the rest reused its universe.
The cost arms differed (costs apply at execution, downstream of scoring), and that
asymmetry was the tell. Fixed to one cache per depth; a degeneracy check now
flags identical-across-depths output rather than letting it read as a real result.
