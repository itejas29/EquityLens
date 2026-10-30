# Phase 26 — the crash brake on NIFTY 50 itself: pre-registration

**Written 2026-10-31, before any of the code below existed or any run.** The
commit adding this file is the proof of timing; any later change is recorded
at the end with its reason.

## Question

Phase 25 found one measurable effect in V1's engine: its regime filter, which
cuts exposure when NIFTY is below its 200-day average. That filter turned
NIFTY's −38% COVID drawdown into −6% to −9% for every ranking tested, and gave
up about as much in rallies. Stock selection added nothing.

This phase asks whether the same idea, applied to the index alone, is worth
anything over a longer history. The rule: hold NIFTY 50 while it is above its
200-day average, cash otherwise. Does it give a better trade-off between return
and worst-case loss than buying and holding, over all of Yahoo's NIFTY history?

## Data

- `^NSEI` daily closes from Yahoo, 2007-09-17 to the last complete session. A
  final row with an empty close (an unfinished session) is dropped. Nothing
  else is used.
- It is the **price** index: dividends are excluded for every arm.
- Checked before writing this:
  - 4,690 complete sessions;
  - no zero or out-of-range values;
  - the three daily moves above 12% are real (2008-10-24, the 2009-05-18
    election-result day, 2020-03-23);
  - one 6-day gap, at the October 2014 holiday cluster.

## Arms

- **BH:** buy and hold NIFTY 50.
- **R1 — primary:** the daily 200-day rule. At each close, invested if the
  close is at or above the simple average of the last 200 closes, otherwise
  in cash.
- **R2:** the monthly 10-month rule (Faber 2007, *Journal of Wealth
  Management*). At each month's last close, invested for the following month
  if that close is at or above the average of the last 10 month-end closes.
- **R3:** V1's live regime rule: the 200-day rule, but with 25% invested below
  the average instead of 0%, re-entering in full at once. This is what the app
  runs today.

**Start date:** every arm, BH included, is measured from the same session: the
first one on which every rule has a signal (200 sessions or 10 month-ends of
history). That is mid-2008, so the 2008 crash is in the window only from that
point.

**Execution:** a decision taken at a session's close is executed at the **next
session's close**. Yahoo's NIFTY opening values before 2011 sit within 0.03%
(median) of the previous close, against 0.28% from 2011 on. Filling at the next
open there would in effect fill at the signal close. Every arm takes the full
day of delay, on every switch.

**Costs:** 0.10% of the amount traded, on every buy and every sell. This covers
ETF brokerage, statutory charges and the bid-ask spread. BH pays it once to buy
and once to sell at the end. Sensitivity: 0.25%.

**Cash earns 0%.** No series within the project's data stack records what cash
actually earned. Yahoo's LIQUIDBEES history misses the ETF's payouts for
2010–2012 entirely, and NIFTYBEES carries an unadjusted 1:10 split in December
2019. So, reported alongside: for each rule, the constant cash yield at which
its CAGR would equal BH's.

## Measurements

All figures are after costs and before tax, unless marked otherwise:
- CAGR;
- maximum drawdown (daily closes);
- annualised volatility;
- Sharpe ratio (risk-free 0, matching the 0% cash);
- Calmar ratio (CAGR ÷ |maximum drawdown|);
- downside capture (the arm's mean return in months when BH fell, as a share of
  BH's);
- switches per year;
- share of time invested;
- each arm's drawdown in each of BH's drawdowns deeper than 15%.

## Decision rule

R1 passes — a better trade-off than buying and holding — only if its maximum
drawdown is at most two-thirds of BH's **and** its Calmar ratio is higher than
BH's. Both must hold over the whole window **and** separately in each half. The
halves are split at the window's middle session; each half's metrics come from
the same run.

Whether R1's CAGR beats BH's is reported but not required. A rule that halves
the worst loss for a modestly lower return can still be the better product.
That trade-off is what this tests.

R2 and R3 are reported to show how much the answer depends on the rule's
details. They are not candidates, and the best of them is not picked
afterwards.

## Tax view (reported alongside, not part of the decision)

Today's rules are applied to the whole history:
- **Rates:** 20% on gains from units held under 12 months, 12.5% on longer
  holdings.
- **Losses:** short-term losses set off against any gains, long-term losses only
  against long-term gains, carried forward up to eight years. Lots are first in,
  first out.
- **When tax is paid:** at each financial year's end, from cash. If cash is
  short, units are sold on the next session.
- **Exemption:** the ₹1.25 lakh long-term exemption is ignored, since its
  effect depends on the investor's size.
- **At the end:** every arm is sold at the last session and its final tax is
  paid.

## Known limits, stated in advance

- **Few independent events.** Any drawdown result rests on a handful of bear
  markets (2008, 2011, 2015–16, 2020, 2024–25), not on thousands of
  observations.
- **No dividends.** Leaving them out (roughly 1–1.5% a year) flatters each rule
  by about that yield times its share of time in cash.
- **0% cash understates the rules.** Indian short-term rates ran roughly
  3.5–9% over the period. The break-even yield shows how much this matters.
- **Today's tax rules** are not the ones that applied:
  - long-term gains were exempt before April 2018;
  - short-term gains were taxed at 15% before July 2024 (10% before April
    2008).
- **One index, one market.** The 200-day length is the conventional choice, not
  an optimised one.

## Changes after registration

None yet.
