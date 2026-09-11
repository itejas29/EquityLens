"""The measured record of the research programme.

WHY THIS IS A PYTHON MODULE AND NOT A DATABASE TABLE OR A DOC READ AT RUNTIME.
The full per-fold output of every phase lives in docs/experiments/*/results.json
in the repository. The Dockerfile builds from ./backend, so docs/ is not in the
production image and cannot be read by the API. These entries are therefore a
transcription — each one carries the experiment directory it came from so the
claim can be checked against the raw JSON in the repo.

Nothing here is a projection, a target or a rounded-up figure. Every number was
produced by a walk-forward run that is committed and re-runnable. Where a phase
refuted the hypothesis it was testing, that is what it says.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Phase:
    phase: str
    question: str
    verdict: str
    detail: str
    source: str


# Ordered oldest to newest. Read top to bottom this is the argument for why the
# current configuration is what it is — and why it is not claimed to work.
PHASES: tuple[Phase, ...] = (
    Phase(
        phase="Phase 14",
        question="Does the four-sub-score composite rank stocks correctly?",
        verdict="REFUTED",
        detail=(
            "Close to inverted at short horizons — 5-day decile monotonicity "
            "-0.918. Production had been ranking backwards. The composite was "
            "removed from the signal path and momentum put in its place."
        ),
        source="docs/experiments/phase14_alpha",
    ),
    Phase(
        phase="Phase 16",
        question="Is the frozen risk layer robust to its own parameters?",
        verdict="HELD",
        detail=(
            "The 4-ATR stop, 200-day regime filter and monthly cadence survived "
            "a parameter sweep across 16 out-of-sample folds without the result "
            "depending on any single choice."
        ),
        source="docs/experiments/phase16_robustness",
    ),
    Phase(
        phase="Phase 17",
        question="Does a 5-day trend gate on entries improve the strategy?",
        verdict="HELD",
        detail=(
            "Return 14.65% -> 16.76%, Sharpe 1.03 -> 1.23, max drawdown "
            "-12.48% -> -11.49%, better in 13 of 16 folds. Adopted."
        ),
        source="docs/experiments/phase17_trend_gate",
    ),
    Phase(
        phase="Phase 18",
        question="Does widening the universe to 1000 names improve results?",
        verdict="REFUTED",
        detail=(
            "The same strategy on the same 16 folds measured 16.76% on one "
            "universe construction and 4.48% on another. A ~12pp swing from "
            "membership alone meant the headline number was describing a "
            "universe, not a strategy."
        ),
        source="docs/experiments/phase18_universe_size",
    ),
    Phase(
        phase="Phase 19",
        question="Does the edge survive arbitrary universe construction?",
        verdict="REFUTED",
        detail=(
            "Spread across constructions 4.51pp against a mean edge of +0.47pp. "
            "Random membership beat NIFTY by +0.26pp — as well as the ranked "
            "universes. Drawdown worse than NIFTY, downside capture 154-196%."
        ),
        source="docs/experiments/phase19_universe_robustness",
    ),
    Phase(
        phase="Phase 20",
        question="Does trading faster book more profit?",
        verdict="REFUTED",
        detail=(
            "Monotonically worse at every step. Daily rebalancing returned "
            "-87.61% and beat NIFTY in 0 of 16 folds. Costs explain only ~5% of "
            "the gap — the signal has no information at short horizons."
        ),
        source="docs/experiments/phase20_holding_period",
    ),
    Phase(
        phase="Phase 21",
        question="Do quality filters improve momentum?",
        verdict="REFUTED",
        detail=(
            "Price-derived quality (low volatility, shallow drawdown) filtered "
            "before the momentum rank. No arm beat the control on mean return: "
            "the best, lowdd_50, was -0.72pp. The filters cut the worst fold "
            "from -17.36% to -11.51% but gave up the big up-folds. ROE and EPS "
            "growth were rejected before any run: they exist only as today's "
            "snapshot, so a 2016 fold would have been using 2026 data."
        ),
        source="docs/experiments/phase21_quality_momentum",
    ),
)

# The live configuration's own measured result, from the Phase 20 sweep where
# it ran as the control arm on the neutral universe.
LIVE_ARM = {
    "return_pct": 5.38,
    "sharpe": 0.10,
    "max_drawdown_pct": -14.65,
    "benchmark_return_pct": 6.22,
    "vs_benchmark_pp": -0.84,
    "folds_beating_benchmark": 6,
    "folds_total": 16,
    "source": "docs/experiments/phase20_holding_period",
}

# Walk-forward protocol, identical across every phase above so the numbers stay
# comparable to each other.
PROTOCOL = {
    "folds": 16,
    "train_months": 18,
    "test_months": 6,
    "roll_months": 6,
    "window": "2016-10-01 to 2026-08-01",
    "capital": 500_000,
    "transaction_cost_pct": 0.12,
    "slippage_pct": 0.05,
}
