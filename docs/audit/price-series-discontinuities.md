# Split-shaped discontinuities in the stored price series

Measured 2026-09-10 against the production database (read-only scan for
single-day close ratios near a common split ratio).

```
single-day moves beyond -30% / +45%: 32
of which split-shaped (within 2% of a common ratio): 9

  CDSL         2017-06-30    261.75  ->  2017-07-03    131.07   x1.9970  ~2:1
  CGCL         2023-12-29    769.65  ->  2024-01-01    193.73   x3.9728  ~4:1
  MOTILALOFS   2023-12-29   1240.85  ->  2024-01-01    315.01   x3.9391  ~4:1
  PARAS        2024-12-31   1008.20  ->  2025-01-01    502.90   x2.0048  ~2:1
  SAMMAANCAP   2019-09-27    347.73  ->  2019-09-30    228.11   x1.5244  ~3:2
  SAMMAANCAP   2020-03-18    120.74  ->  2020-03-19     80.00   x1.5093  ~3:2
  TDPOWERSYS   2026-08-20   1507.50  ->  2026-08-21    767.40   x1.9644  ~2:1
  TRENT        2025-12-31   4279.00  ->  2026-01-01   2864.93   x1.4936  ~3:2
  ZEEL         2024-01-19    235.00  ->  2024-01-23    155.95   x1.5069  ~3:2
```

These are two different defects that look identical in the data.

## A. Stale stored history — our bug

`TDPOWERSYS`. Our stored close for 2026-08-20 is **1507.50**. Yahoo, asked
today, returns **753.75** for that same date. The provider restated the series
when the split went ex; `incremental_price_update` only ever fetched bars
*after* the last stored date, so the pre-event half of our series was never
revisited and stayed on the old basis.

Fixed: the incremental window now reaches back `RESTATEMENT_OVERLAP_DAYS` so it
re-returns bars already on disk, compares them, and re-pulls the full history
for any symbol whose past has moved. Costs no extra requests. See
`_detect_restatement` in `services/incremental.py`.

TDPOWERSYS itself still needed a one-off re-pull — the fix prevents
recurrence, it does not repair history already stored. Done via
`scripts/repair_price_history.py --symbols TDPOWERSYS`, at `period="max"`
rather than `HISTORY_PERIOD`: a 10y pull starts 2016-09-12 while our stored
series starts 2016-08-16, which would have left 18 bars on the old basis and
moved the seam instead of removing it.

**Re-examining these nine turned up a separate, larger defect.** CGCL and PARAS
are listed below as defect B, and they are — but each *also* disagreed with the
provider on exactly one further date, 2026-08-25, as did TDPOWERSYS. One shared
date across three unrelated companies is one bad ingest, not three corporate
actions: 487 of 500 stored closes for that date were in-progress session
snapshots written as closes. See
[unsettled-session-bars.md](unsettled-session-bars.md).

## B. Yahoo adjusts only from the start of the split's calendar year — their bug

`MOTILALOFS`, `PARAS`, `TRENT`, `CGCL`. These are **not** stale. Yahoo serves
the discontinuity itself, right now, identically across `period="2y"`,
`period="5y"`, a narrow `start`/`end` window, and `yf.download` — so a full
re-pull reproduces it exactly.

The pattern is that the split adjustment is applied only from 1 January of the
year the split occurred, not from the split date:

| symbol | actual split (yfinance `.splits`) | where the jump appears |
|---|---|---|
| MOTILALOFS | 2024-06-10, 4:1 | 2024-01-01 |
| PARAS | 2025-07-04, 2:1 | 2025-01-01 |
| TRENT | 2026-06-04, 1.5:1 | 2026-01-01 |

Verified for PARAS: `2024-12-31 close=1008.20`, `2025-01-01 close=502.90`, with
a normal-looking volume of 91,090 on the second bar. Nothing about the row
looks synthetic; the level simply shifts.

### Why this matters here

V1 ranks on 12-month momentum and sizes stops on ATR. A level shift inside the
lookback window corrupts both:

- **Momentum.** A downward shift reads as a large negative return for a year,
  so the stock ranks at the bottom and is never bought — the safe direction. A
  **consolidation (reverse split) shifts the level UP**, reads as a large
  positive return, and puts the stock at the top of the ranking, bought on a
  move that never happened. None of the nine above are consolidations, but
  nothing in the pipeline distinguishes the two cases.
- **ATR, volatility, beta, max drawdown.** All computed across the jump. A
  4-ATR stop derived from a series containing a 50% single-day move is wide
  enough to be no stop at all.

Currently in the 12-month window as of 2026-09-10: **TDPOWERSYS** (defect A,
2026-08-21) and **TRENT** (defect B, 2026-01-01).

### Resolution: excluded, not back-adjusted

Defect B cannot be repaired by re-fetching, because the provider's own series
is what carries it. Two options: back-adjust our stored history by the known
split ratio, or exclude the affected symbol from ranking until the
discontinuity ages out of the lookback.

**Excluded.** Back-adjusting means writing computed prices into
`price_history`, and rule 5 is that missing data stays NULL rather than being
filled with derived values. Exclusion is also what this pipeline already does
with an unusable series — `daily_signals` is GATED, "a stock with no usable
price series is dropped rather than shown with a caveat" — so this applies the
existing policy to a case it did not previously cover, rather than inventing a
new one.

The gate lives in `_momentum_scores`, which is the single chokepoint: published
signals, momentum leaders, the screener and `market_ext` all call it. It reads
the closes already in memory, so it costs one pass and no queries.

The discriminator is the RATIO, not the size of the move — see
`services/price_integrity.py`. Real stocks fall 30% in a day; they do not fall
by a factor of 2.0000.

**Measured impact on production, 2026-09-09 (`as_of` = latest stored session):**

```
active stocks         : 500
too little history    : 33     (pre-existing behaviour, unchanged)
scored (unchanged)    : 465
EXCLUDED by the gate  : 2
    TDPOWERSYS (x1.9644 ~2:1    1507.50 -> 767.40)
    TRENT      (x1.4936 ~3:2    4279.00 -> 2864.93)
```

Exactly the two symbols whose discontinuity falls inside the current 12-month
window. No collateral exclusions.

TDPOWERSYS is defect A and will repair itself once the restatement detector
ships and its full history is re-pulled, at which point it re-enters the
ranking. TRENT is defect B and stays out until 2027-01-01, when the step ages
past the momentum lookback.


## Effect on the research programme (Phases 17-20)

`daily_signals._momentum_scores` documents that its construction is *identical*
to `backtest_scoring` "so a signal published in production matches what the
backtest would have selected". Gating only the live path would have broken
exactly that property, so both now call `find_discontinuity`, and
`test_live_and_backtest_apply_the_same_gate` asserts they continue to.

The backtests already run on this same stored data, so every phase so far was
computed with these nine discontinuities present. The effect is bounded and
measurable:

- **All nine steps are downward** (`prev_close > close` in every row above).
- A downward step makes 12-month momentum read large-negative for about a year,
  which puts the stock at the *bottom* of a percentile ranking that selects
  from the top.
- So no phase result ever contains a position opened because of one of these.
  The effect is that roughly eight stock-years were effectively absent from a
  ~500-stock universe — under 0.2% of the available stock-years across the
  walk-forward span, in the direction of a slightly smaller candidate pool.

Adding the gate does not change a published conclusion. It is worth stating
plainly that it *could* have: had any of the nine been a consolidation, the
step would have been upward, the stock would have ranked at the top, and the
backtest would have bought it on a move that never happened.
