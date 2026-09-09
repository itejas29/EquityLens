# The metrics every phase conclusion rests on

`_equity_metrics` in `services/backtest.py` produces the CAGR, Sharpe, Sortino,
max drawdown, Calmar and volatility that Phases 11 through 20 are reported in.
Until 2026-09-10 **nothing checked any of them against an independently
computed figure**. There are now 15 known-answer tests, each deriving its
expectation from the definition rather than from the function under test.

## Defect found: Sortino was not Sortino

```python
downside = daily_returns[daily_returns < 0]
downside_vol = downside.std() * sqrt(252)
```

Sortino's denominator is the root-mean-square **shortfall below the target**
(0 here), taken over **every** period. The code made two compounding mistakes:

1. `std()` measures spread about the **mean of the negative returns** — itself
   a negative number — rather than about the 0 target.
2. It divided by the count of negative days rather than by all days.

Both shrink the denominator, so the magnitude of the ratio was inflated.
Measured on a 2,000-day series (drift 0.04%/day, vol 1.2%):

```
denominator as implemented (std of the negatives only) : 0.1112
denominator, downside deviation about 0, all periods   : 0.1315
  implemented / correct                               : 0.846
|Sortino| overstatement                                : 1.18x
```

On the 400-day seeded series the tests use, 2.16 against a correct 2.02
(1.068x).

Direction: **flattering for a positive return**, harsher for a negative one.
Either way the reported number was not the statistic it was labelled as.

### What this does and does not affect

`sortino_ratio` is stored in the results JSON of Phases 11–19 and appears in
three published tables: `README.md`, `phase18_universe_size/FINDINGS.md` and
`phase19_universe_robustness/FINDINGS.md`. Those figures are overstated in
magnitude by roughly 7–18% depending on the return distribution.

**No prose conclusion in any phase cites Sortino.** Phase 19's findings rest on
edge versus NIFTY, drawdown, and downside capture — all computed elsewhere and
unaffected. Sharpe is unaffected: it uses total deviation, which was correct.
So the correction changes three table cells, not a conclusion. Re-running the
phases to refresh those cells is a research decision, not a fix.

## Also corrected: a units label

`max_drawdown_duration_days` counts **trading sessions**, because the equity
curve carries one point per trading day. ~250 of them is a year, not
~250/365 of one. The key keeps its name so stored phase results stay
comparable; the frontend label is now "Drawdown (trading days)".

## Verified correct, so it need not be re-audited

- **CAGR** annualises over the calendar span with 365.25-day years, returns
  `None` when the span is a single day, and is guarded against a fractional
  power of a negative final equity (a wipeout gives `None`, not a complex
  number).
- **Sharpe** matches `(mean * 252 - rf) / (std * sqrt(252))`.
- **Max drawdown** is measured from the running peak, not from the start.
- **Calmar** is `CAGR / |maxDD|` and `None` when there was no drawdown.
- **Guards**: an empty curve and zero capital both return `{}` rather than
  zeros; a flat curve gives `None` for Sharpe rather than dividing by zero.


## Benchmark-relative metrics: checked, and correct

Phase 19's published conclusion cites **downside capture of 154-196%** as the
evidence that V1's risk control fails. That figure comes from
`_capture_ratios`, and nothing had ever checked it. If the function were wrong,
a headline finding would be wrong with it.

It is not wrong. Ten known-answer tests, all passing on the first run:

| case | expected | source of the expectation |
|---|---|---|
| worked example | 48.44% up / 157.14% down | compounded by hand from the definition |
| strategy == benchmark | 100% / 100% | identity |
| strategy holds cash | 0% / 0% | identity |
| strategy rises while index falls | negative downside capture | sign analysis |
| fewer than 2 qualifying months | `None` | not a ratio from one point |
| a partial first month prepended | unchanged | `pct_change` drops the partial period |

`_relative_metrics` (tracking error, information ratio) likewise matches the
definition, and reports `None` rather than 0 for a strategy that tracks the
index exactly.

**So Phase 19's downside-capture conclusion stands.** This is a negative
result and worth recording as one: the number that carries a published finding
was audited and found sound.

The one correction was a docstring. It claimed "both numerator and denominator
are negative on down days, so the ratio stays positive". It does not — a
strategy that GAINS during the benchmark's down months produces a positive
numerator over a negative denominator, and a negative ratio. That is the best
possible outcome, not an error, and "lower is better" still sorts it correctly.
