"""Phase 26's index-timing simulation (services/index_timing.py).

What its result rests on: a rule decides from closes it has seen, the decision
trades at the next close, costs are charged exactly, and the tax book follows
the Income-tax Act's set-off and carry-forward rules.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.services import index_timing as it


def _sessions(n, start=date(2015, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _closes(values, start=date(2015, 1, 1)) -> pd.Series:
    return pd.Series(np.asarray(values, dtype=float), index=_sessions(len(values), start))


def _rise_then_crash(n=320, crash_at=260):
    """Up 0.1% a day, then a 30% fall that takes the close under its 200-day average."""
    v = 1000 * 1.001 ** np.arange(n)
    v[crash_at:] *= 0.7
    return _closes(v)


@pytest.mark.parametrize("rule", ["R1", "R2", "R3"])
def test_a_decision_uses_only_closes_up_to_its_day(rule):
    rng = np.random.default_rng(4)
    base = 1000 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, 700)))
    cut = 500
    other = base.copy()
    other[cut + 1:] *= 0.5
    a = it.decided_exposure(_closes(base), rule)
    b = it.decided_exposure(_closes(other), rule)
    pd.testing.assert_series_equal(a.iloc[:cut + 1], b.iloc[:cut + 1])


def test_the_rules_decide_what_they_say():
    c = _rise_then_crash()
    r1, r3 = it.decided_exposure(c, "R1"), it.decided_exposure(c, "R3")
    assert r1.iloc[:199].isna().all(), "no decision before 200 closes exist"
    assert r1.iloc[259] == 1.0 and r1.iloc[260] == 0.0
    assert r3.iloc[260] == it.V1_BEAR_EXPOSURE
    r2 = it.decided_exposure(c, "R2")
    month = pd.Series([d.year * 12 + d.month for d in c.index], index=c.index)
    changes = r2.ne(r2.shift()) & r2.notna() & r2.shift().notna()
    assert all(month[d] != month.shift(-1)[d] for d in r2.index[changes]), "R2 decides only at month-ends"


def test_a_decision_trades_at_the_next_close_not_its_own():
    c = _rise_then_crash()
    run = it.simulate(c, it.decided_exposure(c, "R1"), start=c.index[200], cost=0.0)
    v = run.values
    units = v.iloc[0] / c.iloc[200]
    # Decided at close 260 (the crash day), still holding through that close...
    assert v.loc[c.index[260]] == pytest.approx(units * c.iloc[260])
    # ...sold at close 261, then flat in cash.
    assert v.loc[c.index[261]] == pytest.approx(units * c.iloc[261])
    assert v.iloc[-1] == pytest.approx(v.loc[c.index[261]])
    assert run.switches == 1


def test_costs_are_charged_on_the_buy_and_on_the_final_sale():
    c = _closes(1000 * 1.0005 ** np.arange(300))
    run = it.simulate(c, it.decided_exposure(c, "BH"), start=c.index[1], cost=0.001)
    expected = 1_000_000 * (c.iloc[-1] / c.iloc[1]) * (1 - 0.001) / (1 + 0.001)
    assert run.final_value == pytest.approx(expected)


def test_cash_earns_its_yield_by_calendar_day():
    c = _rise_then_crash()
    flat = it.simulate(c, it.decided_exposure(c, "R1"), start=c.index[200], cost=0.0)
    paid = it.simulate(c, it.decided_exposure(c, "R1"), start=c.index[200], cost=0.0, cash_yield=0.06)
    days = (c.index[-1] - c.index[261]).days
    assert paid.final_value == pytest.approx(flat.final_value * 1.06 ** (days / 365))


def test_holding_period_is_more_than_twelve_months():
    assert not it.is_long_term(date(2020, 1, 15), date(2021, 1, 15))
    assert it.is_long_term(date(2020, 1, 15), date(2021, 1, 16))
    assert it.is_long_term(date(2020, 2, 29), date(2021, 3, 1))


def test_set_off_follows_the_act():
    book = it.TaxBook()
    book.realise(2020, 100.0, long_term=False)
    assert book.settle(2020) == pytest.approx(20.0)

    book = it.TaxBook()
    book.realise(2020, -50.0, long_term=False)
    book.realise(2020, 100.0, long_term=True)
    assert book.settle(2020) == pytest.approx(0.125 * 50), "a short-term loss absorbs long-term gains"

    book = it.TaxBook()
    book.realise(2020, -50.0, long_term=True)
    book.realise(2020, 100.0, long_term=False)
    assert book.settle(2020) == pytest.approx(20.0), "a long-term loss never absorbs short-term gains"
    assert book.carried == [[2020, "long", 50.0]]
    book.realise(2021, 80.0, long_term=True)
    assert book.settle(2021) == pytest.approx(0.125 * 30), "carried forward, it absorbs long-term gains"


def test_a_loss_is_carried_forward_for_eight_years_only():
    book = it.TaxBook()
    book.realise(2010, -100.0, long_term=False)
    book.settle(2010)
    book.realise(2018, 100.0, long_term=False)
    assert book.settle(2018) == pytest.approx(0.0), "eighth year: still usable"

    book = it.TaxBook()
    book.realise(2010, -100.0, long_term=False)
    book.settle(2010)
    book.realise(2019, 100.0, long_term=False)
    assert book.settle(2019) == pytest.approx(20.0), "ninth year: expired"


def test_buy_and_hold_pays_long_term_tax_once_at_the_end():
    c = _closes(1000 * 1.0005 ** np.arange(600))
    run = it.simulate(c, it.decided_exposure(c, "BH"), start=c.index[1], cost=0.0, taxed=True)
    gain = 1_000_000 * (c.iloc[-1] / c.iloc[1] - 1)
    assert run.tax_paid == pytest.approx(0.125 * gain)
    assert run.final_value == pytest.approx(1_000_000 + gain * (1 - 0.125))


def test_a_rule_that_sells_inside_a_year_pays_short_term_tax_from_its_proceeds():
    # Up 0.4% a day for 200 sessions after entry, then 35% lower and flat: the
    # brake sells at a gain, about nine months after buying, and stays out.
    v = 1000 * 1.004 ** np.arange(460)
    v[400:] = v[399] * 0.65
    c = _closes(v)
    run = it.simulate(c, it.decided_exposure(c, "R1"), start=c.index[200], cost=0.0, taxed=True)
    assert run.switches == 1
    gain = 1_000_000 * (c.iloc[401] / c.iloc[200] - 1)
    assert gain > 0
    assert run.tax_paid == pytest.approx(0.20 * gain)


def test_drawdown_measures():
    v = pd.Series([100, 120, 90, 130, 104, 140.0], index=_sessions(6))
    assert it.max_drawdown(v) == pytest.approx(-0.25)
    eps = it.drawdown_episodes(v, -0.15)
    assert [(round(depth, 4)) for _, _, depth in eps] == [-0.25, -0.2]
