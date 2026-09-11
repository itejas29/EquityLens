"""ticker_frame and the downloaders that depend on it, fed frames shaped the
way yfinance actually returns them. The earlier tests mocked the downloaders
out entirely, which is exactly why a single-ticker batch could return nothing
for months without a test noticing."""
from unittest.mock import patch

import pandas as pd

from app.services.market_data import ticker_frame

FIELDS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
DATES = pd.DatetimeIndex(["2026-09-09", "2026-09-10"], name="Date")


def _yf16(tickers):
    """What yfinance 1.6 returns for group_by="ticker" — MultiIndex columns
    (ticker, field), EVEN WHEN only one ticker was requested. Verified on the
    production image 2026-09-11."""
    cols = pd.MultiIndex.from_tuples([(t, f) for t in tickers for f in FIELDS])
    data = [[100.0, 105.0, 95.0, 102.0, 102.0, 1000] * len(tickers)] * len(DATES)
    return pd.DataFrame(data, index=DATES, columns=cols)


def _flat():
    """The older single-ticker shape: flat columns."""
    return pd.DataFrame([[100.0, 105.0, 95.0, 102.0, 102.0, 1000]] * len(DATES),
                        index=DATES, columns=FIELDS)


def test_single_ticker_multiindex_is_unwrapped():
    df = ticker_frame(_yf16(["PGIL.NS"]), "PGIL.NS")
    assert df is not None and list(df["Close"]) == [102.0, 102.0]


def test_flat_single_ticker_is_returned_as_is():
    df = ticker_frame(_flat(), "PGIL.NS")
    assert df is not None and list(df["Close"]) == [102.0, 102.0]


def test_missing_ticker_in_multiindex_is_none_not_another_symbol():
    assert ticker_frame(_yf16(["TCS.NS"]), "PGIL.NS") is None


def test_empty_and_none_are_none():
    assert ticker_frame(pd.DataFrame(), "X.NS") is None
    assert ticker_frame(None, "X.NS") is None


def test_full_batch_of_one_symbol_returns_it():
    """The PGIL failure itself: a restatement re-pull is a batch of one."""
    from app.services import incremental
    with patch("app.services.market_data.download_batch_with_retry", return_value=_yf16(["PGIL.NS"])):
        out = incremental._download_full_batch(["PGIL"], "max")
    assert list(out) == ["PGIL"]


def test_incremental_batch_of_one_symbol_returns_it():
    from datetime import date
    from app.services import incremental
    with patch("app.services.market_data.download_batch_with_retry", return_value=_yf16(["PGIL.NS"])):
        out = incremental._download_incremental_batch(["PGIL"], date(2026, 9, 1), date(2026, 9, 10))
    assert list(out) == ["PGIL"]


def test_universe_batch_of_one_symbol_returns_it():
    from app.services import universe
    with patch("app.services.market_data.download_batch_with_retry", return_value=_yf16(["PGIL.NS"])):
        out = universe._download_batch(["PGIL"], "1mo")
    assert list(out) == ["PGIL"]


def test_multi_ticker_batch_still_separates_symbols():
    from app.services import incremental
    with patch("app.services.market_data.download_batch_with_retry",
               return_value=_yf16(["PGIL.NS", "TCS.NS"])):
        out = incremental._download_full_batch(["PGIL", "TCS"], "max")
    assert sorted(out) == ["PGIL", "TCS"]
