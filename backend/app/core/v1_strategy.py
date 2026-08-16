"""EquityLens v1 — the frozen production strategy.

Single source of truth so production daily signals and the research backtests
cannot drift apart. Before this existed, app/services/daily_signals.py called
score_universe() (the 4-sub-score composite) and compute_levels_for_stock()
with no parameters, which meant production was silently running:

    composite ranking + 2 ATR stop + support stop ON + no regime filter

...while the research had established the opposite on 16 out-of-sample folds.
Phase 14 measured that composite ranking as close to inverted at short horizons
(5-day decile monotonicity -0.918), so production was ranking backwards.

The values below are the frozen Phase 15/16 architecture. They are NOT to be
tuned from live results — the research phases are the only place they change,
and any change there must be re-validated out of sample.
"""

from dataclasses import replace

from app.core.strategy_params import StrategyParams

# --- Frozen EquityLens v1 -----------------------------------------------------
V1 = replace(
    StrategyParams.for_appetite("moderate"),
    # Alpha layer (Phase 14/15): momentum 12-month minus 1-month, equal weight.
    ranking_engine="momentum",
    momentum_long_days=252,
    momentum_skip_days=21,
    sizing_method="equal_weight",
    # Risk layer (Phase 12/13), frozen and validated ROBUST in Phase 16.
    atr_stop_multiplier=4.0,
    use_support_stop=False,
    use_regime_filter=True,
    regime_ma_days=200,
    bull_exposure=1.0,
    bear_exposure=0.25,
    reentry_rule="immediate",
    cash_buffer_pct=0.0,
    rebalance_frequency="monthly",
)

V1_VERSION = "v1.0-momentum-eqwt-regime"

# Human-readable description, surfaced in the API so the UI can state which
# strategy produced a given snapshot rather than leaving it implicit.
V1_DESCRIPTION = (
    "Momentum (12m-1m) ranking, equal-weight sizing, 4 ATR stop, no support stop, "
    "NIFTY 200DMA regime filter at 25% bear exposure, immediate re-entry, monthly rebalance."
)
