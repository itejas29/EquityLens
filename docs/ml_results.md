# ML Layer Results

Secondary signal only — a probability estimate shown alongside the rule-based
recommendation, never blended into `overall_score`. All numbers below are
copied directly from real training runs (`app/ml/artifacts/latest.json`).
Nothing here has been hand-tuned to look better.

## Improvement attempt (2026-08-15) — measurable, still not enough

Three principled changes, each motivated before it was made, not selected by
whichever produced a better test score:

**1. Relative-strength features — a target/feature mismatch.** The target asks
a *relative* question ("does this beat ^NSEI over 20 days?") but every original
feature was *absolute* (own RSI, own distance from own moving average). Nothing
described the stock versus the index. Added `rel_5/20/60/120` (trailing return
minus benchmark return — the same quantity as the target, pointed backwards),
own trailing returns, and 52-week range position.

**2. Cross-sectional ranks.** "Beats the index" is really "beats the average
stock", so each date's features are also expressed as percentile ranks within
that day's universe (`cs_*`). This neutralises market-wide moves that otherwise
dominate absolute features.

**3. History depth 2y → 5y.** At 2y the 252-day beta warm-up left only **243
usable dates** — under a year of distinct observations from a single regime.
5y gives **981** across several regimes. Cost: 2.8 minutes, 0 failures.

Two bugs found and fixed along the way:

- **Model selection read the test set.** `selected_model` compared *test* ROC-AUC
  between LR and RF, which turned the test split into a selection set and made
  the reported figure optimistic. Now selected on validation AUC; the test split
  is touched once, for the final report.
- **RF capacity never scaled with the data.** `max_depth=5` was set when training
  had 770 rows; it now has 232,269. Depth is now chosen from a grid on
  validation (5/10/16/24 → **10** won at 0.5291; 16 and 24 were worse, so the
  underfit was real but shallow).

### Measured progression

| Run | Dataset | Best test ROC-AUC |
|---|---|---|
| 40-stock, 2y | 1,100 rows / 40 stocks | 0.7341 — small-sample artifact |
| 500-stock, 2y, absolute features | 80,846 rows / 375 stocks | 0.5136 |
| + relative & cross-sectional features, 5y | 331,814 rows | 0.5301 |
| + validation-selected depth, leak fixed | 331,814 rows | **0.5369** |

**Result: 0.5136 → 0.5369.** A real improvement, and still below the 0.55
serving bar, so the model remains unserved. Top-30% precision did improve to
0.6194 against a 0.5897 base rate (+3.0 points, up from +1.3), and the ML
ranking beat the rule-based score's 0.546 on the same rows — but on a
one-window diagnostic over 20 sampled dates, that is suggestive, not decided.

I stopped here rather than continuing. The remaining moves — sweeping
hyperparameters until the *test* number cleared 0.55, or lowering the bar —
would produce a better-looking figure and the same model.

## Headline: the model is currently NOT SERVED

Retrained 2026-08-14 on the 500-stock universe: **80,846 rows across 375
stocks**, up from 1,100 rows across 40. On that much larger and more
realistic sample the best model scored a **test ROC-AUC of 0.5136** — a coin
flip. `app/ml/predict.py` therefore refuses to serve it (`MIN_SERVABLE_ROC_AUC
= 0.55`) and `ml_probability` reads as `null` everywhere in the API.

**The earlier 0.7341 ROC-AUC was an artifact of a tiny sample.** That figure
came from a 165-row test window on 40 large-caps. Testing on 12,127 rows
across 375 stocks, the apparent edge disappeared. The honest conclusion is
that the earlier number never measured a real edge — it measured a small
sample. It is left documented below rather than deleted, because "we reported
0.73 and it did not survive a bigger test" is the useful record.

### Measured comparison

| | Old run (40 stocks) | New run (500 stocks) |
|---|---|---|
| Rows used | 1,100 | 80,846 |
| Contributing stocks | 40 | 375 |
| Test rows | 165 | 12,127 |
| Best test ROC-AUC | 0.7341 (LR) | **0.5136 (RF)** |
| Served? | yes | **no — below the 0.55 bar** |

### Why the dataset grew

`roe` was dropped from the feature set on coverage grounds, decided from
these counts before any model was fitted:

```
pe 474/500 · pb 498/500 · debt_to_equity 457/500 · revenue_growth 496/500
eps_growth 456/500 · operating_margin 500/500 · roe 78/500
```

Because a row is dropped when *any* feature is missing, `roe` alone collapsed
the trainable set to 46 stocks. `operating_margin` (500/500 coverage) replaced
it. This is why the sample grew 8x — not a modelling change.

### What the 2026-08-14 500-stock run measured

- Majority-class baseline test accuracy: **0.4492**
- LogisticRegression: accuracy 0.4878, ROC-AUC **0.4957** (below random)
- RandomForest: accuracy 0.4871, ROC-AUC **0.5136** (selected on validation
  accuracy 0.4614 vs 0.4181, then failed the serving bar)
- ML precision at top 30%: 0.5640 against a **0.5508 base rate** — a 1.3
  point edge, inside noise
- Rule-based precision at top 30%: 0.4975 — *below* the base rate on this
  window
- Spearman rank correlation, ML vs rule-based: 0.0666 (they disagree, and
  neither is measurably right)
- Top RF feature importances: `pct_from_50dma` 0.157, `revenue_growth` 0.148,
  `pct_from_200dma` 0.130

### What would actually be needed

More rows did not help, which points at the features rather than the sample
size. The binding constraint is that the fundamentals are a **single snapshot
broadcast across every date** — they can separate stocks from each other but
carry no information about how any stock changed over time, so effectively the
model has seven genuine time-varying inputs, all price-derived. Historical
fundamentals (quarterly filings) would be a real change; more stocks or more
tuning on this feature set would not.

---

*The original 40-stock writeup follows, kept for the record.*

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
