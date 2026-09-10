# The Track Record measured returns from a price nobody could pay

Measured 2026-09-10 against the live database.

## The problem

Every return in `signal_outcomes` is computed from `reference_close` — the
previous close the signal was built from:

```python
stk_ret = (ph_closes[eval_date] - reference_close) / reference_close * 100
```

But the published call is not "buy at yesterday's close". It is **"buy between
`entry_low` and `entry_high`"**, and `entry_high` is the worst fill inside the
app's own zone. Across all 168 published signals:

```
entry_high sits above reference_close by:
   mean 1.276%   min 0.000%   max 2.634%
```

So every figure on the Track Record page was better than following the call
would have produced — the same family as the risk:reward finding: a number
measured against a price the user cannot obtain.

## What it changes

The live track record, both ways (168 signals, 2026-08-14 to 2026-09-09):

```
 horizon    n   avg ret  from entry    NIFTY  edge(ref)  EDGE(entry)   win%
      1d  140     -0.43       -1.82    -0.22      -0.22        -1.60   50.0
      5d  109     -1.78       -2.98    -0.75      -1.03        -2.23   45.0
     10d   72     -1.98       -2.69    -1.51      -0.47        -1.18   41.7
     20d    0   (no outcomes yet — the app has only published since 2026-08-14,
                 so no signal is yet 20 trading days old)
```

The edge measured from an obtainable fill is **substantially worse** than what
the page reported: −1.60 against −0.22 at one day, −2.23 against −1.03 at five,
−1.18 against −0.47 at ten.

Alongside: 3 targets hit against 14 stops, and a win rate that falls with
horizon (50.0% → 45.0% → 41.7%).

## What was done

Both figures are now computed and both are shown. The reference-close return
measures the **signal's information content**; the entry-based return measures
the **trade**. The page leads with the second and the Edge column uses it,
because the page's claim is about what happened to a user rather than to a
number.

The re-basing is exact, not an approximation — the evaluation price is
recovered from the stored return and `reference_close`, then re-divided by
`entry_high`. Subtracting the entry gap from the return would be wrong by the
cross term.

No migration and no re-evaluation: `entry_high` and `reference_close` are both
already stored on every signal, so the whole history re-bases from data on
disk.

## Checked and NOT a defect

The 20-day horizon has no outcomes, which looks like the 40-day re-evaluation
cutoff silently dropping signals before their 20d matures. It is not: the app
has only published since **2026-08-14**, 26 calendar days before this audit, so
no signal is yet 20 trading days old. Zero signals have aged past the cutoff.

The latent fragility is real but not currently biting: a signal only gets its
20-day figure if the evaluation job runs during the window where it is between
~28 and 40 calendar days old. The job runs daily, so that window is never
missed in normal operation.
