# Forward record, scored realistically — and why positions stop out

**Measured 2026-10-30** on a read-only export of production: 296 published calls
on 37 dates (2026-08-14 → 2026-10-30), 59 distinct stocks. Scoring rules:
`backend/app/services/forward_scoring.py`. Exit diagnosis:
`backend/scripts/forward_exit_diagnosis.py` → `results.json` (this folder).
Strategy: `v1.1-momentum-eqwt-regime-trend5d`, unchanged by any of this.

## Verdict

**No evidence of stock-selection skill.** Against an equal-weight basket of
every stock in the universe, over the identical holding windows, the clean
record's 20-session excess after costs is **−0.04%** (95% interval −3.19 to
+3.11). The +3.73% "beat" over NIFTY200 Momentum 30 is real arithmetic but not
skill: the picks are smaller stocks, and in these weeks smaller stocks as a
whole fell less than the large-cap indices. Random picks from the same pool
did as well.

This agrees with the September audit verdict (Measurement FAIL / Edge BLOCKED /
Promotion BLOCKED). It does not overturn it in either direction: 6 entry weeks
in one falling market cannot establish or rule out an edge.

## Before and after

| | Old `/track-record` | Realistic, clean record |
|---|---|---|
| Unit | 296 signals | 66 positions in 45 stocks (145 repeat calls absorbed) |
| Entry | previous close (`reference_close`) | limit at `entry_high`: the open if at or below it, else `entry_high` if reached, else a counted miss |
| Costs | none | 0.24% round trip |
| Benchmark window | stock one session longer than NIFTY | identical: fill-day open → exit-day close |
| Benchmarks | NIFTY 50 | NIFTY 50, NIFTY200 Momentum 30 (MOM30IETF), equal-weight universe |
| Days included | all 37 | 27 (10 flagged days scored separately) |
| 20-session return | +0.55% (from ref. close), −1.08% (from entry) | **−0.02%** net, n=37 |
| 20-session excess | +4.52% vs NIFTY 50 | +3.40% vs NIFTY 50, +3.73% vs Momentum 30, **−0.04% vs universe** |
| Stops vs targets | 63 / 13 ("ever touched", any time, every signal) | **7 / 2**, plus 31 time exits (first touch, 20 sessions, per position) |

Clean record by horizon (net of costs; excess = mean of per-position differences):

| Sessions | n | Mean net | Median | Win rate | Profit factor | vs NIFTY 50 | vs Momentum 30 | vs universe |
|---|---|---|---|---|---|---|---|---|
| 1 | 60 | −0.46% | +0.86% | 58.3% | 0.81 | −0.51 | −1.10 | −1.16 |
| 5 | 43 | −0.23% | −1.12% | 39.5% | 0.93 | +0.48 | +0.15 | −1.10 |
| 10 | 43 | +0.86% | +1.46% | 53.5% | 1.24 | +2.46 | +2.55 | +0.41 |
| 20 | 37 | −0.02% | −0.73% | 45.9% | 1.00 | +3.40 | +3.73 | −0.04 |

Including the 10 flagged days, every figure is worse (20 sessions: −1.58% net,
−1.71% vs universe), because those calls were priced off sessions their
followers could not trade.

## Days excluded from the clean record

Built from their own session's close: 08-14, 08-25, 09-15, 10-22, 10-28.
Published after the 15:30 close: 08-14, 08-18, 08-20, 09-15, 10-22, 10-28.
Dated on a closed day: 08-15, 08-16, 09-14. (Ten dates; some carry two flags.)

**2026-10-28 was a normal session**, not a short Muhurat one: median volume
0.82× and range 0.85× each stock's 20-day median. Diwali 2026 is on 8 November.
Its problem is the shortlist, published at 20:19 IST from that day's close.

## Why positions stop out

**The stop is not misfiring.** For each of the 37 positions with a complete
20-session window, 2,000 paths were drawn by resampling that stock's own last
250 daily bars with the drift removed, then run through the same exit rule.

| | Stop | Target | 20-session time exit |
|---|---|---|---|
| Observed | 5 | 1 | 31 |
| Expected with no edge | 5.8 | 1.6 | 29.6 |
| No-edge 90% range | 2–10 | 0–4 | |

The split is what a coin-flip stock produces with a stop 4 ATR below and a
target 8 ATR above. The old "83% stop rate" counted every repeated signal and
any touch at any time.

**Entries were extended, but extension does not predict the outcome.** The
median call came 1.79 ATR above its 20-session average after a 12.0% 20-session
run-up. The rank correlation between extension and 20-session net return is
+0.03; the thirds go +0.97% / −1.96% / +0.84%.

**No alternative exit is distinguishable from the published one.** In-sample,
same calls, chosen before looking; every interval includes zero:

| Exit rule | Positions | Mean net | Win rate | PF | Excess vs universe (95% interval) |
|---|---|---|---|---|---|
| As published (4 / 8 ATR) | 37 | −0.24% | 45.9% | 0.96 | −0.11 (−2.90, +2.75) |
| Tighter, 2 / 4 ATR | 54 | −1.13% | 33.3% | 0.79 | −1.14 (−4.14, +1.74) |
| Middle, 3 / 6 ATR | 44 | −2.14% | 36.4% | 0.67 | −1.87 (−4.61, +1.67) |
| Symmetric, 4 / 4 ATR | 45 | −0.84% | 42.2% | 0.86 | −1.02 (−4.42, +2.39) |
| Stop at 20-session low | 38 | +0.59% | 42.1% | 1.13 | +0.64 (−1.05, +2.54) |
| Out after 5 sessions if below fill | 48 | +0.03% | 31.2% | 1.01 | +0.16 (−3.12, +3.38) |
| No stop or target, hold 20 | 36 | +1.79% | 47.2% | 1.45 | +1.75 (−2.64, +4.06) |

Tighter stops are consistently worse, as the walk-forward research found
(Phases 11–12). "No levels" ranks best here, which is what the maximum of seven
noisy estimates does; adopting it on this evidence would be fitting noise.
V1's exits stay frozen and change only through the walk-forward research,
after backtest defects #19 and #20 are fixed.

## Limits of these numbers

- 37 complete positions, opened in 6 calendar weeks, all in one falling market.
- Intervals resample whole entry weeks, but 20-session windows also overlap
  across weeks, so even these intervals are too narrow.
- The NIFTY200 Momentum 30 figures use an ETF's prices (the index itself has
  no history on Yahoo): fees and tracking error included, as an investor
  would have experienced them.
- Fills at the open assume an order placed within 15 minutes of the 09:15
  publication gets roughly the opening price.
- Costs are a statutory floor; DP charges on small positions add more.

## Data defects found along the way

- **No-trade placeholder bars.** 4,265 rows that are a flat, zero-volume copy
  of the previous close, on six 2026 NSE holidays (01-15, 05-01, 05-28, 06-26,
  09-14, 10-02; no bhavcopy for any) and on 2025-03-18, a day NSE traded,
  where 632 of 641 stocks lost their real session. The ingest now drops them;
  `scripts/remove_placeholder_bars.py` removes the stored ones. The Yahoo
  feed still returns placeholders for 2025-03-18, so that session stays
  missing.
- **ICICIPRULI → ICICILIFE** (same ISIN) and **MOREPENLAB, HFCL** moved to
  NSE's BE segment; MOREPENLAB was published on 10-28. The catalogue had not
  been refreshed since 08-14.
- **Paper-equity snapshots** have failed every night since 2026-09-09 (a
  missing `Decimal` import). The gap is left as a gap.
