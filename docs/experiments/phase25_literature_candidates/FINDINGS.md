# Phase 25 — rankings from the literature: findings

**Run 2026-10-30 23:51 → 2026-10-31 00:48 IST**, in the local lab, on the fixed
engine and Phase 24's survivorship-free universe B.
- **Pre-registration:** [PREREGISTRATION.md](PREREGISTRATION.md), committed before
  any code existed. Three changes are logged there, all made before any run:
  - early stopping switched off for the ML model;
  - the basket's corporate-action rule;
  - ML training starting 2017-11-30.
- **Raw output:** [results.json](results.json).

## Decision: NO CANDIDATE — none of the five rankings qualifies

| Arm | Folds 1–10 vs NIFTY: mean / median / leave-one-out min | All 16 vs NIFTY: mean / median / folds beaten | All 16 vs basket: mean |
|---|---|---|---|
| M0 — V1's momentum, 30 stocks | −2.32 / −5.19 / −5.76 | +0.40 / −0.63 / 8 of 16 | −1.44 |
| M1 — residual momentum | −0.48 / −3.77 / −3.71 | +0.02 / −1.14 / 5 of 16 | −1.82 |
| M2 — nearness to 52-week high | −0.39 / −3.98 / −3.84 | −1.12 / −1.86 / 5 of 16 | −2.96 |
| M3 — momentum + low volatility | −1.03 / −2.45 / −4.33 | −0.42 / −1.20 / 7 of 16 | −2.26 |
| M4 — ML ranker (gradient-boosted trees) | −2.01 / −3.26 / −5.54 | −1.62 / −2.05 / 5 of 16 | −3.46 |
| *Reference: V1, 10 stocks (Phase 24)* | | *+0.04 / −0.03 / 8 of 16* | |

All figures are percentage points per 6-month fold.

**Why nothing was selected.** The rule selects only an arm whose median excess
over NIFTY 50 on folds 1–10 is above 0 and whose mean stays above 0 after
dropping any one fold. Every arm's median there was negative, from −2.45pp to
−5.19pp. Nothing qualified, so nothing was selected, and the hold-out folds
(11–16) had no candidate to judge. Over all 16 folds, no arm's median excess is
positive, and none beat NIFTY in more than half the folds.

**The ML ranker did worst.** Mean −1.62pp, median −2.05pp, 5 of 16 folds. Its
training set grew from 1,396 rows (fold 1) to 44,136 (fold 16). It did no
better in the later folds, where it had the most data: on folds 11–16 it was
−0.96pp a fold against NIFTY. This matches what Avramov, Cheng & Metzker
(2023) report for US stocks: machine-learning stock-picking profits shrink
sharply once microcaps and distressed firms are excluded and trading costs are
counted. This universe is NSE's 500 most-traded companies.

## Every arm trailed simply owning the universe

An equal-weight basket of each fold's 500 stocks, held at no cost, beat NIFTY 50:
mean +1.84pp, median +2.28pp, 9 of 16 folds. Every arm fell short of the basket,
by 1.44–3.46pp per fold. Split by market direction:

| | The 10 folds where NIFTY rose | The 6 folds where NIFTY fell |
|---|---|---|
| Arms vs the basket, mean per fold | −6.3pp to −10.9pp | +6.6pp to +11.5pp |

**That pattern is V1's risk layer, not its stock selection.** Every arm shares it:
- the regime filter cut exposure to 25% on 405 bear-regime days across the folds;
- capital deployed averaged about 67%.

The layer gives up upside in rallies and saves money in falls. Over these eight
years, for the better arms, the two roughly cancelled against NIFTY.

**Chained over the 16 folds** (each fold restarting from cash):

| | Total return | A year |
|---|---|---|
| NIFTY 50 | +125.3% | 10.7% |
| Basket | +132.4% | 11.1% |
| M3 | +128.7% | |
| M0 | +122.7% | |
| M1 | +120.1% | |
| M2 | +103.4% | |
| M4 | +82.2% | |

NIFTY here excludes dividends (roughly 1–1.5% a year), and so does every arm. The
basket's lead over NIFTY is the equal-weight, mid-cap exposure of 2018–2026,
and it came with deeper falls:

| Fold | Basket | NIFTY |
|---|---|---|
| 1 | −16.4% | +7.0% |
| 4 | −31.3% | −24.5% |
| 14 | −18.7% | −10.0% |

It was the control, not a candidate.

## What the risk layer does, measured

- **Worst fold drawdown:** NIFTY −38.4% (fold 4, COVID); the arms −15.8% to
  −19.9%. In fold 4 itself the arms fell only 6.1–8.6%.
- **Annualised volatility, mean per fold:** NIFTY 15.8%; the arms 9.9% (M3) to
  15.5% (M0). Part of the lower volatility is simply being about two-thirds
  invested.

These are reported alongside the decision, not part of it. Whether NIFTY alone,
under the same 200-day rule, gives the same protection was not tested here.

## Per fold (total return %, 6-month test window)

| Fold | Test window | NIFTY 50 | Basket | M0 | M1 | M2 | M3 | M4 | ML training rows |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 2018-04 → 2018-09 | +7.04 | −16.36 | −15.12 | −8.00 | +1.00 | +3.44 | −12.32 | 1,396 |
| 2 | 2018-09 → 2019-03 | +5.10 | +4.08 | −11.14 | −8.64 | −5.37 | −5.34 | −7.20 | 4,224 |
| 3 | 2019-03 → 2019-09 | −1.34 | −13.53 | −12.76 | −7.31 | −9.78 | −1.26 | −6.77 | 7,109 |
| 4 | 2019-09 → 2020-03 | −24.53 | −31.26 | +4.08 | +4.13 | +6.14 | +4.21 | +5.20 | 10,009 |
| 5 | 2020-03 → 2020-09 | +33.44 | +55.83 | +26.79 | +24.45 | +18.79 | +8.26 | +17.52 | 12,901 |
| 6 | 2020-09 → 2021-03 | +29.21 | +34.81 | +25.49 | +27.65 | +22.12 | +22.40 | +28.12 | 15,778 |
| 7 | 2021-03 → 2021-09 | +20.26 | +32.21 | +35.06 | +38.17 | +22.77 | +30.90 | +26.51 | 18,689 |
| 8 | 2021-09 → 2022-03 | −3.93 | −3.75 | −1.70 | −3.39 | −5.85 | −5.23 | −4.34 | 21,527 |
| 9 | 2022-03 → 2022-09 | +0.61 | +4.99 | +5.33 | +7.77 | +12.64 | +5.16 | +6.88 | 24,305 |
| 10 | 2022-09 → 2023-03 | −0.42 | −4.03 | −13.80 | −14.15 | −0.93 | −7.37 | −8.31 | 27,106 |
| 11 | 2023-03 → 2023-09 | +15.83 | +40.88 | +38.59 | +30.68 | +14.76 | +14.72 | +17.31 | 29,977 |
| 12 | 2023-09 → 2024-03 | +12.31 | +20.26 | +12.34 | +11.60 | +9.55 | +15.42 | +8.21 | 32,881 |
| 13 | 2024-03 → 2024-09 | +17.21 | +24.86 | +24.15 | +12.28 | +4.15 | +9.22 | +14.83 | 35,746 |
| 14 | 2024-09 → 2025-03 | −9.98 | −18.72 | −12.00 | −13.15 | −11.79 | −5.68 | −13.05 | 38,542 |
| 15 | 2025-03 → 2025-09 | +7.05 | +12.81 | +10.20 | +6.55 | +9.31 | +5.07 | +5.34 | 41,350 |
| 16 | 2025-09 → 2026-03 | −8.28 | −14.05 | −9.56 | −8.73 | −5.92 | −1.08 | −4.26 | 44,136 |

## Execution (summed over the 16 folds)

| Arm | Trades | Missed entries | Costs paid (₹, on ₹5 lakh per fold) | Capital deployed, mean % |
|---|---|---|---|---|
| M0 | 1,194 | 21 | 20,555 | 67.6 |
| M1 | 1,193 | 12 | 20,304 | 67.0 |
| M2 | 1,197 | 22 | 20,085 | 67.2 |
| M3 | 1,178 | 12 | 19,364 | 66.5 |
| M4 | 1,172 | 7 | 19,643 | 68.3 |

**Basket:**
- 498–500 names per fold;
- 9 name-folds left out in total for a corporate-action step inside the window
  (pre-registration change 2);
- none lacked data before its window.

## Limits

- **Comparing with the basket mixes two things:** stock selection, and V1's
  exposure rules (regime filter, entry gate, stops). The arms share those
  rules, so comparing arms with each other isolates the ranking. Comparing them
  with the basket does not.
- **Only the registered ML model was tested:** 13 price and volume features,
  fixed settings, never tuned. Other models, other features, or point-in-time
  fundamentals (not available here) could behave differently. Testing them
  would be a new phase with its own registration, not a re-run of this one.
- **Same limits as Phase 24:**
  - a delisted name exits at its last traded price;
  - NSE only;
  - the benchmark excludes dividends;
  - tax is not modelled. Monthly trading would pay short-term rates; buying and
    holding an index fund defers tax.
- **Sixteen folds cannot prove a small edge either way.** They can show, as
  here, that none of these rankings produces one large enough to see.

## What this means

On a survivorship-free universe of NSE's 500 most-traded companies, 2018–2026,
after costs, none of the six rankings tested beats NIFTY 50:
- V1's ranking, at 10 stocks (Phase 24) and at 30 (here);
- the four alternatives tested here: residual momentum, nearness to the 52-week
  high, momentum plus low volatility, and a gradient-boosted ML ranker.

None selects stocks better than owning the universe equally. The forward record
says the same of V1's live picks. With public daily prices as the only input,
these methods show no stock-selection edge in this market and period. The one
measured effect is V1's risk layer, which trades rally gains for smaller
crashes and leaves the total about level with NIFTY.
