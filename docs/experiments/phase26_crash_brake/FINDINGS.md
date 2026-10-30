# Phase 26 — the crash brake on NIFTY 50: findings

**Run 2026-10-31**, on Yahoo's `^NSEI` daily closes.
- **Pre-registration:** [PREREGISTRATION.md](PREREGISTRATION.md), committed before
  any code. No changes were made to it.
- **Raw output:** [results.json](results.json).
- **Data:** 2007-09-17 → 2026-10-29, 4,690 sessions. Every check matched what
  the pre-registration recorded.
- **Window:** 2008-07-08 (the first session that trades on every rule's
  decision) to 2026-10-29, 18.3 years. Halves split at 2017-09-20.

## Decision: R1 PASSES, narrowly, and gives up 4 percentage points a year

All figures are after 0.10% per trade, with cash earning 0%.

| | CAGR | Worst loss | Calmar | Sharpe | Volatility | Switches a year | Time invested | ₹10 lakh became, pre-tax | After tax | Cash yield needed to match BH |
|---|---|---|---|---|---|---|---|---|---|---|
| **BH** — buy and hold | 10.01% | −45.4% | 0.221 | 0.60 | 19.5% | 0 | 100% | ₹57.4 lakh | ₹51.5 lakh | — |
| **R1** — 200-day rule | 6.02% | −25.5% | 0.236 | 0.53 | 12.9% | 7.9 | 71% | ₹29.1 lakh | ₹25.8 lakh | 13.5% |
| R2 — 10-month rule | 6.88% | −29.0% | 0.238 | 0.59 | 13.1% | 2.0 | 70% | ₹33.8 lakh | ₹29.5 lakh | 10.1% |
| R3 — V1's 25% rule | 7.04% | −24.4% | 0.288 | 0.59 | 13.3% | 7.9 | 78% | ₹34.8 lakh | ₹30.1 lakh | 13.1% |

The registered test asks whether R1's worst loss is at most two-thirds of BH's
and its Calmar ratio is higher, over the whole window and in each half.

| | R1's worst loss ÷ BH's | Calmar, R1 vs BH |
|---|---|---|
| Whole window | 0.56 | 0.236 vs 0.221 |
| First half, 2008-07 → 2017-09 | 0.56 | 0.237 vs 0.235 |
| Second half, 2017-09 → 2026-10 | 0.63 | 0.246 vs 0.244 |

Every condition holds, so R1 passes. How narrow the pass is matters as much as
the pass:
- **The Calmar edge is under 1% in each half.** R1 gives up return almost
  exactly in proportion to the loss it avoids. That is what an insurance policy
  looks like, not an edge.
- **At 0.25% per trade it fails.** That is the registered cost sensitivity, and
  there R1's Calmar falls to 0.16, below BH's 0.22. The pass needs cheap
  execution, because the rule switches about eight times a year.
- **Its Sharpe ratio is lower than BH's**: 0.53 against 0.60.
- **It costs 4.0 percentage points of return a year.** Over 18 years, ₹10 lakh
  became ₹29 lakh instead of ₹57 lakh, or ₹26 lakh instead of ₹51 lakh after
  tax.
- **Interest on cash narrows the gap but cannot close it.** R1 would need cash
  to earn 13.5% a year to match BH. Indian short-term rates ran roughly 3.5–9%
  over the period.

## Where the brake helps, and where it does not

Each of buy-and-hold's falls deeper than 15%, and what each rule did over the
same dates:

| Buy-and-hold's fall | BH | R1 | R2 | R3 |
|---|---|---|---|---|
| 2008-08 → 2008-10 | −45.4% | 0.0% | 0.0% | −12.6% |
| 2010-11 → 2011-12 | −27.9% | −15.4% | −17.7% | −18.4% |
| 2015-03 → 2016-02 | −22.5% | −20.4% | −15.8% | −20.7% |
| 2020-01 → 2020-03 | −38.4% | −6.0% | −10.0% | −14.1% |
| 2021-10 → 2022-06 | −17.2% | −11.7% | −13.5% | −13.0% |
| 2024-09 → 2025-03 | −15.8% | −13.8% | −9.5% | −14.2% |
| 2026-01 → 2026-10 | −15.6% | −6.8% | −5.7% | −9.0% |

- **Fast, deep crashes (2008, 2020):** the brake was out early and missed most
  of the fall.
- **Slow, choppy declines (2015–16, 2024–25):** it was whipsawed in and out and
  saved little.
- **The 2008 row:** R1 was already in cash when the window opened, because
  NIFTY was then below its average. The fall from January to July 2008 comes
  before the window.

## What this means

- **The crash brake is insurance, not a way to beat NIFTY.** It roughly halved
  the worst loss, and it paid for that with about half the final wealth over 18
  years.
- **R2 and R3 earned more than R1 and had higher Calmar ratios:**
  - R2 trades about twice a year instead of eight, with a deeper worst loss
    (−29.0%);
  - R3, V1's own rule, has the highest Calmar of the four.

  Neither was a candidate. They were registered to show how much the details
  matter, and picking the better one now, after seeing the results, would be
  choosing with hindsight.
- **For the app:** "NIFTY is below its 200-day average" is a risk signal with
  measured evidence:
  - over 2008–2026 it would have cut the COVID fall from −38% to −6%;
  - it cost about 4% a year in return, before interest on cash;
  - it does not beat NIFTY.

## Limits (from the pre-registration)

- **Few events:** the result rests on seven falls deeper than 15% in 18 years.
- **No dividends:** at roughly 1–1.5% a year, leaving them out flatters every
  rule by about that yield times the 22–30% of its money left uninvested,
  roughly 0.2–0.45 points a year.
- **0% cash understates the rules;** the break-even yields above show by how
  much.
- **Today's tax rules** were applied to the whole history, though the actual
  rules changed during it.
- **One index**, and conventional rule lengths, not optimised ones.
