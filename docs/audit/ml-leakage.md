# Two leaks in the ML pipeline

Found 2026-09-10. Both affect the metrics published in `README.md` and
`docs/ml_results.md`; neither affects trading, because `ml_probability` is an
additional field on recommendations and is never folded into `overall_score`.

## 1. The train/validation/test split

`train.py` states its intent clearly: shuffling "would let the model train on
rows chronologically after some of its own test rows, which is exactly the kind
of look-ahead this whole project has been careful to avoid everywhere else." It
did not shuffle. It leaked anyway.

```python
df = df.sort_values("date").reset_index(drop=True)
train_end = int(n * 0.70)
return df.iloc[:train_end], ...
```

**It cut mid-date.** A positional cut on a stock-by-date panel with hundreds of
ragged rows per trading day (stocks enter the universe at different times, and a
missing bar drops a stock out of that day) lands inside a date — putting the
same trading day, and the same benchmark forward return, in train and
validation simultaneously.

**It did not purge.** The target is the forward 20-trading-day return, so a row
dated D is labelled by the price at D+20. Without a gap, the last 20 trading
days of train are labelled by prices inside the validation window, and the last
20 of validation by prices inside test.

Fixed: cuts on unique dates, with `PURGE_DAYS = TARGET_HORIZON_DAYS` dropped
either side of each boundary. Costs ~40 trading days out of ~1,900. A panel too
short to purge falls back to an unpurged date split with a WARNING rather than
silently returning empty frames.

## 2. The fundamentals features

Six features — `pe_ratio`, `pb_ratio`, `debt_to_equity`, `revenue_growth`,
`eps_growth`, `operating_margin` — come from `fundamentals`, which the loader
reads with `ORDER BY as_of_date DESC LIMIT 1`.

Measured against production:

```
fundamentals as_of_date : 2026-08-14 .. 2026-09-01  (5 distinct dates,
                                                     at most 3 per stock)
price history spans     : 2016-08-16 .. 2026-09-09
```

A PE measured on 2026-09-01 is attached to rows dated back to 2016-08-16. **A
decade of look-ahead.**

The module already carried a note about this, and the note was too mild:

> "the model can use these features to tell stocks apart from each other, but
> not to learn how a stock's own fundamentals evolved over time"

That describes a **loss** of information. It is a **leak** of it. Because the
value is constant per stock, the feature is not weak — it is a stock-identity
label carrying end-state information, and against a chronological split a model
can learn from the training period which stocks ended up with which
fundamentals and apply that to the same stocks in test.

Fixed: `INCLUDE_FUNDAMENTAL_FEATURES = False`. Build rule 1 is that something
which cannot be built properly is left out and said so, and point-in-time
fundamentals cannot be built from a table holding three weeks of snapshots. The
toggle remains so the effect on ROC-AUC can be measured rather than assumed.

## Not done

Regenerating the metrics. That needs a full training run against the production
database. Until then the README carries an explicit note that the published
figures predate both fixes and should be expected to fall.
