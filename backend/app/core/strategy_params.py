"""Every strategy knob in one injectable object.

These values previously lived as module-level constants in portfolio_config and
were read directly by levels.py and backtest.py. That made them impossible to
sweep: any test of "does a wider stop help?" meant editing a global and
re-running, which is exactly the kind of hand-adjustment that produces a
strategy tuned to one window.

Defaults below reproduce the current live behaviour exactly, so an unparameterised
call behaves as before. The optimiser varies them; nothing else should.
"""

from dataclasses import dataclass

from app.core.portfolio_config import (
    ATR_STOP_MULTIPLIER,
    CASH_BUFFER_PCT,
    ENTRY_ZONE_ATR_MULTIPLIER,
    MAX_ALLOCATION_PCT,
    MAX_STOCKS_PER_SECTOR,
    MAX_STOCKS_PREFERENCE,
    MIN_SCORE_THRESHOLD,
    RISK_PCT_PER_TRADE,
    RISK_REWARD_RATIO,
    SUPPORT_LOOKBACK_DAYS,
)


@dataclass(frozen=True)
class StrategyParams:
    # --- Exit geometry ---
    atr_stop_multiplier: float = ATR_STOP_MULTIPLIER
    entry_zone_atr_multiplier: float = ENTRY_ZONE_ATR_MULTIPLIER
    risk_reward_ratio: float = RISK_REWARD_RATIO
    # When True the 20-day low is used as the stop whenever it sits ABOVE the
    # ATR stop — i.e. the tighter of the two wins. This is the current live
    # behaviour and a prime whipsaw suspect, so it is a sweepable flag rather
    # than a hardcoded rule.
    use_support_stop: bool = True
    support_lookback_days: int = SUPPORT_LOOKBACK_DAYS

    # --- Holding ---
    horizon_days: int = 90
    rebalance_frequency: str = "monthly"

    # --- Portfolio construction ---
    min_score: float = 60.0
    max_allocation_pct: float = MAX_ALLOCATION_PCT["moderate"]
    cash_buffer_pct: float = CASH_BUFFER_PCT["moderate"]
    max_stocks_per_sector: int = MAX_STOCKS_PER_SECTOR
    max_stocks: int = MAX_STOCKS_PREFERENCE
    risk_pct_per_trade: float = RISK_PCT_PER_TRADE["moderate"]

    # --- Market regime filter ---
    #
    # Targets the failure the walk-forward identified: 111% upside capture but
    # 203% downside capture. That asymmetry is a market-exposure problem, not a
    # stock-selection one, so the lever is portfolio-level exposure rather than
    # any change to scoring.
    #
    # Defaults are deliberately inert (filter off, both exposures 100%) so
    # enabling it is an explicit experiment and every existing result stays
    # reproducible.
    use_regime_filter: bool = False
    regime_ma_days: int = 200
    bull_exposure: float = 1.0
    bear_exposure: float = 1.0

    # --- Re-entry rule (Phase 13) ---
    #
    # Phase 12 showed the filter exits well but re-enters late: in the 2020
    # recovery it returned +5.76% against NIFTY's +33.44%, giving back more than
    # the crash protection had saved relative to simply staying invested.
    #
    # These rules govern the BEAR -> BULL transition only. The BULL -> BEAR exit
    # stays immediate in every variant, because that is the half that works and
    # delaying it would trade away the crash protection this phase is meant to
    # preserve.
    #
    #   "immediate" — current behaviour: full exposure the first day close >= MA
    #   "confirm3"  — require 3 consecutive closes above the MA
    #   "confirm5"  — require 5 consecutive closes above the MA
    #   "staged"    — re-enter at once but ramp 25/50/75/100% as the bull streak
    #                 extends, so a false dawn costs a quarter position, not a full one
    #   "momentum"  — require close >= MA AND positive trailing momentum
    reentry_rule: str = "immediate"
    reentry_momentum_days: int = 20
    # Trading days per staged step; ~21 aligns each step with a monthly rebalance.
    reentry_stage_days: int = 21

    # --- Ranking engine (Phase 14) ---
    #
    # "composite" — the existing technical+risk point-in-time score.
    # "momentum"  — 12-month minus 1-month return, percentile-ranked 0-100
    #               across the cross-section so the same min_score threshold
    #               selects a comparable slice of the universe.
    # Only the RANKING changes. Universe, entry logic, sizing, stops, regime,
    # costs and execution stay identical, which is what makes the A/B clean.
    ranking_engine: str = "composite"

    # --- Position sizing (Phase 15) ---
    #
    # Phase 14 measured momentum deploying only 41% of capital. High-momentum
    # names carry high ATR, and risk-based sizing with a 4-ATR stop mechanically
    # buys least of exactly the stocks the signal ranks highest. These are the
    # alternatives, with everything else frozen:
    #   "atr_risk"     — current: shares = risk_budget / (entry - stop)
    #   "equal_weight" — equity / max_stocks per position
    #   "inverse_vol"  — weights proportional to 1/volatility, normalised across
    #                    the slate so total intended exposure matches equal_weight
    sizing_method: str = "atr_risk"

    # --- Scoring blend used by the backtest (technical/risk renormalised) ---
    technical_weight: float = 0.6
    risk_weight: float = 0.4

    @classmethod
    def for_appetite(cls, appetite: str) -> "StrategyParams":
        """Defaults matching the live per-appetite configuration."""
        return cls(
            min_score=float(MIN_SCORE_THRESHOLD[appetite]),
            max_allocation_pct=MAX_ALLOCATION_PCT[appetite],
            cash_buffer_pct=CASH_BUFFER_PCT[appetite],
            risk_pct_per_trade=RISK_PCT_PER_TRADE[appetite],
        )

    def label(self) -> str:
        """Compact identifier for reporting — only the swept fields."""
        return (
            f"stop{self.atr_stop_multiplier:g}"
            f"/rr{self.risk_reward_ratio:g}"
            f"/{'sup' if self.use_support_stop else 'nosup'}"
            f"/cash{self.cash_buffer_pct:.0%}"
            f"/hold{self.horizon_days}"
            f"/{self.rebalance_frequency[:1]}"
            f"/n{self.max_stocks}"
            f"/min{self.min_score:g}"
            + (f"/{self.ranking_engine}" if self.ranking_engine != "composite" else "")
            + (f"/{self.sizing_method}" if self.sizing_method != "atr_risk" else "")
            + (
                f"/bear{self.bear_exposure:.0%}/re-{self.reentry_rule}"
                if self.use_regime_filter
                else "/noregime"
            )
        )
