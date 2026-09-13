"""The committed momentum_v1.0 verdict: reproducible, unedited, and current.

Excluded from the generator's own test run (it verifies the artifact, so it
cannot be an input to it) but runs everywhere else, including CI.

THE EXPECTED RESULT IS NOT THE ONE THE PLAN ANTICIPATED. The plan expected
Measurement PASS / Edge FAIL / Promotion BLOCKED. Generated from explicit
checks, the result is Measurement FAIL / Edge BLOCKED / Promotion BLOCKED:
two execution defects in the backtest engine (gap-down stops fill at the stop;
entries fill on the signal bar) fail measurement, and an edge verdict on numbers
the measurement checks cannot vouch for is no verdict. The edge checks that
could be evaluated are still recorded — four of five fail — so BLOCKED hides
nothing. test_the_current_result_and_what_decides_it pins exactly that.
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
    assert (v.measurement_verdict, v.edge_verdict, v.promotion_verdict) == ("FAIL", "BLOCKED", "BLOCKED")

    m = {c["name"]: c["status"] for c in v.evidence["measurement_checks"]}
    assert {k for k, s in m.items() if s != "PASS"} == {"gap_down_stop_execution", "next_session_entry_execution"}

    e = {c["name"]: c["status"] for c in v.evidence["edge_checks"]}
    assert {k for k, s in e.items() if s == "FAIL"} == {
        "beats_primary_benchmark_after_costs", "positive_median_fold_excess_return",
        "not_dependent_on_single_fold", "live_paper_beats_benchmark",
    }
    assert e["drawdown_within_tolerance"] == "PASS"
    assert {k for k, s in e.items() if s == "NOT_EVALUATED"} == set(sv.COMPARISON_CHECKS)

    fig = v.evidence["edge_figures"]
    # The negative MEAN is fragile: removing any one of three folds flips it.
    # The median and the live record are the robust evidence of underperformance.
    assert fig["mean_fold_excess_pp"] < 0 < fig["leave_one_out_mean_excess_max_pp"]
    assert fig["folds_whose_removal_alone_flips_the_mean"] == [1, 2, 10]
    assert fig["fold_whose_removal_raises_mean_most"] == 2
    assert fig["median_fold_excess_pp"] < 0 and fig["folds_beating_benchmark"] == 6


def test_the_generator_run_left_nothing_unevaluated():
    suite = _verdict().evidence["test_run"]["suite"]
    assert suite["failures"] == 0 and suite["errors"] == 0
    assert suite["skipped"] == 0, "a skipped test in the generating run is a check that was not evaluated"
