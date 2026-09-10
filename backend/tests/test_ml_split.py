"""The ML train/validation/test split must not leak the answer.

train.py's docstring says shuffling "would let the model train on rows
chronologically after some of its own test rows, which is exactly the kind of
look-ahead this whole project has been careful to avoid everywhere else". It
did not shuffle — and it leaked anyway, in two other ways.
"""

import numpy as np
import pandas as pd
import pytest

from app.ml.features import TARGET_HORIZON_DAYS
from app.ml.train import PURGE_DAYS, _split


def _panel(n_dates=400, per_date=50, ragged=True):
    """A stock-by-date panel shaped like the real one.

    Ragged on purpose: the real panel does not have the same number of rows on
    every date, because stocks enter the universe at different times and a
    stock with a missing bar drops out of that day. A perfectly rectangular
    panel can make a positional cut land exactly on a date boundary by
    coincidence, which would hide the defect this file is about.
    """
    dates = pd.date_range("2020-01-01", periods=n_dates, freq="B").date
    rng = np.random.default_rng(5)
    rows = []
    for i, d in enumerate(dates):
        k = per_date if not ragged else int(rng.integers(max(2, per_date - 8), per_date + 1))
        rows.extend({"date": d, "stock_id": j, "target": float(j % 2)} for j in range(k))
    return pd.DataFrame(rows)


def test_no_date_appears_in_more_than_one_split():
    """The first defect: a positional cut on a panel with ~500 rows per day
    lands INSIDE a day, putting the same trading date — and the same benchmark
    forward return — in train and validation at once."""
    train, val, test = _split(_panel())

    d_train, d_val, d_test = (set(p["date"]) for p in (train, val, test))
    assert not (d_train & d_val), "a date is in both train and validation"
    assert not (d_val & d_test), "a date is in both validation and test"
    assert not (d_train & d_test), "a date is in both train and test"


def test_the_gap_between_splits_covers_the_label_horizon():
    """The second defect: the target is the forward TARGET_HORIZON_DAYS return,
    so a row dated D is labelled by the price at D+20. Without a gap the tail of
    train is answered by prices inside validation."""
    train, val, test = _split(_panel())
    dates = sorted(set(_panel()["date"]))
    index = {d: i for i, d in enumerate(dates)}

    train_last = index[max(train["date"])]
    val_first = index[min(val["date"])]
    val_last = index[max(val["date"])]
    test_first = index[min(test["date"])]

    assert val_first - train_last >= PURGE_DAYS
    assert test_first - val_last >= PURGE_DAYS


def test_the_purge_is_the_target_horizon_not_an_arbitrary_number():
    """It is exactly how far a label reaches forward. A smaller gap would leave
    part of the leak; a larger one would throw away data for nothing."""
    assert PURGE_DAYS == TARGET_HORIZON_DAYS


def test_the_splits_stay_in_chronological_order():
    train, val, test = _split(_panel())
    assert max(train["date"]) < min(val["date"])
    assert max(val["date"]) < min(test["date"])


def test_every_split_is_non_empty_on_a_realistic_panel():
    """Purging must not starve a split on a panel the size of the real one
    (~1,900 trading days)."""
    train, val, test = _split(_panel(n_dates=1900, per_date=20))
    assert len(train) > 0 and len(val) > 0 and len(test) > 0
    assert len(train) > len(val) and len(train) > len(test)


def test_a_panel_too_short_to_purge_degrades_loudly_not_silently(caplog):
    """Better an unpurged split with a warning than three empty frames."""
    import logging

    with caplog.at_level(logging.WARNING):
        train, val, test = _split(_panel(n_dates=30, per_date=5))

    assert "ml.split.no_purge" in caplog.text
    assert len(train) > 0 and len(val) > 0 and len(test) > 0


def test_the_row_split_this_replaced_would_have_failed_these():
    """Pins what the old implementation did, so the regression is explicit."""
    df = _panel().sort_values("date").reset_index(drop=True)
    n = len(df)
    old_train, old_val = df.iloc[: int(n * 0.70)], df.iloc[int(n * 0.70) : int(n * 0.85)]

    overlap = set(old_train["date"]) & set(old_val["date"])
    assert overlap, "the positional split did not cut mid-date on this panel"

    # And it purged nothing: train ran right up to where validation began.
    assert max(old_train["date"]) >= min(old_val["date"])
