"""The committed momentum_v1.0 verdict: reproducible, unedited, and current.

Excluded from the generator's own test run (it verifies the artifact, so it
cannot be an input to it) but runs everywhere else, including CI.

THE RESULT, AND HOW IT GOT HERE. Generated 2026-09-13 the verdict was
Measurement FAIL / Edge BLOCKED / Promotion BLOCKED: two execution defects in
the backtest engine (gap-down stops filled at the stop; entries filled on the
signal bar) failed measurement. Both were fixed on 2026-10-30, V1's folds were
re-run on the fixed engine (Phase 23's v1_raw arm), and the verdict is now
Measurement PASS / Edge FAIL / Promotion BLOCKED — what the original plan
anticipated. Every survivors-only backtest check passes; the edge fails on the
live record and, since Phase 24 measured it, on the survivorship-free
confirmation — the backtest excess disappears once companies that later
delisted or faded are put back. test_the_current_result_and_what_decides_it
pins exactly that.
"""

import ast
import hashlib
import json
from pathlib import Path

from app.core.canonical import sha256_hex
from app.core.v1_strategy import V1_VERSION
from app.core.verdict import StrategyVerdict
from app.services import strategy_verdict as sv

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
ARTIFACT = BACKEND / "app/core/verdict_artifacts/momentum_v1.0.verdict.json"
EVIDENCE = BACKEND / "app/core/verdict_artifacts/momentum_v1.0.evidence.json"


def _verdict() -> StrategyVerdict:
    # from_json verifies the embedded content hash: a hand edit raises here.
    return StrategyVerdict.from_json(ARTIFACT.read_text())


def _evidence() -> dict:
    return json.loads(EVIDENCE.read_text())


def test_the_artifact_loads_with_an_intact_content_hash():
    assert _verdict().strategy_id == "momentum_v1.0"


def test_the_verdict_is_for_the_strategy_the_code_runs():
    """If the frozen strategy changes, this verdict no longer describes it."""
    assert _verdict().strategy_version == V1_VERSION


def test_the_data_snapshot_is_the_committed_evidence_bundle():
    assert _verdict().data_snapshot == f"sha256:{sha256_hex(_evidence())}"


def test_the_evidence_sources_are_unchanged_since_assembly():
    for rel, digest in _evidence()["sources"].items():
        assert hashlib.sha256((REPO / rel).read_bytes()).hexdigest() == digest, f"{rel} changed after assembly"


def test_the_committed_verdict_is_exactly_what_the_logic_produces():
    committed = _verdict()
    run = committed.evidence["test_run"]
    recomputed = sv.compute_verdict(
        evidence=_evidence(), evidence_sha256=sha256_hex(_evidence()),
        outcomes=run["outcomes"], suite=run["suite"],
        code_revision=committed.code_revision, generated_at=committed.generated_at,
    )
    assert recomputed.content() == committed.content()


def _xfail_marked(node: str) -> bool:
    path, func = node.split("::")
    for fn in ast.walk(ast.parse((BACKEND / path).read_text())):
        if isinstance(fn, ast.FunctionDef) and fn.name == func:
            return any("xfail" in ast.unparse(d) for d in fn.decorator_list)
    raise AssertionError(f"{node} not found")


def test_known_defects_in_the_verdict_are_still_known_defects_in_the_code():
    """Binds the artifact to the source. Fixing an engine defect removes its
    strict xfail marker; without regenerating the verdict, this goes red rather
    than leaving a stale FAIL published."""
    specs = {name: nodes for name, nodes, _ in sv.MEASUREMENT_SPECS}
    for check in _verdict().evidence["measurement_checks"]:
        nodes = specs.get(check["name"])
        if nodes is None:
            continue
        marked = [n for n in nodes if _xfail_marked(n)]
        if check["detail"].startswith("known defect"):
            assert marked == list(nodes), f"{check['name']} is recorded as a known defect but is no longer xfail-marked"
        elif check["status"] == "PASS":
            assert not marked, f"{check['name']} is recorded PASS but {marked} is xfail-marked now"


def test_the_current_result_and_what_decides_it():
    v = _verdict()
    assert (v.measurement_verdict, v.edge_verdict, v.promotion_verdict) == ("PASS", "FAIL", "BLOCKED")

    m = {c["name"]: c["status"] for c in v.evidence["measurement_checks"]}
    assert {k for k, s in m.items() if s != "PASS"} == set(), m

    e = {c["name"]: c["status"] for c in v.evidence["edge_checks"]}
    # On the fixed engine every survivors-only backtest check passes; what fails
    # is the survivorship-free confirmation (Phase 24) and the live record.
    assert {k for k, s in e.items() if s == "FAIL"} == {"live_paper_beats_benchmark", "survivorship_free_confirmation"}
    assert {k for k, s in e.items() if s == "PASS"} == {
        "beats_primary_benchmark_after_costs", "positive_median_fold_excess_return",
        "not_dependent_on_single_fold", "drawdown_within_tolerance",
    }
    assert {k for k, s in e.items() if s == "NOT_EVALUATED"} == set(sv.COMPARISON_CHECKS) - {"survivorship_free_confirmation"}

    fig = v.evidence["edge_figures"]
    # Positive, and not carried by any one fold — on a survivors-only universe.
    assert fig["mean_fold_excess_pp"] > 0 and fig["leave_one_out_mean_excess_min_pp"] > 0
    assert fig["folds_whose_removal_alone_flips_the_mean"] == []
    assert fig["median_fold_excess_pp"] > 0 and fig["folds_beating_benchmark"] == 9
    # The live record is negative from an obtainable entry at the shorter horizons.
    assert fig["live_edge_from_entry_pct"]["1"] < 0 and fig["live_edge_from_entry_pct"]["5"] < 0


def test_the_generator_run_left_nothing_unevaluated():
    suite = _verdict().evidence["test_run"]["suite"]
    assert suite["failures"] == 0 and suite["errors"] == 0
    assert suite["skipped"] == 0, "a skipped test in the generating run is a check that was not evaluated"
