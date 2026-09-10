"""The Phase 21 quality gate.

Phase 21 was originally specified as ROE + EPS growth. It cannot be: the
fundamentals table holds 18 days of snapshots against a 10.1-year backtest
window, so an accounting-quality filter would be a decade of look-ahead — and
it would not fail loudly, it would produce a flattering curve, because the
companies with high ROE today are the ones that compounded over the decade.
ROE is also present for only 70 of 500 active names.

The gate therefore uses volatility and trailing max drawdown, both derived from
price history and both genuinely point-in-time. These tests pin the properties
that make it a fair test rather than a second way to leak.
"""

from dataclasses import dataclass

import pytest

from app.services.backtest import _apply_quality_gate


@dataclass
class _Snap:
    stock_id: int
    volatility: float | None = None
    max_drawdown: float | None = None
    overall_score: float = 50.0


def _ids(snaps):
    return sorted(s.stock_id for s in snaps)


def test_lowvol_keeps_the_calmer_half():
    c = [_Snap(1, volatility=0.10), _Snap(2, volatility=0.20),
         _Snap(3, volatility=0.30), _Snap(4, volatility=0.40)]
    assert _ids(_apply_quality_gate(c, "lowvol", 50.0)) == [1, 2]


def test_lowdd_keeps_the_shallower_half():
    """max_drawdown is negative; closer to zero is better."""
    c = [_Snap(1, max_drawdown=-0.05), _Snap(2, max_drawdown=-0.10),
         _Snap(3, max_drawdown=-0.40), _Snap(4, max_drawdown=-0.60)]
    assert _ids(_apply_quality_gate(c, "lowdd", 50.0)) == [1, 2]


def test_both_requires_passing_on_each_dimension():
    # 1 is calm and shallow; 2 is calm but deep; 3 is volatile but shallow.
    c = [_Snap(1, volatility=0.10, max_drawdown=-0.05),
         _Snap(2, volatility=0.12, max_drawdown=-0.60),
         _Snap(3, volatility=0.50, max_drawdown=-0.06),
         _Snap(4, volatility=0.55, max_drawdown=-0.70)]
    assert _ids(_apply_quality_gate(c, "both", 50.0)) == [1]


def test_none_is_a_no_op():
    c = [_Snap(i, volatility=i / 10) for i in range(1, 6)]
    assert _ids(_apply_quality_gate(c, "none", 50.0)) == [1, 2, 3, 4, 5]


def test_a_missing_value_leaves_the_gate_open():
    """A data gap must not masquerade as a quality signal — the same rule the
    Phase 17 trend gate follows. Excluding on missing data would quietly bias
    the universe toward whichever names happen to have complete indicators."""
    c = [_Snap(1, volatility=0.10), _Snap(2, volatility=0.90), _Snap(3, volatility=None)]
    kept = _ids(_apply_quality_gate(c, "lowvol", 50.0))
    assert 3 in kept, "a stock with no volatility reading was silently dropped"
    assert 1 in kept and 2 not in kept


def test_the_cut_is_cross_sectional_not_a_fixed_threshold():
    """A 30% annualised volatility meant something different in 2018 than in
    2020. Ranking within the date is what keeps the gate comparable across a
    decade of folds — the same absolute vol passes in a calm universe and fails
    in a violent one."""
    calm = [_Snap(1, volatility=0.05), _Snap(2, volatility=0.08),
            _Snap(3, volatility=0.30), _Snap(4, volatility=0.35)]
    violent = [_Snap(1, volatility=0.30), _Snap(2, volatility=0.35),
               _Snap(3, volatility=0.80), _Snap(4, volatility=0.90)]

    assert 3 not in _ids(_apply_quality_gate(calm, "lowvol", 50.0))
    assert 1 in _ids(_apply_quality_gate(violent, "lowvol", 50.0))
    # 0.30 fails in the calm universe and passes in the violent one.


def test_the_percentile_controls_how_much_is_kept():
    c = [_Snap(i, volatility=i / 100) for i in range(1, 11)]
    assert len(_apply_quality_gate(c, "lowvol", 50.0)) == 5
    assert len(_apply_quality_gate(c, "lowvol", 80.0)) == 2
    assert len(_apply_quality_gate(c, "lowvol", 20.0)) == 8


def test_it_never_empties_the_slate_entirely():
    """A gate that can return nothing turns into an accidental cash strategy,
    and the fold would measure the gate's arithmetic rather than the factor."""
    c = [_Snap(1, volatility=0.10), _Snap(2, volatility=0.20)]
    assert len(_apply_quality_gate(c, "lowvol", 99.0)) >= 1


def test_a_single_candidate_survives_any_setting():
    c = [_Snap(1, volatility=0.9, max_drawdown=-0.9)]
    assert _ids(_apply_quality_gate(c, "both", 50.0)) == [1]


def test_the_gate_does_not_reorder_the_candidates():
    """It filters, it does not rank. Ranking stays with the momentum score, so
    the arms differ in WHICH names are eligible and in nothing else."""
    c = [_Snap(1, volatility=0.40), _Snap(2, volatility=0.10), _Snap(3, volatility=0.20)]
    kept = _apply_quality_gate(c, "lowvol", 80.0)
    assert [s.stock_id for s in kept] == [s.stock_id for s in c if s in kept]


def test_the_snapshot_carries_what_the_gate_needs():
    """max_drawdown had to be added to PointInTimeSnapshot for this. If it ever
    stops being carried, every lowdd arm silently becomes a no-op — the gate
    would see None everywhere and leave itself open."""
    from app.services.backtest_scoring import PointInTimeSnapshot

    snap = PointInTimeSnapshot(stock_id=1, technical_score=None, risk_score=None,
                               overall_score=None, signal=None, levels=None, latest_close=None)
    assert hasattr(snap, "volatility")
    assert hasattr(snap, "max_drawdown")
    assert snap.max_drawdown is None, "must default to None so an old cache stays valid"


def test_the_default_strategy_has_the_gate_off():
    """Every published phase must be reproducible byte-for-byte. A gate that
    defaulted on would silently change all of them."""
    from app.core.strategy_params import StrategyParams
    from app.core.v1_strategy import V1

    assert StrategyParams().quality_filter == "none"
    assert getattr(V1, "quality_filter", "none") == "none"
