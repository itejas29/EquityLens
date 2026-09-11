# Automatic split repair never worked on a single symbol

Found 2026-09-11, when PGIL went 2:1 (ex-date 2026-09-11) on the day this
audit's own repair tooling was being verified.

## What happened

The 20:00 IST ingest that evening did the first half correctly. The
restatement detector, added earlier in this audit, compared the overlap window
against disk and fired:

```
PGIL  CORPORATE_ACTION  history restated at 2026-09-01: stored 2348.00 vs
                        fetched 1174.00 (x2.0000) — re-pulling full history
PGIL  NOT_FOUND         no data returned
```

The re-pull it queued then failed, and was reported as the symbol not existing.

## Cause

Every batched download in the codebase extracted one ticker's frame like this:

```python
df = raw[ticker] if len(tickers) > 1 else raw
```

That assumes a single-ticker `yf.download(group_by="ticker")` returns flat
columns. On yfinance 1.6 it does not. Reproduced on the production image:

```
single-ticker raw columns: MultiIndex  [('PGIL.NS','Open'), ('PGIL.NS','High'), ...]
_download_full_batch(["PGIL"])          -> {}
_download_full_batch(["PGIL", "TCS"])   -> {'PGIL': 2475, 'TCS': 2475}
_download_incremental_batch(["PGIL"])   -> {}
```

With one ticker, `df` was the whole MultiIndex frame, `df["Close"]` raised
`KeyError`, the `except (KeyError, IndexError): continue` swallowed it, and the
symbol went down as NOT_FOUND.

A restatement re-pull is almost always a batch of **one** symbol, so the
self-healing path failed in exactly the case it was built for. It had never
worked in production. The earlier tests passed because they mocked the
downloaders out completely and never saw a real yfinance return shape.

## Where the pattern was

Five copies: both ingest downloaders (`_download_full_batch`,
`_download_incremental_batch`), the universe build (`universe._download_batch`),
the live-price poller in `core/scheduler.py`, and
`scripts/repair_price_history.py`. The last one was written during this audit;
it worked only because its full re-pull goes through `Ticker.history`, a
different code path.

## Fixed

`market_data.ticker_frame(raw, ticker)` handles both shapes and replaces all
five. A ticker missing from a MultiIndex result returns `None`, never another
symbol's frame.

`tests/test_ticker_frame.py` builds frames the way yfinance 1.6 actually
returns them and drives all three batch downloaders with a batch of one.
Negative control: putting the old pattern back at the call sites fails the
batch-of-one tests.

## A second defect on the same path

The re-pull, had it run, would still have been incomplete. It fetched
`HISTORY_PERIOD` (10y), which starts 2016-09-12, while stored series start
2016-08-16. Upsert only rewrites the dates it receives, so 18 bars would have
stayed on the pre-split basis and the gap would have moved to 2016 instead of
disappearing. Re-pulls now use `RESTATEMENT_REPULL_PERIOD = "max"`; PGIL's
provider history reaches back to 2007.

## Exposure

PGIL had no published signals since 2026-08-01 and no paper positions, so
nothing live was priced off the mismatched basis. Its stored series is on the
old basis *consistently* (the re-pull wrote nothing), so momentum is unaffected
until new-basis bars are appended.
