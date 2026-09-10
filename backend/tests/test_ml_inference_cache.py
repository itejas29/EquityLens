"""The inference cross-section cache: expiry and the stampede.

Both defects were dormant — nothing is served while the ROC-AUC gate is unmet
(the last real training run measured 0.5369 against a 0.55 bar) — and both
would have gone live the moment a retrain cleared it. That is the interesting
part: a defect behind a feature flag is not a fixed defect, it is an armed one.
"""

import threading
from datetime import date, timedelta

import pandas as pd
import pytest

from app.ml import predict


@pytest.fixture(autouse=True)
def clean_cache():
    predict._frame_cache.clear()
    yield
    predict._frame_cache.clear()


def test_the_frame_is_built_once_not_per_call(db_session, monkeypatch):
    calls = {"n": 0}

    def _build(db):
        calls["n"] += 1
        return pd.DataFrame({"x": [1]}, index=[1])

    monkeypatch.setattr(predict, "build_latest_features_frame", _build)
    for _ in range(5):
        predict._latest_frame(db_session)
    assert calls["n"] == 1


def test_a_stale_frame_does_not_outlive_the_day(db_session, monkeypatch):
    """It never expired, and invalidate_feature_cache() had ZERO callers. On an
    always-on box the process runs for weeks, so a served probability could have
    been computed from a cross-section weeks old."""
    calls = {"n": 0}
    monkeypatch.setattr(predict, "build_latest_features_frame",
                        lambda db: (calls.__setitem__("n", calls["n"] + 1),
                                    pd.DataFrame({"x": [1]}, index=[1]))[1])

    predict._latest_frame(db_session)
    assert calls["n"] == 1

    # Pretend the cached frame was built yesterday.
    predict._frame_cache["date"] = date.today() - timedelta(days=1)
    predict._latest_frame(db_session)
    assert calls["n"] == 2, "yesterday's cross-section was served again today"


def test_concurrent_cold_misses_build_the_frame_once(db_session, monkeypatch):
    """The check-then-fill was unguarded, so N worker threads arriving together
    on a cold cache would each build the whole panel — up to 24 concurrent
    full-universe builds plus 24 NIFTY fetches, on a box with four prior memory
    incidents."""
    calls = {"n": 0}
    lock = threading.Lock()
    start = threading.Barrier(8)

    def _build(db):
        with lock:
            calls["n"] += 1
        # Long enough that an unguarded check-then-fill would definitely race.
        import time
        time.sleep(0.05)
        return pd.DataFrame({"x": [1]}, index=[1])

    monkeypatch.setattr(predict, "build_latest_features_frame", _build)

    def worker():
        start.wait(timeout=10)
        predict._latest_frame(db_session)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert calls["n"] == 1, f"the cross-section was built {calls['n']} times concurrently"


def test_invalidation_clears_both_the_frame_and_its_datestamp(db_session, monkeypatch):
    monkeypatch.setattr(predict, "build_latest_features_frame",
                        lambda db: pd.DataFrame({"x": [1]}, index=[1]))
    predict._latest_frame(db_session)
    assert "frame" in predict._frame_cache and "date" in predict._frame_cache

    predict.invalidate_feature_cache()
    assert "frame" not in predict._frame_cache
    assert "date" not in predict._frame_cache, "a stale datestamp would block the next rebuild"


def test_the_pipeline_actually_calls_the_invalidator():
    """It existed with a docstring saying "call after ingesting or refreshing
    stocks" and had no callers anywhere."""
    import inspect

    from app.services import incremental

    assert "invalidate_feature_cache()" in inspect.getsource(incremental)


def test_nothing_is_served_while_the_gate_is_unmet(db_session, monkeypatch):
    """The reason both defects were dormant. predict_probability returns before
    touching the cross-section when no model clears MIN_SERVABLE_ROC_AUC."""
    monkeypatch.setattr(predict, "_load_selected_model", lambda: None)

    def _explode(db):
        raise AssertionError("built the cross-section with no model served")

    monkeypatch.setattr(predict, "build_latest_features_frame", _explode)
    assert predict.predict_probability(db_session, 1) is None
