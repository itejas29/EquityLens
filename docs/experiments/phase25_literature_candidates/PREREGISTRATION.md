# Phase 25 — rankings from the literature: pre-registration

**Written 2026-10-30, before any of the code below existed or any run.** The
commit adding this file is the proof of timing; any later change is recorded
at the end with its reason.

## Question

Phase 24 found that V1 (12-month-minus-1-month momentum, 10 stocks) has no edge
over NIFTY 50 on a survivorship-free universe: median −0.03pp per 6-month fold.
Does a ranking with published evidence behind it do better on the same bench,
and if it beats NIFTY 50, is that stock selection or just owning the universe?

## Bench (fixed, from Phase 24)

- Universe B per fold, unchanged: `docs/experiments/phase24_survivorship/universes.json`
  (top 500 NSE company shares by traded value before each fold, delisted names
  included), and the same 16 folds.
- The fixed engine (audit #19, #20), existing costs (12 bps round trip + 5 bps
  slippage per fill), NIFTY 50 price index as the benchmark.
- V1's engine for every arm: regime filter (200-day average, 25% exposure in a
  bear regime), 4 ATR stop, 5-day trend gate on entries, equal weight, monthly
  rebalance, 90-day horizon.

**30 positions for every arm** (`max_stocks=30`, at most 6 per sector — V1's
20% sector cap). In Phase 24, V1's 10-stock excess over NIFTY varied by 13.8pp
(standard deviation) from one 6-month fold to the next, which is too much
noise for 16 folds to tell an edge from luck. Thirty stocks cut the
stock-specific part of that noise and match NSE's own momentum index. Holding
more stocks does not change what any ranking is expected to earn.

## Arms — only the ranking differs

- **M0 — V1's ranking at 30 stocks:** 12-month return minus 1-month return.
  This isolates what diversification alone does.
- **M1 — residual momentum** (Blitz, Huij & Martens 2011, *Journal of Empirical
  Finance*): daily log returns over the last 252 sessions; β from an OLS
  regression on NIFTY 50 (with intercept); residual e = r − β·r_NIFTY, without
  subtracting the intercept, so a stock's own drift stays in. Score = sum of e
  over the window excluding the last 21 sessions, divided by its standard
  deviation over the same sessions. The paper uses 36 monthly returns; one year
  of daily returns is used because the NSE files start in June 2016.
- **M2 — nearness to the 52-week high** (George & Hwang 2004, *Journal of
  Finance*; for Indian stocks, Raju & Rajan, SSRN 4587697, 2004–2023): close ÷
  highest close of the last 252 sessions.
- **M3 — momentum plus low volatility** (the construction of NSE's NIFTY Alpha
  Low-Volatility 30, with V1's momentum as the return leg): the average of the
  momentum percentile and the low-volatility percentile (standard deviation of
  daily log returns over 252 sessions; lower ranks higher).
- **M4 — machine-learning ranker** (gradient-boosted trees, one of the two
  model families Gu, Kelly & Xiu 2020, *Review of Financial Studies*, found
  best for cross-sectional returns; the other, neural networks, is out of this
  project's scope): scikit-learn
  `HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05,
  max_leaf_nodes=15, min_samples_leaf=200, l2_regularization=1.0,
  random_state=20261030)`. These settings are fixed now and never tuned.
  - **Features** (13, each turned into a percentile across that date's
    universe): 12-1 momentum; 1-month return; 6-month return; M1's residual
    momentum; M2's 52-week-high nearness; 1-year volatility; 3-month volatility;
    1-year beta; largest single-day return in the last month; Amihud
    illiquidity (mean |return| ÷ traded value, 63 sessions); log mean traded
    value (63 sessions); traded-value trend (21-session mean ÷ 252-session
    mean); distance from the 200-day average.
  - **Target:** the stock's percentile, within that date's universe, of its
    return over the next 21 sessions. That makes it a stock-selection question,
    not a market forecast (the old model's target, beating NIFTY over 20 days,
    was mostly a market forecast). A stock that stops trading inside the 21
    sessions is scored at its last close, as the engine exits it.
  - **Training rows:** every month-end from 2017-06-30, with its label window
    ending before the fold starts. Each month-end uses the universe B of the
    latest fold starting on or before it; month-ends before fold 1 use fold 1's.
    The model is trained once at each fold start on everything before it, and
    used for every rebalance inside that fold.

## Decision rule

The rule makes one choice on the first ten folds and checks it on the last six.

1. **Selection — folds 1–10 (test windows 2018-04 → 2023-03).** Among M0–M4,
   the arm with the highest mean excess over NIFTY 50, among those whose median
   excess is above 0 **and** whose mean stays above 0 after dropping any single
   fold, is selected. If no arm qualifies, Phase 25 has no candidate, and the
   later folds are reported for information only.
2. **Hold-out — folds 11–16 (2023-03 → 2026-03).** The selected arm passes if,
   on these six folds:
   - its mean **and** median excess over NIFTY 50 are above 0; **and**
   - its mean excess over an equal-weight basket of the same universe is above
     0. This last condition separates stock selection from simply owning the
     universe's mid-caps.
3. **Full period.** It must also pass Phase 24's rule on all 16 folds: median
   excess over NIFTY 50 above 0, and mean above 0 after dropping any one fold.

An arm that passes all three is a **V2 candidate**: eligible to be paper-traded
alongside V1 in the forward record. A backtest is never enough on its own.
Nothing in production changes from this phase.

**Equal-weight basket:** per fold, every stock in that fold's universe B,
bought at the close of the first test session and held to the last, at no
cost. A name that stops trading is held at its last close.

## Reported alongside (not part of the decision)

All five arms on all 16 folds, the basket, V1 at 10 stocks (from Phase 24),
trade counts, missed entries, costs paid, and maximum drawdown.

## Known limits, stated in advance

- **No significance test.** With fold-to-fold noise of this size, 16 folds
  cannot prove a modest edge. The rule above is a screen. The forward record is
  the test.
- **Hindsight in the choice of M3.** It is modelled on an NSE index whose
  reported outperformance (to 2022) overlaps folds 1–10. The hold-out folds
  start in 2023-03, after those reports were published.
- **ML training data:** at most about nine years, and much less for the early
  folds. Month-ends before 2018-04 use fold 1's universe, which was ranked in
  March 2018, so a name that died between mid-2017 and March 2018 is missing
  from those training rows only.
- **Same as Phase 24:**
  - a delisted name exits at its last traded price, a lower bound on the loss;
  - NSE only;
  - the benchmark is NIFTY 50 without dividends (over 1% a year), which
    flatters every arm equally;
  - tax is not modelled.

## Changes after registration

None yet.
