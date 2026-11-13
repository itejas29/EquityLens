# Phase 27 — positions and refill frequency for the AI paper account: findings

**Run 2026-11-13, 16:13 → 18:41 IST** (148 minutes, three worker processes) in
the local lab, on Phase 24's survivorship-free universe B and 16 folds.

- **Pre-registration:** [PREREGISTRATION.md](PREREGISTRATION.md), committed
  before the script existed (06c3de7), with no changes since.
- **Raw output:** [results.json](results.json).

## Decision: KEEP 8 POSITIONS, MONTHLY

The pre-registered rule compared each arm with the live setup (8 positions,
monthly) on paired per-fold returns. **Every one of the five alternatives
is "Worse"**: each loses to the live setup on the median, on the mean, and
after dropping any one fold. None qualified as Better or as No measured cost,
so the AI account stays at 8 positions, monthly.

| Arm | Mean return per fold | Excess vs NIFTY (median / mean) | Folds beating NIFTY | Mean max drawdown | Worst drawdown | Capital deployed | Trades per fold | vs live (median / mean) | Class |
|---|---|---|---|---|---|---|---|---|---|
| **N8_monthly (live)** | 7.22% | +1.86 / +1.00 pp | 10 / 16 | −14.30% | −22.09% | 67.5% | 21 | — | reference |
| N8_weekly | 5.61% | −2.70 / −0.61 pp | 5 / 16 | −16.68% | −32.66% | 73.0% | 31 | −4.20 / −1.62 pp | Worse |
| N12_monthly | 4.82% | −2.77 / −1.41 pp | 6 / 16 | −13.70% | −23.08% | 67.7% | 31 | −3.35 / −2.41 pp | Worse |
| N12_weekly | 5.43% | −0.79 / −0.79 pp | 7 / 16 | −15.38% | −26.64% | 73.7% | 45 | −1.71 / −1.79 pp | Worse |
| N15_monthly | 6.31% | +0.49 / +0.09 pp | 10 / 16 | −13.29% | −23.82% | 68.2% | 38 | −1.11 / −0.91 pp | Worse |
| N15_weekly | 4.33% | −3.12 / −1.90 pp | 6 / 16 | −15.10% | −25.87% | 74.3% | 55 | −2.67 / −2.90 pp | Worse |

Folds are 6 months each, 2018-04 → 2026-03. Each fold starts with ₹5 lakh.

## What it says about the two ideas

- **Refilling weekly does not put much more money to work.** Average
  deployment rises only about 5–6 points (67.5% → 73–74%) at every size.
- **Weekly refill costs return at 8 and 15 positions:**
  - 1.6 points per fold on average at 8 positions, where it also deepens the
    worst drawdown from −22% to −33%;
  - 2.0 points at 15 positions.
- **At 12 positions weekly does better than monthly** (5.43% vs 4.82%), but
  both remain below the live setup.
- **Why money stays idle (inferred, not measured here):** since refilling
  more often barely changes deployment, most idle cash is not from waiting
  for the next month. The likely causes are:
  - the crash brake (25% invested in a bear regime);
  - the entry gates (the trend filter, the stop distance).

  Refilling more often changes neither.
- **Holding 12 or 15 stocks did not help either.** At 15 positions, monthly,
  the result is closest to the live setup (−1.11 points median per fold),
  with a slightly shallower average drawdown, but it is still below on every
  measure the rule checks.

## What this is not

- **Not evidence of an edge.** The live arm's +1.86 points median over NIFTY
  is noise of the size Phase 24 measured: V1's 10-stock excess varied by
  13.8 points from fold to fold, and at 10 stocks on the same bench the
  median was −0.03 points.
- **A choice between configurations, not a claim that any of them beats the
  index.** The only question was whether the proposed changes would make
  the paper account better. They do not.
