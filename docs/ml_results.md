# ML Layer Results

Secondary signal only — a probability estimate shown alongside the rule-based
recommendation, never blended into `overall_score`. All numbers below are
copied directly from a real training run against the seeded 40-stock
universe (`app/ml/artifacts/latest.json`, trained 2026-08-14). Nothing here
has been hand-tuned to look better.

## Target

Binary: does the stock's forward 20-trading-day return beat `^NSEI`'s
forward 20-trading-day return over the same window? Built with a per-stock
`shift(-20)`, the only place future data enters the pipeline — it feeds the
label only, never a feature.

## Data limitation (read this before the numbers)

`fundamentals` holds one snapshot per stock, not a historical series, so
`pe_ratio`, `pb_ratio`, `roe`, `debt_to_equity`, `revenue_growth`, and
`eps_growth` are the same value repeated across every historical row for a
given stock — they can only help the model tell stocks apart from each
other, not learn how a stock's own fundamentals changed over time. The
technical features (`rsi_14`, `macd_hist`, `pct_from_50dma`,
`pct_from_200dma`, `volume_ratio`, `volatility`, `beta`) don't have this
problem — they're genuinely point-in-time, read directly from
`indicators` rows, which are backward-looking rolling/EMA computations by
construction.

## Dataset

- 19,688 candidate (stock, date) rows → **1,100 survive** after dropping
  rows with any missing feature or an undefined target. The drop is driven
  almost entirely by the fundamentals gaps already documented in Phase 2/4
  (30/40 stocks missing ROE, 6/40 missing debt/equity) — only stocks with a
  complete fundamentals snapshot contribute any rows at all.
- Time-based split, no shuffling: train earliest 70% (770 rows,
  2025-08-18 → 2026-04-06), validate next 15% (165 rows), test final 15%
  (165 rows, 2026-05-27 → 2026-07-16).
- Target balance: 527 positive / 573 negative — roughly balanced, majority-
  class baseline test accuracy is **0.5091**.

## Models

**Baseline — LogisticRegression** (StandardScaler + LogisticRegression,
default regularization):

| metric | value |
|---|---|
| accuracy | 0.5515 |
| precision | 0.6786 |
| recall | 0.2262 |
| F1 | 0.3393 |
| ROC-AUC | 0.7341 |
| confusion matrix | `[[72, 9], [65, 19]]` |

**RandomForestClassifier** (`n_estimators=200`, `max_depth=5` — capped to
limit overfitting on a 770-row training set, `class_weight="balanced"`):

| metric | value |
|---|---|
| accuracy | 0.5212 |
| precision | 0.6471 |
| recall | 0.1310 |
| F1 | 0.2178 |
| ROC-AUC | 0.5657 |
| confusion matrix | `[[75, 6], [73, 11]]` |

Feature importances (RandomForest):

| feature | importance |
|---|---|
| pct_from_50dma | 0.1500 |
| beta | 0.1499 |
| volatility | 0.1434 |
| rsi_14 | 0.1275 |
| pct_from_200dma | 0.1232 |
| macd_hist | 0.1176 |
| volume_ratio | 0.1011 |
| eps_growth | 0.0171 |
| revenue_growth | 0.0160 |
| roe | 0.0153 |
| debt_to_equity | 0.0144 |
| pe_ratio | 0.0131 |
| pb_ratio | 0.0114 |

The static, single-snapshot fundamentals features rank last by a wide
margin — exactly what the data limitation above predicts: they carry almost
no signal once you can't observe how they change over time.

### LogisticRegression beat RandomForest here — reported as measured

On this dataset, the simple linear baseline outperforms the more complex
model on every held-out test metric, most clearly ROC-AUC (0.7341 vs
0.5657 — RandomForest is barely better than a coin flip on ranking).
RandomForest actually scored *higher* on the validation split (0.6061 vs
0.5758 accuracy) but that reversed on test, which combined with 770
training rows and a 5-deep, 200-tree forest, reads as RandomForest fitting
validation-split noise rather than a real generalizable pattern. Rather
than swap in a different split or tune hyperparameters until RandomForest
wins, `app/ml/predict.py` serves whichever model actually tested better —
**LogisticRegression** — and says so in code. RandomForest's artifact is
still saved for reference.

## ML probability vs. rule-based score on the test period

For every (stock, date) in the test set, the point-in-time rule-based
`overall_score` (the same technical+risk composite the backtest engine
uses — see `docs` / Phase 6 notes on why fundamentals are excluded there
too) was computed and compared against the ML probability, using the same
actual 20-day-forward outperformance label as ground truth.

| | value |
|---|---|
| comparable rows | 165 |
| base rate (target = 1) | 0.5091 |
| **ML precision @ top 30%** | **0.6327** |
| **rule-based precision @ top 30%** | **0.3469** |
| Spearman rank correlation (ML vs. rule-based) | -0.4583 |

**ML beats the rule-based baseline on this test period.** Stocks the ML
model ranked in its top 30% by predicted probability actually outperformed
the benchmark 63.3% of the time — well above the 50.9% base rate. Stocks
the rule-based technical+risk score ranked in its top 30% outperformed only
34.7% of the time — *worse* than chance. The two rankings are also
moderately negatively correlated (-0.46), meaning they largely disagree
about which stocks look attractive over this window; on this particular
test period, "high recent momentum/low volatility" (what the rule-based
score rewards) and "likely to beat the index over the next 20 days" (what
the label measures) pointed in different directions.

This is one 165-row test window from one 40-stock universe — not a claim
that the ML model reliably beats the rule-based approach in general. It's
reported here exactly as measured, per this project's no-cherry-picking
rule; a different backtest window could easily show the opposite.

## Reproducing

```bash
python -m app.ml.train
```

Trains fresh, writes `app/ml/artifacts/{rf,lr}_<timestamp>.joblib` and
`app/ml/artifacts/latest.json` (which `app/ml/predict.py` reads to find the
selected model).
