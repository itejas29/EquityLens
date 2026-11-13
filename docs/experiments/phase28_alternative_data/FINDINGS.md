# Phase 28 — alternative (non-price) data: findings

**Run 2026-11-13** in the local lab, on Phase 24's survivorship-free universe.

- **Pre-registration:** [PREREGISTRATION.md](PREREGISTRATION.md), committed
  before any data was loaded (60a516f). It has four logged changes:
  - three were made before any return was computed;
  - one (change 4, a backtest bug fix) was made after the first run, and both
    runs are reported below.
- **Raw output:** [results.json](results.json), with every trade listed;
  [diagnostics.json](diagnostics.json); and the first run's summary,
  [results_run1_summary.json](results_run1_summary.json).

## Decision: NO SIGNAL PASSES

| Signal | Trades | Median excess vs NIFTY TR | Mean excess | Win rate | p (one-sided) | Verdict |
|---|---|---|---|---|---|---|
| S1 insider cluster buy | 306 | −4.76% | +1.81% | 38.6% | 0.106 | **FAIL** |
| S2 bulk / block buy | 1,838 | −4.58% | −0.86% | 39.5% | 0.979 | **FAIL** |
| S3 top-fund accumulation | — | — | — | — | — | **BLOCKED** |
| S4 extreme FII selling (index) | 16 | −2.50% | −1.72% | 37.5% | 0.843 | **INSUFFICIENT** |
| S5 index deletion recovery | 225 | −0.81% | +8.05% | 46.7% | 0.0012 | **FAIL** |

- **How excess is measured:** per trade, over that trade's own window
  (about 3 months, or shorter if stopped out).
- **Pass bar:** at least 30 trades, a median excess above 0, and p < 0.02.
  That is the directive's 0.10, Bonferroni-corrected for five signals.
- **S5 fails on its median** despite its p-value; the section below explains
  why its mean is not evidence.

Robustness checks and Phase D (combining with the crash brake) were
registered to run only for a passing signal, so neither ran.

**Bottom line:** none of the public non-price sources we could collect shows
stock-picking information that survives a fair test. Together with Phases 14–26,
nothing measured so far — price-based or not — beats NIFTY 50.

## Read every number against this control

The same trade rules applied to **5,000 random (stock, date) pairs** from the
same universes and period (seed 28) give:

| | Median excess | Mean excess | Win rate |
|---|---|---|---|
| Random entry | −4.34% | −0.13% | 40.0% |
| S1 insider cluster buy | −4.76% | +1.81% | 38.6% |
| S2 bulk / block buy | −4.58% | −0.86% | 39.5% |

**A random stock's median trade loses about 4.3 points to the benchmark
under these rules.** Three things cause it:

- **The stop:** a −15% stop closes 35% of random trades at a loss before they
  can recover.
- **The benchmark includes dividends:** NIFTY TR adds 1.3% a year.
- **The stock prices do not:** Yahoo and NSE prices exclude dividends, which
  costs a stock about its dividend yield over the hold.

So the medians in the decision table are a high bar, as the directive set
it. Against the control:

- **S1 and S2** sit right on it. Their trades behave like random trades.
- **S5's median** is about 3.5 points better than the control. That is not
  evidence of an edge: S5's 225 trades enter on only 17 dates, so they share
  those dates' market moves. Per review, the median is positive in 8 of 17
  reviews and negative in 9.

## Why S5's mean (+8.05%, p = 0.0012) is not an edge

| Effective date | Trades | Median excess | Mean excess |
|---|---|---|---|
| 2020-03-27 (COVID bottom) | 15 | +65.86% | +97.34% |
| 2021-03-31 | 18 | +16.72% | +24.10% |
| All other 15 reviews | 192 | between −20.29% and +8.90% | between −12.08% and +5.38% |

- **March 2020 carries the mean.** The stocks dropped from NIFTY 500 at the
  March 2020 review were bought on 27 March 2020, three days after the market
  bottomed:
  - Reliance Infrastructure, Jain Irrigation, Reliance Power, Vodafone Idea
    and Indiabulls Housing rose 100–280% in three months.
  - That is buying distressed stocks at a once-in-a-decade low, not a
    repeatable index effect.
- **Without March 2020:** 210 trades, median −2.23%, mean +1.67%.
- **Without the top 5% of trades:** median −2.81%, mean +0.95%.
- **The t-test is overconfident here.** It treats 225 trades as independent,
  but they cluster on 17 days. The registered month-clustered check would
  have tested exactly this had S5 passed its median.

## S1 and S2 in more detail

### S1 — insider cluster buy

- **Funnel:** 1,401 clusters (two or more promoters or directors buying
  ₹50 lakh or more in the open market within 5 sessions):
  - 39 before the sample;
  - 406 in stocks outside the lab's 1,428-stock pool (mostly small companies);
  - 555 outside that date's top-500 universe;
  - 95 in a stock already held;
  - 306 traded.
- **Exits:** 185 by time, 121 by stop.
- **Outliers:** the positive mean (+1.81%) comes from a few 2020 trades
  (SAMMAANCAP +176 points, AARTIDRUGS +151, AUROPHARMA +117). Without the top
  5%: median −5.82%, mean −2.48%.
- **Bad price data:** 2 of 306 trades contain a split-like price step;
  removing them changes nothing (median −4.76%, mean +1.93%).

### S2 — bulk / block buy

- **Funnel:** 96,845 client-days. 4,985 stock-days have a single client
  buying ₹10 crore or more without selling the same stock that day:
  - 1,008 in a stock already held;
  - 1,354 outside the universe;
  - 655 unmapped;
  - 56 could not be entered or finished;
  - 1,838 traded.
- **Exits:** 1,249 by time, 585 by stop, 4 delisted.
- **Without the top 5%:** median −5.61%, mean −3.46%.

### S4 — extreme FII selling

- **Data:** 104 reporting dates since April 2018 had net equity selling of
  ₹5,000 crore or more through the stock exchanges.
  - They cluster: 82 of them fall in 2024–2026, as flows have grown with the
    market.
- **Trades:** with one 60-session NIFTY position at a time, only 16 trades
  fit. That is below the 30 the directive requires for a conclusion.
- **Excess here** is each trade's net return against the mean return from
  entering NIFTY at every session (+3.09% over 2,066 sessions). The 16 trades
  came in 2.50 points below that at the median. Exploratory only.

## Data: where it came from, how much, what is missing

| Source | Origin | Coverage | Records | Gaps |
|---|---|---|---|---|
| Insider / promoter trades | NSE `api/corporates-pit` (SEBI PIT disclosures) | broadcast 2018-01-01 → 2026-05-02 | 235,050 | Nothing after 2 May 2026 (empty from NSE). Yearly volume falls from 58,542 (2018) to 13,605 (2025), mostly fewer employee and ESOP filings; promoter and director filings continue. Signals after May 2026 could not have finished a 60-session trade before the price data ends (2026-10-30), so the test window is unaffected. |
| Bulk + block deals | NSE `api/historicalOR/bulk-block-short-deals` | 2018-01-01 → 2026-10-31 | 147,255 deals | The JSON endpoint stops at 70 rows, which cut 730 bulk and 18 block days short. Every such day was re-fetched through the same endpoint's CSV download, which is not capped (70 to 1,040 rows per day). The CSV's byte-order mark first cost those files their Date column; it was restored from each single-day request's own date. Final check: every deal has a valid date, and no day is at a cap. |
| Mutual-fund holdings | AMFI → each AMC's site | — | — | **Blocked.** No consolidated source: about 45 AMCs each post monthly Excel files in their own layout, and reading Excel needs `openpyxl`, which is not in the approved stack. Testing this would take approval for `openpyxl` and one scraper per AMC. |
| FPI daily flows | NSDL FPI archive (one postback per month) | 2016-01-01 → 2026-10-30 | 2,617 reporting dates | None. NSE's own FII/DII API serves only the latest day. |
| Index exclusions | niftyindices.com press releases (PDF text read with the standard library) | Oct 2017 → Oct 2026 | 477 releases, 456 parsed, 632 exclusions | The main September 2021 review release is a scanned image (no text without OCR), so its NIFTY 50 / Next 50 exclusions are missing; its NIFTY 500 exclusions come from a separate release. 43 extracted symbols could not be confirmed against that week's bhavcopy and were dropped. |

Event to stock mapping went through each symbol's ISIN on the event date,
read from NSE's own bhavcopies, so renamed companies still match: 5,662 events
matched by ISIN, 30 by symbol, and 1,083 matched no lab stock. The
unmatched are almost all small companies outside the lab's top-1,000 pool, and
so outside the top-500 universe anyway.

## Changes made after registration (details in the pre-registration)

1. **Before any return:** an S5 parser bug, where the wrong text was taken
   as each section's heading.
2. **Before any return:** S5's periodic-review test changed from wording to
   release size (10 or more exclusions). NSE's wording made the keyword rule
   misclassify whole semi-annual reviews.
3. **A data gap:** the scanned September 2021 release.
4. **After the first run:** a bug in exits across a gap of more than 30
   days. It affected exactly two trades.

   | Run | S2 median | S2 mean | S5 median | S5 mean | Verdicts |
   |---|---|---|---|---|---|
   | Run 1 | −4.60% | −0.89% | −0.81% | +8.17% | the same |
   | Run 2 | −4.58% | −0.86% | −0.81% | +8.05% | the same |

## What this means

The directive's own conclusion applies: "no publicly available data source we
tested beats NIFTY, and that is a valid, publishable finding." The specific
lessons:

- **"Insiders are buying" is not an edge on its own here.** Promoter and
  director open-market buying, clustered and at least ₹50 lakh, produced
  trades indistinguishable from random ones.
- **Big buyers in bulk and block deals are not informed money, on average.**
  Across 1,838 trades the median and mean are both negative and match the
  random control.
- **The index-deletion bounce is not a repeatable effect in this sample.**
  It depends on one crash-bottom date.
- **A fair test of FII flows needs a different design.** S4 has too few
  independent episodes. For example, a contrarian rule scaled to market size
  rather than a fixed ₹5,000 crore, which fires far more often in 2024–2026
  simply because flows are larger. Any such rule would need its own
  pre-registration.

## Limitations

- **Dividends:** stock returns exclude them while the benchmark includes
  them. That costs each stock trade about its dividend yield over the hold
  (a few tenths of a point); the random control carries the same handicap.
- **Benchmark:** NIFTY total return is approximated as price plus 1.3% a
  year, as the directive specified.
- **Single variant:** each signal has one pre-registered form. A different
  threshold might do differently, but searching thresholds after seeing these
  results would be the overfitting the directive forbids.
