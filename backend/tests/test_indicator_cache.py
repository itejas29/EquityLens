"""A shared indicator cache must not change what a universe scores as.

The invariant: compute_point_in_time_universe returns exactly the same snapshot
whether its cache is fresh or was populated first by a DIFFERENT universe. Both
directions matter and failed differently:

- A later universe with names the first lacked: those names were silently
  unscoreable. This is what invalidated Phase 19's arms.
- A later universe narrower than the first: the extra cached names must not
  leak into its cross-sectional percentiles.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.core.canonical import first_difference
from app.core.v1_strategy import V1
from app.services.backtest_scoring import compute_point_in_time_universe

AS_OF = date(2026, 3, 2)


def _frames(ids, seed=7):
    rng = np.random.default_rng(seed)
    days = [d for d in (date(2024, 9, 2) + timedelta(n) for n in range(560)) if d.weekday() < 5 and d <= AS_OF]
    frames = {}
    for sid in ids:
        mu, sigma = rng.uniform(-0.0005, 0.0015), rng.uniform(0.01, 0.03)
        closes = 100 * np.exp(np.cumsum(mu + sigma * rng.standard_normal(len(days))))
        opens = np.concatenate([[100.0], closes[:-1]])
        frames[sid] = pd.DataFrame({
            "date": days, "open": opens,
            "high": np.maximum(opens, closes) * 1.01, "low": np.minimum(opens, closes) * 0.99,
            "close": closes, "volume": rng.integers(10_000, 1_000_000, len(days)),
        })
    bench = pd.DataFrame({"date": days, "close": 20000 * np.exp(np.cumsum(0.0004 + 0.008 * rng.standard_normal(len(days))))})
    return frames, bench


def _score(frames, bench, cache):
    return compute_point_in_time_universe(frames, bench, V1, cache, AS_OF)


def test_a_universe_scores_the_same_after_a_different_universe_filled_the_cache():
    all_frames, bench = _frames(range(1, 13))
    first = {sid: all_frames[sid] for sid in range(1, 9)}    # 1..8
    second = {sid: all_frames[sid] for sid in range(5, 13)}  # 5..12 — four names the first lacks

    fresh = _score(second, bench, None)
    cache: dict = {}
    _score(first, bench, cache)
    polluted = _score(second, bench, cache)

    assert set(polluted) == set(fresh), (
        f"names scoreable fresh but not after a different universe filled the cache: "
        f"{sorted(set(fresh) - set(polluted))}"
    )
    diff = first_difference({str(k): v for k, v in fresh.items()}, {str(k): v for k, v in polluted.items()})
    assert diff is None, diff.describe()


def test_a_narrower_universe_is_not_contaminated_by_a_wider_cache_entry():
    all_frames, bench = _frames(range(1, 13))
    wide = all_frames
    narrow = {sid: all_frames[sid] for sid in range(1, 6)}

    fresh = _score(narrow, bench, None)
    cache: dict = {}
    _score(wide, bench, cache)
    polluted = _score(narrow, bench, cache)

    assert set(polluted) == set(narrow) & set(fresh), "extra cached names leaked into a narrower universe"
    diff = first_difference({str(k): v for k, v in fresh.items()}, {str(k): v for k, v in polluted.items()})
    assert diff is None, diff.describe()


def test_the_cache_is_still_a_cache():
    """The fix must not turn caching off: a repeat call for the same universe
    and date must reuse the stored indicators rather than recompute them."""
    from app.services import backtest_scoring as bs

    all_frames, bench = _frames(range(1, 7))
    cache: dict = {}
    _score(all_frames, bench, cache)

    calls = []
    real = bs.compute_indicator_snapshot
    bs.compute_indicator_snapshot = lambda f, b: calls.append(len(f)) or real(f, b)
    try:
        _score(all_frames, bench, cache)
    finally:
        bs.compute_indicator_snapshot = real
    assert calls == [], f"a fully cached universe recomputed indicators for {calls} stock(s)"
