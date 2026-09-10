# Net bias in the strategy-versus-NIFTY comparison

Two systematic biases exist in opposite directions. Neither was documented, and
neither is large enough on its own to overturn a conclusion — but an
institutional reader will ask for the net picture, and "we never looked" is not
an answer.

## 1. Survivorship — favours the STRATEGY

The default backtest universe is `Stock.is_active == True`: the stocks in the
universe *today*, projected backwards. 67 inactive stocks holding 112,385 bars
are excluded from every run, and the active universe grows from 315 names with
2016 data to 500 in 2026.

The excluded names are the ones that left — delisted, acquired, or dropped for
failing liquidity. For a momentum strategy that matters more than average,
because momentum's worst outcomes are the stocks that fall hard and keep
falling until they are gone.

**Direction: inflates strategy returns. Magnitude: not yet quantified** — it
needs a fold run with `include_inactive=True` against the same fold run
without. Full detail in [survivorship-bias.md](survivorship-bias.md).

## 2. A frictionless benchmark — favours NIFTY

`_benchmark_buy_and_hold` buys the index at the first close with
`shares = initial_capital / start_price` and pays nothing:

- no transaction cost
- no slippage

The strategy pays both, on every leg, on every rebalance. A real index position
is not free either — an entry and an exit at this backtest's own assumptions:

```
transaction cost (round trip) : 0.120%
slippage (per leg)            : 0.050%
friction a real buy-and-hold pays: 0.220% over the holding period
```

So the benchmark's return is overstated by **0.220 percentage points**, once,
regardless of fold length — about 0.44pp/yr on a 6-month fold, 0.02pp/yr over
ten years. (A real investor would more likely hold an index fund and pay an
expense ratio instead, which is a different number but the same order.)

**Direction: deflates the strategy's measured edge. Magnitude: 0.220pp per
fold.**

## Net

For Phase 19, whose measured mean edge was **+0.47pp against a 4.51pp spread
across universe constructions**:

- Correcting the benchmark friction would *add* ~0.22pp to the strategy's edge —
  roughly half the mean edge, and well inside the spread.
- Correcting survivorship would *subtract* an unquantified amount, and for a
  momentum strategy the reasonable prior is that it is the larger of the two.

The two partly offset, both are small relative to the fold-to-fold noise that
Phase 19 measured, and **neither changes the conclusion that no edge is
detectable**. What they do change is the honesty of the claim: the comparison
is not neutral, and the direction of each thumb on the scale is now written
down.

## Not changed

Neither is "fixed", deliberately. Adding costs to the benchmark and switching
the universe both alter every published comparison, which is a research
decision rather than a bug fix. `include_inactive` exists and defaults to
False; the benchmark friction is documented here rather than silently
introduced. The right time to change both is a single re-run of the phases with
both corrections applied together, so the numbers stay comparable to each
other.
