"""Phase 25's ML ranker (app/ml/ranker.py).

What has to hold for its backtest to mean anything: features at a month-end
use no later bar, no training row's label reaches into the fold it is used
for, a corporate-action step never becomes a label, the model is
deterministic, and the plumbing from features to labels is the right way
round — a planted, persistent signal must be learned.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.ml import ranker

N_DAYS = 520


def _calendar(n=N_DAYS, start=date(2023, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _frames(n_stocks=120, seed=1, drift_spread=0.002):
    """Each stock has its own persistent daily drift, so past winners keep
    winning: the signal a working ranker must find."""
    rng = np.random.default_rng(seed)
    cal = _calendar()
    mu = rng.uniform(-drift_spread, drift_spread, n_stocks)
    market = rng.normal(0.0003, 0.008, N_DAYS - 1)
    frames = {}
    for i in range(n_stocks):
        r = market + mu[i] + rng.normal(0, 0.01, N_DAYS - 1)
        c = 100 * np.exp(np.concatenate([[0.0], np.cumsum(r)]))
        frames[i + 1] = pd.DataFrame({"date": cal, "close": c,
                                      "volume": rng.uniform(5e5, 2e6, N_DAYS)})
    bench = pd.DataFrame({"date": cal, "close": 20000 * np.exp(np.concatenate([[0.0], np.cumsum(market)]))})
    return cal, frames, bench


def _rank_corr(a, b) -> float:
    ra, rb = pd.Series(a).rank().to_numpy(), pd.Series(b).rank().to_numpy()
    return float(np.corrcoef(ra, rb)[0, 1])


def test_features_need_a_year_and_a_day_of_history():
    cal, frames, bench = _frames(n_stocks=1)
    s = ranker.prepare_series(frames, bench)[1]
    assert ranker.features_at(s.closes[:252], s.volumes[:252], s.bench[:252]) is None
    f = ranker.features_at(s.closes[:253], s.volumes[:253], s.bench[:253])
    assert set(f) == set(ranker.FEATURES)
    assert all(np.isfinite(v) for v in f.values())


def test_features_at_a_month_end_do_not_change_when_only_later_bars_do():
    cal, frames, bench = _frames(n_stocks=20)
    d0 = ranker.month_ends(cal)[13]
    # 20% lower after d0: under the 25% floor of the corporate-action check, so
    # the rows survive and only their labels should change. (Halving would be
    # read, correctly, as an unadjusted 1:2 split and drop every row.)
    later = {sid: df.assign(close=np.where(df["date"] > d0, df["close"] * 0.8, df["close"]))
             for sid, df in frames.items()}
    universe = lambda d: list(frames)  # noqa: E731
    a = ranker.training_rows(ranker.prepare_series(frames, bench), cal, [d0], universe)
    b = ranker.training_rows(ranker.prepare_series(later, bench), cal, [d0], universe)
    assert len(a) == 20
    pd.testing.assert_frame_equal(a[list(ranker.FEATURES)], b[list(ranker.FEATURES)])


def test_a_label_window_is_the_next_21_sessions_and_never_reaches_the_fold():
    cal, frames, bench = _frames(n_stocks=10)
    rows = ranker.training_rows(ranker.prepare_series(frames, bench), cal, ranker.month_ends(cal),
                                lambda d: list(frames))
    for me, le in set(zip(rows["month_end"], rows["label_end"])):
        assert cal.index(le) - cal.index(me) == ranker.HORIZON
    fold_start = ranker.month_ends(cal)[-3]
    kept = ranker.rows_before(rows, fold_start)
    assert not kept.empty and (kept["label_end"] < fold_start).all()
    assert len(kept) < len(rows), "rows whose label overlaps the fold must be dropped"


def test_a_corporate_action_step_never_becomes_a_label():
    cal, frames, bench = _frames(n_stocks=10)
    d0 = ranker.month_ends(cal)[13]
    i0 = cal.index(d0)
    c = frames[3]["close"].to_numpy().copy()
    k = i0 + 6  # inside d0's label window
    c[k:] = c[k:] * (c[k - 1] / c[k]) / 2  # an exact 1:2 step, as an unadjusted split prints
    frames[3] = frames[3].assign(close=c)
    rows = ranker.training_rows(ranker.prepare_series(frames, bench), cal, ranker.month_ends(cal),
                                lambda d: list(frames))
    assert 3 not in set(rows[rows["month_end"] >= d0].index)
    assert 3 in set(rows[rows["month_end"] < d0].index), "rows wholly before the step are fine"


def test_the_ranker_learns_a_planted_persistent_signal_and_is_deterministic():
    cal, frames, bench = _frames()
    series = ranker.prepare_series(frames, bench)
    ends = ranker.month_ends(cal)
    rows = ranker.training_rows(series, cal, ends, lambda d: list(frames))
    test_end = ends[-3]  # the last month-end with a full 21-session label after it
    train = ranker.rows_before(rows, test_end)
    model = ranker.fit(train)

    held_out = rows[rows["month_end"] == test_end]
    pred = model.predict(held_out[list(ranker.FEATURES)].to_numpy())
    assert _rank_corr(pred, held_out["label"]) > 0.2

    again = ranker.fit(train).predict(held_out[list(ranker.FEATURES)].to_numpy())
    assert np.array_equal(pred, again)


def test_the_score_fn_ranks_from_frames_as_they_stood():
    cal, frames, bench = _frames()
    series = ranker.prepare_series(frames, bench)
    ends = ranker.month_ends(cal)
    model = ranker.fit(ranker.training_rows(series, cal, ends[:-3], lambda d: list(frames)))
    as_of = ends[-3]
    bounded = {sid: df[df["date"] <= as_of] for sid, df in frames.items()}
    scores = ranker.make_score_fn(model)(bounded, bench[bench["date"] <= as_of], as_of)
    assert set(scores) == set(frames)
    rows = ranker.training_rows(series, cal, [as_of], lambda d: list(frames))
    expected = model.predict(rows[list(ranker.FEATURES)].to_numpy())
    assert [scores[sid] for sid in rows.index] == pytest.approx(list(expected))
