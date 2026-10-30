"""Phase 25's candidate rankings (services/ranking_candidates.py) and their
wiring into the backtest's ranking step.

Each test pins the property that makes a candidate what the literature says it
is — residual momentum ignores what the market did, the 52-week score is about
distance from the high, the blend rewards calm — plus the property every
ranking must have: it cannot see a bar after the decision date.
"""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from app.core.canonical import first_difference
from app.core.v1_strategy import V1
from app.models.price_history import PriceHistory
from app.models.stock import Stock
from app.services import backtest as bt
from app.services.backtest_scoring import compute_point_in_time_universe
from app.services.ranking_candidates import (
    high_52w_scores,
    momentum_lowvol_blend,
    residual_momentum_scores,
    volatility_scores,
)

N = 300  # sessions of history: more than the 253 every score needs


def _days(n, start=date(2024, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _frame(log_returns, days) -> pd.DataFrame:
    c = 100.0 * np.exp(np.concatenate([[0.0], np.cumsum(log_returns)]))
    return pd.DataFrame({"date": days, "open": c, "high": c * 1.01, "low": c * 0.99,
                         "close": c, "volume": np.full(len(c), 1_000_000.0)})


@pytest.fixture
def market():
    rng = np.random.default_rng(7)
    days = _days(N + 1)
    # A strong rally: +0.3% a day on average, so a high-beta stock looks great
    # on raw momentum without doing anything a market tracker would not.
    rm = 0.003 + rng.normal(0, 0.008, N)
    bench = pd.DataFrame({"date": days, "close": 20000 * np.exp(np.concatenate([[0.0], np.cumsum(rm)]))})
    return days, rm, bench, rng


def test_residual_momentum_ignores_gains_that_only_track_the_market(market):
    days, rm, bench, rng = market
    noise = lambda: rng.normal(0, 0.002, N)  # noqa: E731
    frames = {
        1: _frame(2.0 * rm + noise(), days),          # pure beta: twice the market, no own drift
        2: _frame(1.0 * rm + 0.001 + noise(), days),  # the market plus its own +0.1% a day
    }
    raw = {sid: f["close"].iloc[-1] / f["close"].iloc[-252] - 1 for sid, f in frames.items()}
    assert raw[1] > raw[2], "fixture: the beta stock should lead on raw return"
    scores = residual_momentum_scores(frames, bench)
    assert scores[2] > scores[1], "residual momentum should rank the stock with its own drift first"
    # The score is a sum over ~231 sessions divided by one session's spread, so
    # pure noise lands around ±15; the stock with its own drift sits near +115.
    assert abs(scores[1]) < 0.5 * scores[2], "no own drift, so far below the stock that has it"


def test_the_intercept_is_not_removed_so_steady_own_drift_scores_positive(market):
    """Subtracting a fitted intercept would force the window's residuals to sum
    to zero, leaving minus the latest month: a steady outperformer would score
    about zero, or below it, instead of high."""
    days, rm, bench, rng = market
    frames = {1: _frame(rm + 0.001 + rng.normal(0, 0.004, N), days),
              2: _frame(rm + rng.normal(0, 0.004, N), days)}
    scores = residual_momentum_scores(frames, bench)
    assert scores[1] > 5.0
    assert scores[1] > scores[2]


def test_residual_momentum_skips_the_latest_month(market):
    days, rm, bench, rng = market
    late = rm + rng.normal(0, 0.004, N)
    late[-21:] += 0.01  # all of its outperformance is in the last 21 sessions
    steady = rm + 0.001 + rng.normal(0, 0.004, N)
    scores = residual_momentum_scores({1: _frame(late, days), 2: _frame(steady, days)}, bench)
    assert scores[2] > scores[1]


def test_52_week_score_is_distance_from_the_highest_close():
    days = _days(N + 1)
    up = _frame(np.full(N, 0.001), days)                       # closes at its high
    fell = _frame(np.r_[np.full(N - 50, 0.002), np.full(50, np.log(0.8) / 50)], days)  # 20% off its high
    scores = high_52w_scores({1: up, 2: fell, 3: up.tail(200)})
    assert scores[1] == pytest.approx(1.0)
    assert scores[2] == pytest.approx(0.8, rel=1e-6)
    assert 3 not in scores, "under a year of history is left out, not scored on less"


def test_the_blend_rewards_lower_volatility_at_equal_momentum():
    mom = {1: 0.30, 2: 0.30, 3: 0.10}
    vol = {1: 0.010, 2: 0.030, 3: 0.010}
    blend = momentum_lowvol_blend(mom, vol)
    assert blend[1] > blend[2], "same momentum, calmer stock first"
    assert blend[1] > blend[3], "same calm, stronger momentum first"
    assert momentum_lowvol_blend({1: 0.3}, {1: 0.01}) == {}


def test_volatility_is_the_daily_log_return_standard_deviation():
    days = _days(N + 1)
    r = np.tile([0.01, -0.01], N // 2)
    assert volatility_scores({1: _frame(r, days)})[1] == pytest.approx(np.std(r[-252:], ddof=1))


# ------------------------------------------------- wiring into the backtest --

def _universe(rng, days, n=6):
    rm = rng.normal(0.0005, 0.008, len(days) - 1)
    bench = pd.DataFrame({"date": days, "close": 20000 * np.exp(np.concatenate([[0.0], np.cumsum(rm)]))})
    frames = {sid: _frame(rm * (0.5 + 0.2 * sid) + 0.0003 * sid + rng.normal(0, 0.006, len(rm)), days)
              for sid in range(1, n + 1)}
    return frames, bench


@pytest.mark.parametrize("engine", ["momentum_residual", "high_52w", "momentum_lowvol"])
def test_each_engine_ranks_by_its_own_score(engine):
    frames, bench = _universe(np.random.default_rng(3), _days(N + 1))
    snap = compute_point_in_time_universe(frames, bench, replace(V1, ranking_engine=engine))
    expected = {"momentum_residual": lambda: residual_momentum_scores(frames, bench),
                "high_52w": lambda: high_52w_scores(frames),
                "momentum_lowvol": None}[engine]
    if expected is None:
        # The blend's momentum leg is V1's own, through the same code path.
        v1 = compute_point_in_time_universe(frames, bench, V1)
        mom = {sid: s.overall_score for sid, s in v1.items()}
        scores = momentum_lowvol_blend(mom, volatility_scores(frames))
    else:
        scores = expected()
    # Compared as ranks, so stocks tied on a score (two at their 52-week high)
    # tie in the snapshot too rather than in an arbitrary order.
    assert pd.Series(scores).rank().to_dict() == pd.Series(
        {sid: s.overall_score for sid, s in snap.items()}).rank().to_dict()
    assert all(0 < snap[sid].overall_score <= 100 for sid in snap)


def test_the_external_engine_needs_a_score_fn_and_sees_only_bounded_frames():
    frames, bench = _universe(np.random.default_rng(5), _days(N + 1))
    params = replace(V1, ranking_engine="external")
    with pytest.raises(ValueError):
        compute_point_in_time_universe(frames, bench, params)

    seen = {}

    def score_fn(fr, b, as_of):
        seen["latest"] = max(df["date"].max() for df in fr.values())
        return {sid: -sid for sid in fr}

    snap = compute_point_in_time_universe(frames, bench, params, as_of=frames[1]["date"].iloc[-1],
                                          score_fn=score_fn)
    assert seen["latest"] == frames[1]["date"].iloc[-1]
    assert max(snap, key=lambda sid: snap[sid].overall_score) == 1, "ranked by the supplied scores"


@pytest.mark.parametrize("engine", ["momentum_residual", "high_52w", "momentum_lowvol"])
def test_signals_before_a_cutoff_do_not_change_when_only_later_bars_do(db_session, monkeypatch, engine):
    """The run_backtest-level look-ahead check test_backtest_execution makes
    for V1, for each Phase 25 engine. Noisy prices, because residual momentum
    and volatility are undefined on the perfectly smooth paths used there."""
    rng = np.random.default_rng(11)
    days = _days(470, start=date(2024, 6, 3))
    frames, bench = _universe(rng, days)
    for sid, f in frames.items():
        st = Stock(symbol=f"S{sid}", is_active=True, sector=["Tech", "Banks", "Energy"][sid % 3])
        db_session.add(st)
        db_session.flush()
        for row in f.itertuples():
            db_session.add(PriceHistory(stock_id=st.id, date=row.date, open=Decimal(f"{row.open:.2f}"),
                                        high=Decimal(f"{row.high:.2f}"), low=Decimal(f"{row.low:.2f}"),
                                        close=Decimal(f"{row.close:.2f}"), volume=int(row.volume)))
    db_session.flush()
    monkeypatch.setattr(bt, "fetch_price_history", lambda *a, **k: bench.copy())
    start, end, cutoff = days[-90], days[-1], days[-45]
    params = replace(V1, ranking_engine=engine, trend_confirm_days=0)
    recorded: dict[str, dict] = {"a": {}, "b": {}}
    real = bt.compute_point_in_time_universe

    def recorder(bucket):
        def wrapped(*args, **kwargs):
            out = real(*args, **kwargs)
            recorded[bucket][str(args[4])] = {str(k): v for k, v in out.items()}
            return out
        return wrapped

    def run(bucket):
        monkeypatch.setattr(bt, "compute_point_in_time_universe", recorder(bucket))
        return bt.run_backtest(db_session, bt.BacktestConfig(
            start_date=start, end_date=end, initial_capital=1_000_000.0, risk_appetite="moderate",
            horizon_days=V1.horizon_days, rebalance_frequency=V1.rebalance_frequency, params=params))

    a = run("a")
    for ph in db_session.query(PriceHistory).filter(PriceHistory.date > cutoff).all():
        for col in ("open", "high", "low", "close"):
            setattr(ph, col, getattr(ph, col) * Decimal("0.5"))
    db_session.flush()
    b = run("b")

    before = [d for d in recorded["a"] if date.fromisoformat(d) <= cutoff]
    assert before, "no rebalance fell before the cutoff — the test checked nothing"
    for d in before:
        diff = first_difference(recorded["a"][d], recorded["b"][d])
        assert diff is None, f"signal on {d} changed when only bars after {cutoff} changed:\n{diff.describe()}"
    entries = lambda r: sorted((t.symbol, t.entry_date, t.entry_price) for t in r.trade_log  # noqa: E731
                               if t.entry_date <= cutoff)
    assert entries(a), "no entries before the cutoff — there was nothing to compare"
    assert entries(a) == entries(b)
