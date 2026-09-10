# Phase 20 — does trading faster book more profit?

Completed 2026-09-10 07:02 IST. 16 walk-forward folds, neutral `pit_top500`
universe, identical V1 params in every arm. Only the holding period and
rebalance frequency differ.

## The question

> "we have to catch todays momemtum stock to catch it and buy and sell if the
> ai think its good time to see like intraday so daily profits can be booked"

Seven arms, from the live configuration (90-day horizon, monthly rebalance)
down to a 1-day horizon rebalanced daily.

## Result

```
  arm                     ret%  Sharpe   maxDD%  trades      costs  vs NIFTY    beat
  h90_monthly_LIVE        5.38     0.1   -14.65   26.94     1352.9     -0.84    6/16
  h30_monthly             3.64   -0.02    -14.9   46.69    2566.17     -2.58    5/16
  h20_weekly             -9.19   -1.47   -22.55   88.69    4514.44    -15.41    3/16
  h10_weekly            -16.73   -2.35   -26.14  121.56    6200.33    -22.95    1/16
  h5_weekly             -32.49   -3.81   -36.51  226.75   10768.82    -38.71    0/16
  h5_daily              -34.39    -4.2   -37.69  263.88    11248.0    -40.61    0/16
  h1_daily              -87.61  -20.68    -87.6 1058.38   23761.15    -93.83    0/16
  NIFTY50                 6.22
```

**Every step faster is worse, on every measure, without a single exception.**
Return falls monotonically. Sharpe falls monotonically. Maximum drawdown
deepens monotonically. The number of folds beating NIFTY falls 6 → 5 → 3 → 1 →
0 → 0 → 0.

Daily rebalancing loses **87.61%** of capital and beats NIFTY in **0 of 16
folds**, with a Sharpe of −20.68 and an 87.6% drawdown.

## It is not the transaction costs

This is the part that matters, because "use a cheaper broker" is the obvious
objection and it is wrong.

Costs rise 17.6× across the arms, from ₹1,353 to ₹23,761 on ₹500,000 of
capital. The extra ₹22,408 the daily arm pays is **4.48 percentage points** of
capital. Its return gap against the live configuration is **92.99 percentage
points**.

So costs explain about **5%** of the gap. The other 95% is the strategy itself
having no information at short horizons. A zero-commission, zero-slippage
broker would turn a −87.61% arm into roughly a −83% arm. The problem is not
what the trading costs; it is that the signal has nothing to say about
tomorrow.

## The live configuration is the best arm tested — and still loses to NIFTY

`h90_monthly_LIVE` returned 5.38% against NIFTY's 6.22%: **−0.84pp**, beating
the index in 6 of 16 folds. It is the best of the seven, and it is still
behind a buy-and-hold.

That is consistent with everything else measured on this strategy — Phase 19's
finding that the edge does not survive universe construction, and the live
forward track record at −1.60 / −2.23 / −1.18pp against NIFTY. Three
independent methods, same answer.

## What this settles

Faster trading is not an unexplored opportunity here. It was the explicit
hypothesis, it was tested properly across 16 folds, and it is **decisively
refuted**. No variant of "catch today's momentum and book daily profits" is
supported by this data, and the direction of the result is so consistent
(monotonic across seven arms and every metric) that it is not a sampling
artifact.

The live 90-day/monthly configuration should stay as it is. Not because it is
good — it is not, it trails the index — but because every alternative tested is
worse, and the cost of finding that out has now been paid.

## Caveats, stated plainly

- The universe is `pit_top500`, chosen as the neutral construction after Phase
  19 showed membership choice moves results by up to 12pp. It is still
  survivors-only; see [`../../audit/survivorship-bias.md`](../../audit/survivorship-bias.md).
- The benchmark pays no transaction costs while every arm does, which
  understates each arm by ~0.22pp. That does not change a 93pp gap.
- Sortino is not quoted above because it was computed incorrectly until
  2026-09-10; return, Sharpe and drawdown are unaffected. See
  [`../../audit/backtest-metrics.md`](../../audit/backtest-metrics.md).

## Raw output

`results.json` in this directory holds the per-fold detail. The table above is
the sweep's own printed summary, captured from the run log.
