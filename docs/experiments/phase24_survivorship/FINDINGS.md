# Phase 24 — survivorship: findings

**Run 2026-10-30, 18:20–19:16 IST**, in the local lab, on the fixed engine.
Pre-registration: [PREREGISTRATION.md](PREREGISTRATION.md) — rules committed
before any universe was built; one change logged there (company shares only),
made before any backtest ran. Universes: [universes.json](universes.json).
Raw output: [results.json](results.json).

## Decision: the backtest edge DOES NOT SURVIVE

| Universe | Mean excess over NIFTY 50 | Median | Folds beaten | Mean after dropping any one fold |
|---|---|---|---|---|
| A — survivors-only (today's pool) | +3.04pp | +2.40pp | 10 of 16 | ≥ +0.96pp |
| **B — survivorship-free** | **+0.04pp** | **−0.03pp** | **8 of 16** | **≥ −1.61pp** |

The pre-registered rule required B's median excess above 0 and its mean
positive after dropping any single fold. Both fail. **Survivorship accounts for
the whole of V1's backtest excess:** survivors-only beat survivorship-free by
+3.00pp per 6-month fold on average (median +2.56pp, 9 of 16 folds) — roughly
6 percentage points a year, the same order as the 4.94pp a year a 2026 study of
Indian equities measured for survivor-only backtests (Ranse, arXiv:2603.19380).

The shared-sector sensitivity (pre-registered, triggered because no-longer-
listed names reached 10% of fold 1's universe) gives identical figures: how
those names are bucketed by sector does not matter.

## What universe B looked like

Each fold's B is the top 500 NSE company shares (ISIN `INE…`) by mean traded
value over the 20 sessions before the fold, from NSE's daily bhavcopies — 2,510
trading days, 3,974,703 rows, 2,984 symbols. Of fold 1's 500 (April 2018), 136
were missing from today's pool: 86 still listed but no longer among today's
1,000 most traded, and 50 no longer listed at all. By fold 16 (September 2025)
only 42 were missing, 2 of them delisted. 323 still-listed names were loaded
from Yahoo; 71 delisted ones from NSE's own prices, 6 of them adjusted for
bonuses or splits from NSE's corporate-actions list.

| Fold | Test window | NIFTY 50 | A survivors | B survivorship-free | A − B | B names from NSE files |
|---|---|---|---|---|---|---|
| 1 | 2018-04 → 2018-09 | +7.04 | −0.65 | −17.87 | +17.22 | 50 |
| 2 | 2018-09 → 2019-03 | +5.10 | −14.94 | −18.38 | +3.44 | 50 |
| 3 | 2019-03 → 2019-09 | −1.34 | −10.54 | −6.70 | −3.84 | 39 |
| 4 | 2019-09 → 2020-03 | −24.53 | −1.98 | +0.22 | −2.20 | 31 |
| 5 | 2020-03 → 2020-09 | +33.44 | +36.34 | +39.90 | −3.56 | 30 |
| 6 | 2020-09 → 2021-03 | +29.21 | +31.10 | +36.82 | −5.72 | 24 |
| 7 | 2021-03 → 2021-09 | +20.26 | +39.56 | +36.61 | +2.95 | 23 |
| 8 | 2021-09 → 2022-03 | −3.93 | +5.87 | −7.76 | +13.63 | 17 |
| 9 | 2022-03 → 2022-09 | +0.61 | +9.06 | +6.89 | +2.17 | 20 |
| 10 | 2022-09 → 2023-03 | −0.42 | −15.48 | −18.48 | +3.00 | 19 |
| 11 | 2023-03 → 2023-09 | +15.83 | +50.16 | +30.93 | +19.23 | 12 |
| 12 | 2023-09 → 2024-03 | +12.31 | +22.72 | +18.72 | +4.00 | 6 |
| 13 | 2024-03 → 2024-09 | +17.21 | +21.27 | +15.25 | +6.02 | 8 |
| 14 | 2024-09 → 2025-03 | −9.98 | −14.83 | −11.55 | −3.28 | 5 |
| 15 | 2025-03 → 2025-09 | +7.05 | +7.70 | +8.56 | −0.86 | 3 |
| 16 | 2025-09 → 2026-03 | −8.28 | −17.07 | −12.91 | −4.16 | 2 |

The damage is not mostly from bankruptcies inside the portfolio: across all 16
folds, B made only 14 trades in names that later left the market and 2 exits
on a delisting. It is membership. A survivors-only universe quietly removes the
stocks that ranked high on momentum then and went on to fade, and momentum is
exactly the strategy that buys them.

## Limits

- **Still a lower bound.** A position in a stock that stops trading exits at
  its last traded price; holders of failed companies often got less. With 2
  such exits here this barely matters.
- Both universes are ranked by NSE's traded value, so A here (+3.04pp mean)
  differs from Phase 23's `v1_raw` (+3.65pp), which ranked by Yahoo's close ×
  volume. The comparison that answers the question is A against B.
- NSE only; BSE-only companies are absent from both. NIFTY 50 is the price
  index (no dividends), which favours the strategy in both arms equally.
- A security is followed across renames (same ISIN) and face-value splits
  (same symbol). A company whose ISIN and symbol changed together in a scheme
  of arrangement appears as two securities; a position in the old one exits at
  its last price.

## What this means for V1

With the execution defects fixed (#19, #20) and survivorship measured, the
backtest shows **no edge over NIFTY 50**: a median of −0.03pp per 6-month fold.
That matches the forward record, where the picks did no better than an
equal-weight basket of the same universe. The strategy, as specified, does not
have an established edge; the verdict's `survivorship_free_confirmation` check
now has eligible evidence, and it fails.
