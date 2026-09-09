# Verifying the audited code against production data

Run 2026-09-10, read-only, against the live Neon database.

Every fix in this audit had been tested against SQLite fixtures and a throwaway
Postgres with synthetic rows. That proves the logic; it does not prove the
logic survives 500 real stocks, 1,084,220 real bars, and real NULLs. This run
does.

**Method.** A throwaway container created from the *same image* the production
backend runs, with the audited `app/` copied over it and the production env
file attached, then removed. The running backend was never touched and nothing
was written.

```
as_of = 2026-09-09

WARNING  momentum: 2 stock(s) excluded — corporate-action discontinuity
         in the lookback window: TDPOWERSYS, TRENT

1. momentum ranking          : 465 of 500 stocks scored
   top 3                     : [('CUPID', 100.0), ('HFCL', 99.8), ('INDSWFTLAB', 99.6)]

2. realised risk:reward      : 60 computed, 0 rejected as invalid setups
   realised  min/median/max  : 1.666 / 1.667 / 1.669
   nominal (what was shown)  : 2.0
   overstatement at median   : 1.20x
   examples                  : [('360ONE', 1.67, 'atr'), ('AARTIIND', 1.67, 'atr'),
                                ('AARTIPHARM', 1.67, 'atr'), ('ABB', 1.67, 'atr')]

3. AI account P&L identity
   capital                   : 1000000.00
   cash                      :  636941.57
   market_value              :  371468.35
   equity                    : 1008409.92
   realized / unrealized     :   13746.20 / -5336.28
   RESIDUAL (must be 0)      : 0.00
   all Decimal?              : True
   open holdings             : 3

4. mark provenance           : {'tape': 3}
   unpriced (risk checks off): none
```

## What each line establishes

**1.** The corporate-action gate behaves on real data exactly as measured
earlier: two exclusions, both the ones whose discontinuity falls inside the
current 12-month window, and 465 stocks still ranked. It is not quietly
removing a tenth of the universe, and the exclusion is logged rather than
silent.

**2.** The risk:reward overstatement is confirmed on live stocks, not just on
a synthetic series. Across 60 real names the realised ratio is 1.666–1.669
against a published 2.00 — a consistent **1.20x overstatement on every signal**.
All 60 used the ATR stop, as expected since V1 disables the support stop; the
4x case measured earlier belongs to the `recommendations` path, which uses the
defaults.

**3.** The P&L identity holds **exactly** on the real, live AI trading book —
not on a fixture. `equity == capital + realized + unrealized` with a residual
of `0.00`, every figure a `Decimal`.

**4.** All three open positions were priced off the live tape, so stop and
target checks are currently operating on real prices rather than the previous
close. That is the healthy state; the point of the provenance work is that the
degraded state now says so.
