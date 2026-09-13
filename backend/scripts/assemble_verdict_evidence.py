"""Assemble the committed evidence bundle for the momentum_v1.0 verdict.

    python scripts/assemble_verdict_evidence.py

Inputs (all committed, each hashed into the bundle):
  docs/experiments/phase20_holding_period/results.json   primary backtest evidence
  docs/audit/momentum_v1.0/raw/track_record.json         live signals, captured from production
  docs/audit/momentum_v1.0/raw/paper_ledger.json         live AI paper ledger, exported read-only

Output:
  backend/app/core/verdict_artifacts/momentum_v1.0.evidence.json

WHY PHASE 20 AND NOTHING ELSE for the backtest. The verdict needs evidence that
tests THIS strategy version on a stated universe. Phase 20's h90_monthly_LIVE
arm is the frozen V1 configuration (including the Phase 17 trend gate) on a
point-in-time top-500 drawn from the 1,000-stock pool. Phase 19 also ran that
configuration but is withdrawn (cache contamination). Phase 16's baseline,
naive-momentum and stress-slippage arms predate the trend gate and used a
different universe construction — Phase 18 measured construction alone moving
results by ~12pp — so they are recorded as ineligible rather than borrowed.
A check with no eligible evidence stays NOT_EVALUATED.
"""

import hashlib
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

import os  # noqa: E402

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://unused:unused@localhost/unused")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("JWT_SECRET_KEY", "evidence-assembly-does-not-use-this-secret")

from app.core.canonical import sha256_hex  # noqa: E402
from app.core.research_record import PROTOCOL  # noqa: E402
from app.core.v1_strategy import V1_DESCRIPTION, V1_VERSION  # noqa: E402

PHASE20 = REPO / "docs/experiments/phase20_holding_period/results.json"
TRACK = REPO / "docs/audit/momentum_v1.0/raw/track_record.json"
LEDGER = REPO / "docs/audit/momentum_v1.0/raw/paper_ledger.json"
OUT = BACKEND / "app/core/verdict_artifacts/momentum_v1.0.evidence.json"
LIVE_ARM = "h90_monthly_LIVE"


def _file_sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> None:
    p20 = json.loads(PHASE20.read_text())
    track = json.loads(TRACK.read_text())
    ledger = json.loads(LEDGER.read_text())

    folds = [{
        "fold": f["fold"],
        "test_start": f["test"][0],
        "test_end": f["test"][1],
        "regime": f["regime"],
        "strategy": f["arms"][LIVE_ARM],
        "benchmark": f["benchmark"],
    } for f in p20["folds"]]

    bundle = {
        "strategy_id": "momentum_v1.0",
        "strategy_version": V1_VERSION,
        "strategy_description": V1_DESCRIPTION,
        "id_note": ("strategy_id is the research label for the frozen baseline; strategy_version is the "
                    "code identifier in app/core/v1_strategy.py it maps to. The baseline includes the "
                    "Phase 17 trend gate, which is why the code string reads v1.1."),
        "sources": {
            str(p.relative_to(REPO)): _file_sha(p) for p in (PHASE20, TRACK, LEDGER)
        },
        "backtest": {
            "source": str(PHASE20.relative_to(REPO)),
            "arm": LIVE_ARM,
            "run_completed": "2026-09-10 07:02 IST (docs/experiments/phase20_holding_period/FINDINGS.md)",
            "protocol": PROTOCOL,
            "universe": ("point-in-time top 500 by 20-day traded value at each fold start, drawn from a "
                         f"{p20.get('pool')}-stock pool of currently active names"),
            "benchmark": ("NIFTY 50 price index (^NSEI via yfinance). A price index, not total return: it "
                          "excludes dividends, so it understates what the benchmark actually returned."),
            "precision": "experiment scripts round per-fold metrics to 2 decimals before writing results.json",
            "folds": folds,
            "known_biases": [
                {"id": "survivors_only_universe", "direction": "favours_strategy",
                 "note": "Membership is drawn from today's active stocks projected backwards (audit finding "
                         "#2). Names that delisted are excluded, and they are the ones momentum is most "
                         "exposed to on the way down. Not yet measured: phase21_survivorship has not run."},
                {"id": "gap_down_stop_fill", "direction": "favours_strategy",
                 "note": "Stops fill at the stop price even when the session opened below it. Measurement "
                         "defect, encoded as a strict xfail."},
                {"id": "same_bar_entry", "direction": "not_established",
                 "note": "Entries fill on the signal bar at entry_high derived from that bar's close. "
                         "Unexecutable timing, but entry_high sits above the close. Strict xfail."},
                {"id": "benchmark_price_index", "direction": "favours_strategy",
                 "note": "The benchmark omits dividends; a total-return benchmark would be harder to beat."},
                {"id": "benchmark_pays_no_costs", "direction": "against_strategy",
                 "note": "The benchmark curve pays no transaction costs while the strategy does, "
                         "understating the strategy by ~0.22pp (Phase 20 FINDINGS)."},
            ],
        },
        "ineligible_evidence": [
            {"check": "beats_random_membership_baseline",
             "source": "docs/experiments/phase19_universe_robustness",
             "reason": "Phase 19 is WITHDRAWN: its random-membership arms shared an indicator cache first "
                       "populated by current_top500, so they traded roughly their overlap with it rather "
                       "than random membership. Not evidence until re-run on the fixed engine."},
            {"check": "beats_naive_momentum_baseline",
             "source": "docs/experiments/phase16_robustness",
             "reason": "Phase 16's momentum variants predate the Phase 17 trend gate and used a different "
                       "universe construction; Phase 18 showed construction alone moves results ~12pp. "
                       "No naive baseline has been run alongside the frozen strategy."},
            {"check": "passes_stress_slippage",
             "source": "docs/experiments/phase16_robustness",
             "reason": "Phase 16's EQ_slip_2x / EQ_slip_3x / EQ_cost2x_slip2x arms test a different "
                       "strategy version on a different universe. No stress run of the frozen strategy exists."},
            {"check": "not_dependent_on_single_sector",
             "source": None,
             "reason": "No committed evidence attributes the strategy's returns by sector."},
            {"check": "survivorship_free_confirmation",
             "source": "backend/scripts/phase21_survivorship.py",
             "reason": "The survivorship-free re-run exists as a script but has not been run. The primary "
                       "evidence is survivors-only, a bias that favours the strategy, so an edge on it alone "
                       "would not be established."},
        ],
        "live_track_record": track,
        "paper_ledger": {
            "exported_at": ledger["exported_at"],
            "account": ledger["account"],
            "reconciliation": ledger["reconciliation"],
            "trades_sha256": sha256_hex(ledger["trades"]),
            "note": ("The first cohort of trades was placed on 2026-08-25 from signals generated at 10:18 IST "
                     "off in-progress bars (audit finding #16). They stand as traded."),
        },
        "governance": {
            "data_terms_reviewed": {
                "status": "FAIL",
                "detail": "yfinance terms of use have not been reviewed for this use, private or otherwise "
                          "(as of 2026-09-13)."},
            "scope_approved": {
                "status": "FAIL",
                "detail": "No decision is recorded to use EquityLens beyond private research for a known group."},
            "regulatory_review_complete": {
                "status": "FAIL",
                "detail": "No SEBI Research Analyst / Investment Adviser review. Required before any public or "
                          "paid stock-specific recommendation."},
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")
    print(f"wrote {OUT.relative_to(REPO)}  sha256:{sha256_hex(bundle)}")


if __name__ == "__main__":
    main()
