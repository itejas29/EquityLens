"""The realistic forward record (services/forward_scoring.py) and the
integrity flags it scores against (services/record_integrity.py).

Every case is built by hand around one call: zone 99-101 (so 1 ATR = 2),
the published 4-ATR stop at 92 and 8-ATR target at 116, published 09:15 IST.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.market_hours import IST
from app.services import forward_scoring as fs
from app.services.record_integrity import (
    CLOSED_DAY, PUBLISHED_AFTER_CLOSE, SAME_DAY_DATA, STALE_REFERENCE, PublishedDay, flag_day,
)

D0 = date(2026, 9, 1)  # a Tuesday


def _sessions(n, start=D0):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


SESSIONS = _sessions(40)


def _call(day=D0, stock_id=1, published=(9, 15), signal_id=1, reference=None):
    return fs.Call(
        signal_id=signal_id, date=day, stock_id=stock_id, symbol=f"S{stock_id}",
        entry_low=Decimal("99"), entry_high=Decimal("101"),
        stop_loss=Decimal("92"), target_price=Decimal("116"),
        reference_date=reference or (day - timedelta(days=1)),
        generated_at=datetime(day.year, day.month, day.day, *published, tzinfo=IST),
    )


def _bar(day, o, h, l, c):
    return fs.Bar(day, Decimal(str(o)), Decimal(str(h)), Decimal(str(l)), Decimal(str(c)))


def _flat_after(first_bar, n=25, price=100):
    """The fill bar, then n quiet sessions that touch neither stop nor target."""
    return [first_bar] + [_bar(d, price, price + 1, price - 1, price) for d in SESSIONS[1:1 + n]]


def _one(calls, bars, rule=fs.AS_PUBLISHED):
    campaigns, unfilled, absorbed = fs.build_campaigns(calls, bars, rule)
    return campaigns, unfilled, absorbed


# ------------------------------------------------------------------- fills --

def test_opening_inside_or_below_the_zone_fills_at_the_open():
    campaigns, _, _ = _one([_call()], {1: _flat_after(_bar(D0, 100.5, 102, 99.5, 101))})
    assert (campaigns[0].fill_price, campaigns[0].fill_how) == (Decimal("100.5"), "open")


def test_opening_above_the_zone_fills_at_entry_high_only_if_it_trades_down_to_it():
    filled, _, _ = _one([_call()], {1: _flat_after(_bar(D0, 102, 103, 100.8, 102))})
    assert (filled[0].fill_price, filled[0].fill_how) == (Decimal("101"), "limit")

    missed, unfilled, _ = _one([_call()], {1: _flat_after(_bar(D0, 103, 104, 101.5, 103))})
    assert missed == [] and unfilled == {"missed": 1}


def test_a_list_published_after_the_open_gets_no_opening_price():
    campaigns, _, _ = _one([_call(published=(11, 0))], {1: _flat_after(_bar(D0, 99.5, 100, 98, 99))})
    assert (campaigns[0].fill_price, campaigns[0].fill_how) == (Decimal("101"), "limit")


def test_opening_through_the_stop_is_not_a_trade():
    campaigns, unfilled, _ = _one([_call()], {1: _flat_after(_bar(D0, 91, 93, 90, 92))})
    assert campaigns == [] and unfilled == {"opened_below_stop": 1}


def test_a_call_whose_session_is_not_ingested_yet_is_pending_not_missed():
    bars = {1: [_bar(SESSIONS[0] - timedelta(days=3), 100, 101, 99, 100)]}
    campaigns, unfilled, _ = _one([_call(day=SESSIONS[0])], bars)
    assert campaigns == [] and unfilled == {"pending": 1}
    summary = fs.summarize(campaigns, unfilled, 0, {})
    assert summary["positions"]["fill_rate_pct"] is None, "a pending call is not an attempt"


# ------------------------------------------------------------------- exits --

def test_a_gap_through_the_stop_exits_at_the_open_not_the_stop():
    bars = [_bar(D0, 100, 101, 99, 100), _bar(SESSIONS[1], 90, 91, 88, 89)]
    c = _one([_call()], {1: bars})[0][0]
    assert (c.exit_reason, c.exit_price, c.exit_date) == ("stop", Decimal("90"), SESSIONS[1])


def test_a_bar_touching_both_levels_is_scored_as_the_stop_and_counted():
    bars = [_bar(D0, 100, 101, 99, 100), _bar(SESSIONS[1], 100, 117, 91, 100)]
    c = _one([_call()], {1: bars})[0][0]
    assert (c.exit_reason, c.exit_price, c.ambiguous) == ("stop", Decimal("92"), True)


def test_the_target_is_taken_at_the_target_and_a_gap_over_it_at_the_open():
    touched = [_bar(D0, 100, 101, 99, 100), _bar(SESSIONS[1], 110, 117, 109, 115)]
    gapped = [_bar(D0, 100, 101, 99, 100), _bar(SESSIONS[1], 118, 120, 117, 119)]
    assert _one([_call()], {1: touched})[0][0].exit_price == Decimal("116")
    assert _one([_call()], {1: gapped})[0][0].exit_price == Decimal("118")


def test_with_neither_level_reached_the_position_closes_at_the_20th_session():
    c = _one([_call()], {1: _flat_after(_bar(D0, 100, 101, 99, 100), price=103)})[0][0]
    assert (c.exit_reason, c.exit_date, c.held_sessions) == ("time", SESSIONS[20], 20)
    assert c.exit_price == Decimal("103")


def test_a_position_without_20_sessions_of_data_is_still_open():
    c = _one([_call()], {1: _flat_after(_bar(D0, 100, 101, 99, 100), n=7)})[0][0]
    assert c.exit_reason == "open" and not c.resolved


# --------------------------------------------------------------- campaigns --

def test_the_same_stock_published_again_while_held_is_one_position():
    calls = [_call(day=d, signal_id=i) for i, d in enumerate(SESSIONS[:5])]
    campaigns, _, absorbed = _one(calls, {1: _flat_after(_bar(D0, 100, 101, 99, 100))})
    assert len(campaigns) == 1 and absorbed == 4
    assert [c.date for c in campaigns[0].calls] == SESSIONS[:5]


def test_a_call_after_the_position_exits_opens_a_new_one():
    bars = [_bar(D0, 100, 101, 99, 100), _bar(SESSIONS[1], 90, 91, 88, 89)]
    bars += [_bar(d, 100, 101, 99, 100) for d in SESSIONS[2:30]]
    calls = [_call(day=D0, signal_id=1), _call(day=SESSIONS[3], signal_id=2)]
    campaigns, _, absorbed = _one(calls, {1: bars})
    assert len(campaigns) == 2 and absorbed == 0
    assert campaigns[1].fill_date == SESSIONS[3]


# ------------------------------------------------------- costs & benchmarks --

def test_returns_are_net_of_costs_and_compared_over_the_same_window():
    """Stock: fills at the open 100, closes 110 at session 20 -> +10% gross,
    +9.76% net. Benchmark: open 1000 on the fill day to close 1050 on the
    same 20th session -> +5%. Excess must be 4.76, not measured from any
    other day's price."""
    bars = [_bar(D0, 100, 101, 99, 100)] + [_bar(d, 100, 101, 99, 100) for d in SESSIONS[1:20]]
    bars.append(_bar(SESSIONS[20], 109, 111, 108, 110))
    campaigns, unfilled, absorbed = _one([_call()], {1: bars})
    bench = {d: (Decimal("990"), Decimal("995")) for d in SESSIONS[:21]}
    bench[D0] = (Decimal("1000"), Decimal("990"))       # only the OPEN of the fill day counts
    bench[SESSIONS[20]] = (Decimal("1040"), Decimal("1050"))

    summary = fs.summarize(campaigns, unfilled, absorbed, {"idx": fs.SeriesBenchmark(bench)})
    h20 = next(h for h in summary["horizons"] if h["horizon_sessions"] == 20)
    assert h20["mean_net_return_pct"] == pytest.approx(9.76)
    assert h20["vs"]["idx"]["mean_excess_pct"] == pytest.approx(4.76)


def test_the_universe_basket_is_the_equal_weight_mean_of_every_stock():
    basket = fs.BasketBenchmark.from_rows([
        (1, D0, Decimal("100"), Decimal("101")), (1, SESSIONS[5], Decimal("108"), Decimal("110")),
        (2, D0, Decimal("50"), Decimal("49")), (2, SESSIONS[5], Decimal("46"), Decimal("45")),
        (3, D0, Decimal("10"), Decimal("10")),  # no bar at the end of the window: not in it
        (4, SESSIONS[5], Decimal("7"), Decimal("7")),  # no bar at the start: not in it either
    ])
    assert basket.window(D0, SESSIONS[5]) == pytest.approx(0.0)  # open->close: (+10% + -10%) / 2
    assert basket.window(D0, SESSIONS[9]) is None


def test_a_missing_benchmark_is_reported_as_no_sample_not_as_zero():
    campaigns, unfilled, absorbed = _one([_call()], {1: _flat_after(_bar(D0, 100, 101, 99, 100))})
    summary = fs.summarize(campaigns, unfilled, absorbed, {"idx": fs.SeriesBenchmark({})})
    assert summary["horizons"][0]["vs"]["idx"] == {"sample": 0}


def test_profit_factor_is_undefined_without_a_losing_trade():
    assert fs._profit_factor([1.0, 2.0]) is None
    assert fs._profit_factor([3.0, -1.0, -2.0]) == 1.0


def test_the_interval_is_reproducible_and_needs_two_clusters():
    values, weeks = [1.0, -2.0, 3.0, 0.5, -1.0, 2.0], [1, 1, 2, 2, 3, 3]
    assert fs.bootstrap_mean_ci(values, weeks) == fs.bootstrap_mean_ci(values, weeks)
    assert fs.bootstrap_mean_ci(values, [1] * 6) is None


# --------------------------------------------------------- integrity flags --

def _day(d, gen_hm, refs):
    return PublishedDay(d, datetime(d.year, d.month, d.day, *gen_hm, tzinfo=IST), tuple(refs))


def test_a_list_built_from_its_own_session_is_flagged():
    assert SAME_DAY_DATA in flag_day(_day(SESSIONS[5], (9, 15), [SESSIONS[5]]), SESSIONS)


def test_a_list_published_at_or_after_the_close_is_flagged():
    assert PUBLISHED_AFTER_CLOSE in flag_day(_day(SESSIONS[5], (15, 30), [SESSIONS[4]]), SESSIONS)
    assert PUBLISHED_AFTER_CLOSE not in flag_day(_day(SESSIONS[5], (15, 29), [SESSIONS[4]]), SESSIONS)


def test_a_date_the_market_skipped_is_a_closed_day_but_today_is_not():
    friday = next(d for d in SESSIONS if d.weekday() == 4)
    saturday = friday + timedelta(days=1)
    assert CLOSED_DAY in flag_day(_day(saturday, (9, 15), [friday]), SESSIONS)
    beyond = SESSIONS[-1] + timedelta(days=1)
    assert CLOSED_DAY not in flag_day(_day(beyond, (9, 15), [SESSIONS[-1]]), SESSIONS)


def test_a_list_built_from_an_older_session_is_stale_but_stays_clean():
    flags = flag_day(_day(SESSIONS[5], (9, 15), [SESSIONS[3]]), SESSIONS)
    assert flags == {STALE_REFERENCE}


def test_flagged_days_are_scored_separately_and_listed():
    clean = _call(day=SESSIONS[1], signal_id=1, stock_id=1)
    late = _call(day=SESSIONS[2], signal_id=2, stock_id=2, published=(20, 5), reference=SESSIONS[2])
    bars = {1: [_bar(d, 100, 101, 99, 100) for d in SESSIONS], 2: [_bar(d, 100, 101, 99, 100) for d in SESSIONS]}
    record = fs.score_record([clean, late], bars, SESSIONS, {})
    assert record["clean_calls"] == 1 and record["published_calls"] == 2
    assert record["excluded_days"] == [
        {"date": SESSIONS[2].isoformat(), "flags": [PUBLISHED_AFTER_CLOSE, SAME_DAY_DATA]}
    ]
    assert record["clean"]["positions"]["opened"] == 1
    assert record["all_published"]["positions"]["opened"] == 2


# ------------------------------------------------------------------- API --

def test_the_realistic_record_endpoint_scores_stored_calls_and_caches(client, db_session, monkeypatch):
    from datetime import timezone

    from app.models.daily_signal import DailySignal
    from app.models.price_history import PriceHistory
    from app.models.stock import Stock

    # Benchmarks come from Yahoo in production; none here, which must read as
    # "no sample", not as a zero return.
    monkeypatch.setattr(fs, "load_benchmark", lambda symbol: fs.SeriesBenchmark({}))
    stock = Stock(symbol="AAA", is_active=True, sector="X")
    db_session.add(stock)
    db_session.flush()
    for d in SESSIONS[:30]:
        db_session.add(PriceHistory(stock_id=stock.id, date=d, open=Decimal("100"), high=Decimal("101"),
                                    low=Decimal("99"), close=Decimal("100"), volume=1000))
    call_day = SESSIONS[1]
    db_session.add(DailySignal(
        date=call_day, stock_id=stock.id, rank=1, overall_score=Decimal("70"), signal="BUY",
        reference_close=Decimal("100"), reference_date=SESSIONS[0], entry_low=Decimal("99"),
        entry_high=Decimal("101"), stop_loss=Decimal("92"), target_price=Decimal("116"),
        risk_reward=Decimal("1.5"), stop_method="atr", rationale=[],
        # 03:45 UTC = 09:15 IST. Stored in UTC because SQLite drops the offset.
        generated_at=datetime(call_day.year, call_day.month, call_day.day, 3, 45, tzinfo=timezone.utc),
    ))
    db_session.commit()

    resp = client.get("/api/v1/daily-signals/track-record/realistic")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["published_calls"], body["clean_calls"], body["excluded_days"]) == (1, 1, [])
    assert body["clean"]["positions"]["filled_at_open"] == 1
    h20 = next(h for h in body["clean"]["horizons"] if h["horizon_sessions"] == 20)
    # Flat stock: 100 -> 100, so the whole result is the cost.
    assert h20["mean_net_return_pct"] == pytest.approx(-0.24)
    assert h20["vs"]["nifty50"] == {"sample": 0, "mean_excess_pct": None, "beat_rate_pct": None}
    # The basket here is that same stock, gross: excess = -cost.
    assert h20["vs"]["universe_equal_weight"]["mean_excess_pct"] == pytest.approx(-0.24)

    def must_not_recompute(db):
        raise AssertionError("served the same data twice without the cache")

    monkeypatch.setattr(fs, "compute_realistic_record", must_not_recompute)
    assert client.get("/api/v1/daily-signals/track-record/realistic").json() == body
