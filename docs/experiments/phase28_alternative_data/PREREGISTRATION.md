# Phase 28 — alternative (non-price) data: pre-registration

**Written 2026-11-13, before any of this data was loaded or any return was
computed.** The commit adding this file is the proof of timing. Any later
change is recorded at the end, with its reason and whether any result had been
seen when it was made.

The directive asked for `phase27_alternative_data`. Phase 27 was already taken
by the portfolio-construction study committed earlier today, so this phase is
numbered 28.

## Question

Phases 14 to 26 found that price data alone (momentum, the literature
rankings, an ML ranker, trading faster) does not beat NIFTY 50 after costs.
Do any of five public non-price data sources carry information that does?

## What was established before writing this (data access only, no returns)

| # | Source | Access | Format |
|---|---|---|---|
| 1 | Insider and promoter trades (SEBI PIT disclosures) | NSE `api/corporates-pit`, by date range | JSON: person, category, mode, buy/sell, quantity, value, broadcast time |
| 2 | Bulk and block deals | NSE `api/historicalOR/bulk-block-short-deals`, at most 70 rows per request, so one request per day | JSON: date, symbol, client, buy/sell, quantity, price |
| 3 | Mutual-fund portfolios | **Not available in one place.** AMFI's page links to each AMC's own site; each posts monthly Excel files in its own layout, and reading Excel needs `openpyxl`, which is not in the approved stack | — |
| 4 | FII/FPI daily flows | NSDL FPI archive: one request returns a month of daily reporting dates. NSE's own FII/DII API has only the latest day | HTML table |
| 5 | Index changes | niftyindices.com press releases (1,531, back to 1998); the stock lists are inside PDFs, readable with the standard library | PDF text |

## Common rules (signals 1, 2, 3 and 5: single stocks)

- **Sample:** signals with a public date from 2018-04-01 to the last date whose
  trade can finish inside the lab's price data, which ends 2026-10-30. That
  is about 8.5 years, above the five-year minimum.
- **Universe:** the stock must be in the survivorship-free universe B of the
  Phase 24 fold whose test window contains the signal date: the top 500 NSE
  company shares by traded value, delisted names included. Signals after the
  last fold (which ends 2026-03-22) use fold 16's universe.
- **Mapping an event to a stock:**
  1. Take the event's NSE symbol on the event date.
  2. Find that symbol's ISIN in that day's bhavcopy (or the nearest earlier
     one).
  3. Find the lab stock with that ISIN, which handles renames.
  4. Otherwise match the symbol directly, or match `DL:<symbol>` for a
     delisted name.

  Unmatched events are counted and reported.
- **Public date:** the date the information became public (defined per signal
  below), never the date of the underlying trade.
- **Entry:** the open of the first trading session strictly after the public
  date.
- **Exit:**
  - Hold 60 trading sessions, counting the entry session as the first, and
    sell at the open of the next session.
  - **Stop:** if any session's low (from the entry session on) is at or below
    85% of the entry price, sell at 85% of entry, or at that session's open if
    it opened below that level. No target.
- **Missing bars:**
  - A session with no bar for the stock is skipped.
  - If the stock has no bar for 30 calendar days, the trade closes at its last
    close (the Phase 24 delisting rule).
  - A trade that cannot finish by 2026-10-30 is excluded.
- **One position per stock at a time:** a signal in a stock already held is
  ignored.
- **Costs:** 0.24% round trip for stocks, 0.12% on each leg, applied to the
  fill prices.
- **Benchmark:** NIFTY 50 total return over exactly the trade's window, from
  the entry session's open to the exit session's open. This is approximated as
  the price index (Yahoo `^NSEI`, downloaded once) plus 1.3% a year,
  pro-rata by calendar days, as the directive allows. A stopped-out trade is
  compared over its own shorter window.
- **Excess return per trade:** the net trade return minus the benchmark
  return.

## The five signals, one variant each

### S1 — insider cluster buy

- **Qualifying record:**
  - the transaction type is `Buy`;
  - the acquisition mode is `Market Purchase` (excluding ESOP, preferential
    allotment, off-market, inter-se transfer, gifts and pledges);
  - the security is equity shares;
  - the person category is a promoter, promoter group or director (KMP and
    employees are excluded, per the directive's "promoter or director");
  - the value (`secVal`) is above 0.
- **De-duplication:** on (symbol, person, from date, to date, quantity,
  value), keeping the earliest broadcast.
- **Public date:** the broadcast date of the filing (NSE's `date` field, IST).
- **Signal:** on public date D, within the 5 trading sessions ending on D, the
  qualifying records for the stock come from at least 2 distinct persons and
  total at least ₹50 lakh. The signal fires on the first such D.

### S2 — large bulk or block buy

- **Qualifying client-day:** for one client, one stock and one day, across
  bulk and block deals:
  - the buy value (quantity × weighted average price) is at least ₹10 crore;
  - the same client did not sell that stock that day. This excludes
    intraday and arbitrage churn.
- **Public date:** the deal date. NSE publishes bulk and block deals after the
  close the same day.
- **Signal:** any qualifying client-day.

### S3 — top-fund accumulation

- **Definition (not testable):**
  - Among active equity schemes, take the top quartile by trailing 12-month
    return.
  - A stock qualifies when shares held by those schemes rise at least 25%
    month over month, with at least 3 of those schemes holding it.
  - Public date: the 10th of the following month (SEBI's disclosure
    deadline).
- **Expected outcome: not testable.** No consolidated source, and reading the
  files needs a library outside the stack. This is recorded as blocked unless
  both of those change.

### S4 — extreme FII selling, contrarian (index level)

- **Data:** NSDL FPI daily trends, equity, Stock Exchange route, net
  investment in ₹ crore.
- **Public date:** the reporting date. NSDL reports trades up to the previous
  session.
- **Signal:** net investment at or below −₹5,000 crore on a reporting date.
- **Trade:**
  - buy NIFTY 50 (ETF proxy: the price index plus 1.3% a year) at the open of
    the next session;
  - same 60-session hold and −15% stop as the stock signals;
  - 0.10% round trip;
  - one position at a time, so signals during an open trade are ignored.
- **Excess return:** the stock signals' benchmark would be zero by
  construction here (it *is* NIFTY). Instead, a trade's excess is its net
  return minus the mean 60-session NIFTY total return from entering at every
  session's open across the same sample. That asks whether the signal picks
  better-than-average days to buy the index.

### S5 — index deletion recovery

- **Data:** niftyindices.com press releases on replacements in NIFTY 50,
  NIFTY Next 50 and NIFTY 500.
- **Qualifying exclusion:**
  - the company is excluded from one of these three indices;
  - its section of the release names a periodic review ("review" in the
    section heading);
  - the heading names no corporate event (amalgamation, merger, scheme,
    demerger, delisting, suspension), because those stocks stop trading or
    change form and no recovery from forced selling applies.
- **De-duplication:** one signal per stock per release.
- **Public date:** the last session before the effective date (the "close of"
  date, when index funds sell).
- **Entry:** the open of the next session, the effective date.
- **Symbol check:** each extracted symbol must exist in that date's bhavcopy;
  an unconfirmed one is dropped and counted.

## Pass / fail

A signal **PASSES** only if all three of these hold:

1. there are at least 30 trades;
2. the median excess per trade is above 0;
3. a one-sided one-sample t-test of the per-trade excess against 0 gives
   p < 0.02. That is the directive's 0.10 divided by the five registered
   signals (Bonferroni). The test uses `scipy.stats`, which is already
   installed with scikit-learn and used by the Phase 14 script.

Fewer than 30 trades is reported as **INSUFFICIENT**, never as a pass.

Robustness, run only for a passing signal; it is called robust only if all
three hold:

- the median excess is above 0 in each half of the sample, split at the
  median signal date;
- the signal still passes at 0.35% round-trip costs;
- the monthly-clustered test holds: averaging the excess per entry month and
  t-testing those monthly means gives p < 0.02. Trades entered in the same
  weeks share the market's moves, so the trade-level test overstates
  certainty.

## Phase D (only if a signal passes)

The passing signal's entries are opened only while NIFTY is at or above its
200-day average (the crash brake). Each version's Calmar ratio (annual return
divided by maximum drawdown) is measured on an equal-weight book of the
signal's trades:

- the signal alone;
- the signal with the brake;
- the brake alone, on NIFTY.

## Reporting

- **Every signal** gets its trade count, median and mean excess, win rate
  (share of trades with excess above 0), p-value and PASS / FAIL /
  INSUFFICIENT / BLOCKED. All in FINDINGS.md, raw numbers in results.json.
- **Each source** gets its origin, coverage dates, record count and gaps.
- **No other variant** of any signal is run for a decision. Any descriptive
  breakdown (for example S5 by index) is labelled descriptive.

## Changes after registration

1. **2026-11-13, before any return was computed: S5 parser bug fix, rule
   unchanged.** A dry run on the downloaded releases showed the code was
   reading the wrong text as each exclusion's "section heading":
   - It looked only for lowercase `a)`-style markers, so it took index
     sub-headers ("a) Nifty 50 …") for the heading.
   - It missed the real headings ("1) Replacements on account of semi-annual
     review …", "A. Replacement on account of … demerger …").

   The code now finds headings by their wording ("… on account of …:" or
   "… review …:"), and the registered rule is applied to them unchanged. On
   the releases checked, the three semi-annual reviews (February 2025, August
   2025, February 2026) now qualify and the six one-off replacements do not.
