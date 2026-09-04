# Phase 19 — v1's measured edge does not survive universe construction

Question: Phase 17 reported live v1 at 16.76% mean OOS (Sharpe 1.23) vs NIFTY 6.22%.
Phase 18 reported the same strategy, same 16 folds, same benchmark, at 4.48%. Is the
edge a property of the strategy, or of the universe it was measured on?

**Answer: mostly the universe. The strategy does not show a reliable edge on
membership it was not selected for.**

## Result

Identical v1 params, identical 16 folds, membership rebuilt per fold. Only universe
construction differs. Pool of 1000; every arm draws 500.

| construction | ret% | Sharpe | Sortino | maxDD% | vs NIFTY | beat |
|---|---|---|---|---|---|---|
| current_top500 | 8.88 | 0.41 | 0.48 | −13.41 | +2.66 | 7/16 |
| pit_top500 | 4.49 | 0.03 | 0.13 | −14.47 | −1.73 | 5/16 |
| pit_top500_60d | 5.06 | 0.10 | 0.17 | −14.04 | −1.16 | 6/16 |
| random500_s1 | 5.56 | 0.11 | 0.11 | −14.17 | −0.66 | 8/16 |
| random500_s2 | 6.71 | 0.22 | 0.55 | −13.31 | +0.49 | 7/16 |
| random500_s3 | 7.17 | 0.16 | 0.21 | −13.97 | +0.95 | 8/16 |
| bottom500 | 9.00 | 0.40 | 0.70 | −14.72 | +2.78 | 9/16 |
| **NIFTY 50** | **6.22** | | | | | |

- **Spread across constructions: 4.51pp.** Mean edge over NIFTY: **+0.47pp.**
  The choice of membership moves the result roughly ten times as much as the
  strategy beats its benchmark.
- **Random membership: +0.26pp vs NIFTY (sd 0.68 across three seeds).** This is
  the decisive arm. On membership nobody selected for it, v1 performs like the
  index.
- The two arms that beat NIFTY meaningfully — `current_top500` (+2.66) and
  `bottom500` (+2.78) — are the two most selection-biased. `current_top500`
  encodes hindsight (liquid *today* correlates with having grown since 2016).
  `bottom500` is the least liquid 500, where the flat 12bps cost model is least
  defensible, so its 9.00% is simultaneously the best-looking and least
  achievable number in the table.
- Membership overlap between `current_top500` and `pit_top500` ran 306–359 of
  500 across folds. Phase 17 and Phase 18 were never comparing sizes; they were
  comparing substantially different sets of companies.

## What this does and does not establish

**Does:** 16.76% cannot be quoted as v1's edge. It is not reproducible under any
neutral construction, and the strategy shows no reliable alpha independent of
selection.

**Does not:** prove v1 is worthless. Drawdowns sit in a −13 to −15% band across
every arm, which the regime filter is designed to produce and which is worth
measuring properly against NIFTY's own drawdown — that comparison is not in this
run and is the obvious next question.

**Unexplained residual:** `current_top500` returns 8.88% here, not Phase 17's
16.76%, despite both being "today's most liquid names". The pools differ (this
draws from the lab's fresh 1000-stock ingest; Phase 17 used production's
weekly-rebuilt 500) and so may the data vintage. That a third construction gives
a third number reinforces the finding rather than softening it, but the specific
gap is not isolated here.

**Survivorship is untouched by all of it.** Every universe draws from names that
exist and trade today; the NSE catalogue holds no delisted constituents and
yfinance exposes no historical membership. Every figure above is therefore
optimistic in absolute terms. None is an achievable return.

## Implication

Any performance claim about v1 — in a README, a pitch, or a conversation with a
buyer — cannot rest on backtested return. The only evidence that survives this
class of critique is live, forward, unmodified track record, which as of this
writing is days old and had a cadence bug for most of it.

## Reproduce

```
python scripts/phase18_prepare_universe.py       # non-production DB, builds the 1000 pool
python scripts/phase19_universe_robustness.py    # refuses below 1000 active
```

Raw per-fold metrics: `results.json`.
