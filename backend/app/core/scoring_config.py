"""Weights and thresholds for the scoring engine (app/services/scoring.py).

Kept out of the scoring functions so weights can be tuned/audited without
touching computation logic. All weight dicts sum to 1.0.
"""

# --- Sub-score internal weights -------------------------------------------

TECHNICAL_WEIGHTS = {
    "price_vs_dma50": 0.25,
    "golden_cross": 0.25,
    "rsi_band": 0.20,
    "macd_momentum": 0.20,
    "volume_confirmation": 0.10,
}

FUNDAMENTAL_WEIGHTS = {
    "revenue_growth": 0.15,
    "eps_growth": 0.15,
    "profit_growth": 0.15,
    "roe": 0.20,
    "roce": 0.15,
    "operating_margin": 0.10,
    "debt_to_equity": 0.10,
}

VALUATION_WEIGHTS = {
    "pe_vs_sector": 0.30,
    "pe_vs_own_range": 0.25,
    "pb_vs_sector": 0.25,
    "growth_adjusted_pe": 0.20,
}

RISK_WEIGHTS = {
    "volatility": 0.30,
    "beta": 0.25,
    "max_drawdown": 0.25,
    "liquidity": 0.20,
}

# --- Composite weights ------------------------------------------------------

COMPOSITE_WEIGHTS = {
    "technical": 0.30,
    "fundamental": 0.30,
    "valuation": 0.20,
    "risk": 0.20,
}

# A sub-score is nulled entirely (rather than renormalized) once more than
# half its inputs are missing — with a majority of inputs absent we don't
# trust the remaining ones to represent the sub-score.
SUBSCORE_NULL_THRESHOLD = 0.5

# --- Signal mapping (applied to overall_score, 0-100) -----------------------

SIGNAL_THRESHOLDS = [
    (75, "STRONG_ACCUMULATE"),
    (60, "ACCUMULATE"),
    (45, "WATCH"),
    (30, "AVOID"),
    (0, "STRONG_AVOID"),
]
