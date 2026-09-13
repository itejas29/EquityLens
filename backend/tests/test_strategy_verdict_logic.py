"""How test outcomes and evidence become checks — on synthetic inputs.

The committed momentum_v1.0 artifact is verified separately
(test_strategy_verdict_artifact.py). These pin the mapping itself, including
that a PASS is reachable: a verdict engine that could only ever say "not
passed" would be built to fail, not built to measure.
"""

import ast
import importlib.util
from pathlib import Path

import pytest

from app.services import strategy_verdict as sv

BACKEND = Path(__file__).resolve().parents[1]


def _generator():
    spec = importlib.util.spec_from_file_location("gen", BACKEND / "scripts/generate_strategy_verdict.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
  <testcase classname="tests.test_a" name="test_pass"/>
  <testcase classname="tests.test_a" name="test_fail"><failure message="boom"/></testcase>
  <testcase classname="tests.test_a" name="test_error"><error message="fixture broke"/></testcase>
  <testcase classname="tests.test_a" name="test_known_defect"><skipped type="pytest.xfail" message="KNOWN DEFECT: x"/></testcase>
  <testcase classname="tests.test_a" name="test_skipped"><skipped type="pytest.skip" message="needs postgres"/></testcase>
  <testcase classname="tests.test_a" name="test_param[a]"/>
  <testcase classname="tests.test_a" name="test_param[b]"><failure message="case b"/></testcase>
  <testcase classname="tests.test_a" name="test_xpass_strict"><failure message="[XPASS(strict)] fixed now"/></testcase>
</testsuite></testsuites>"""


# ------------------------------------------------------------------ junit --

def test_junit_outcomes_distinguish_xfail_from_skip_and_failure():
    outcomes, suite = _generator().parse_junit(JUNIT)
    status = {k.split("::")[1]: v["status"] for k, v in outcomes.items()}
    assert status == {
        "test_pass": "passed", "test_fail": "failed", "test_error": "failed",
        "test_known_defect": "xfailed", "test_skipped": "skipped",
        "test_param": "failed", "test_xpass_strict": "failed",
    }
    assert list(outcomes)[0] == "tests/test_a.py::test_pass"
    assert suite == {"tests": 8, "passed": 2, "failures": 3, "errors": 1, "skipped": 1, "xfailed": 1}


def test_a_parametrized_function_takes_its_worst_case():
    outcomes, _ = _generator().parse_junit(JUNIT)
    assert outcomes["tests/test_a.py::test_param"]["status"] == "failed"


# ------------------------------------------------------ measurement mapping --

@pytest.mark.parametrize("status,expected", [
    ("passed", "PASS"), ("failed", "FAIL"), ("xfailed", "FAIL"), ("skipped", "NOT_EVALUATED"),
])
def test_a_test_outcome_maps_to_a_check_status(status, expected):
    node = "tests/test_x.py::test_y"
    check = sv._test_check("c", (node,), "meaning", {node: {"status": status, "message": "why"}})
    assert check.status == expected


def test_a_test_missing_from_the_run_is_not_an_assumed_pass():
    check = sv._test_check("c", ("tests/test_x.py::test_gone",), "meaning", {})
    assert check.status == "NOT_EVALUATED" and "not present" in check.detail


def test_a_known_defect_carries_its_reason_into_the_check():
    node = "tests/test_x.py::test_y"
    check = sv._test_check("c", (node,), "m", {node: {"status": "xfailed", "message": "KNOWN DEFECT: stops"}})
    assert "KNOWN DEFECT: stops" in check.detail


def test_every_referenced_test_exists_in_its_file():
    """Renaming a test must not silently orphan the check that rests on it."""
    missing = []
    for node in sv.referenced_tests():
        path, func = node.split("::")
        tree = ast.parse((BACKEND / path).read_text())
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        if func not in names:
            missing.append(node)
    assert not missing, f"checks reference tests that do not exist: {missing}"


# --------------------------------------------------------------------- edge --

def _evidence(returns, bench, *, dd=(-10.0, -10.0), live=((1, 0.5, True), (5, 0.4, True)),
              eligible=None, ineligible=None, governance=None):
    folds = [{"fold": i + 1, "strategy": {"total_return_pct": r, "max_drawdown_pct": dd[0]},
              "benchmark": {"total_return_pct": b, "max_drawdown_pct": dd[1]}}
             for i, (r, b) in enumerate(zip(returns, bench))]
    return {
        "strategy_id": "s", "strategy_version": "v",
        "backtest": {"source": "x", "folds": folds, "benchmark": "b", "universe": "u", "known_biases": []},
        "live_track_record": {"payload": {"signals_published": 100, "horizons": [
            {"horizon_days": h, "edge_from_entry_pct": e, "sufficient_sample": ok, "sample": 50} for h, e, ok in live]}},
        "eligible_evidence": eligible or [],
        "ineligible_evidence": ineligible or [],
        "paper_ledger": {"reconciliation": {"residual": "0.00", "trades": 3, "stored_cash": "1",
                                            "expected_cash": "1", "identity": "cash == x"}},
        "governance": governance or {},
    }


def _status(checks):
    return {c.name: c.status for c in checks}


def test_mean_and_median_are_computed_against_the_benchmark():
    # excess per fold: 5, 2, 1, 3 -> mean 2.75, median 2.5
    checks, figures = sv.edge_checks(_evidence([10, 2, 6, 8], [5, 0, 5, 5]))
    assert figures["mean_fold_excess_pp"] == pytest.approx(2.75)
    assert figures["median_fold_excess_pp"] == pytest.approx(2.5)
    assert _status(checks)["beats_primary_benchmark_after_costs"] == "PASS"
    assert _status(checks)["positive_median_fold_excess_return"] == "PASS"


def test_a_positive_mean_with_a_zero_median_is_not_an_edge():
    # excess 5, -2, -1, 1: mean +0.75 but the typical fold does not win
    checks, _ = sv.edge_checks(_evidence([10, -2, 4, 6], [5, 0, 5, 5]))
    assert _status(checks)["beats_primary_benchmark_after_costs"] == "PASS"
    assert _status(checks)["positive_median_fold_excess_return"] == "FAIL"


def test_one_fold_cannot_measure_single_fold_dependence():
    checks, _ = sv.edge_checks(_evidence([5], [0]))
    assert _status(checks)["not_dependent_on_single_fold"] == "NOT_EVALUATED"


def test_no_folds_is_not_evaluated_rather_than_a_crash():
    checks, _ = sv.edge_checks(_evidence([], []))
    assert {_status(checks)[k] for k in ("beats_primary_benchmark_after_costs", "drawdown_within_tolerance")} == {"NOT_EVALUATED"}


def test_a_result_carried_by_one_fold_fails_the_single_fold_check():
    # One huge fold makes the mean positive; drop it and the rest lose.
    checks, figures = sv.edge_checks(_evidence([40, -1, -1, -1], [0, 0, 0, 0]))
    assert _status(checks)["beats_primary_benchmark_after_costs"] == "PASS"
    assert _status(checks)["not_dependent_on_single_fold"] == "FAIL"
    assert figures["leave_one_out_mean_excess_min_pp"] < 0
    assert figures["fold_whose_removal_raises_mean_most"] != 1  # removing fold 1 LOWERS the mean
    # mean +9.25 is positive; only removing fold 1 makes it negative
    assert figures["folds_whose_removal_alone_flips_the_mean"] == [1]


def test_every_fold_that_alone_flips_a_negative_mean_is_named():
    # excess -20, -18, 1, 2, 3: mean -6.4; drop fold 1 -> -3.0, drop fold 2 -> -3.5 (no flip)
    _, fig = sv.edge_checks(_evidence([-20, -18, 1, 2, 3], [0, 0, 0, 0, 0]))
    assert fig["folds_whose_removal_alone_flips_the_mean"] == []
    # excess -20, -19, 6, 6, 6, 6: mean -2.5; drop fold 1 -> +1.0, drop fold 2 -> +0.8
    _, fig = sv.edge_checks(_evidence([-20, -19, 6, 6, 6, 6], [0, 0, 0, 0, 0, 0]))
    assert fig["folds_whose_removal_alone_flips_the_mean"] == [1, 2]


def test_drawdown_tolerance_is_relative_to_the_benchmark():
    ok, _ = sv.edge_checks(_evidence([5], [0], dd=(-14.0, -10.0)))
    bad, _ = sv.edge_checks(_evidence([5], [0], dd=(-16.0, -10.0)))
    assert _status(ok)["drawdown_within_tolerance"] == "PASS"
    assert _status(bad)["drawdown_within_tolerance"] == "FAIL"


def test_live_needs_every_sufficient_horizon_positive_and_ignores_thin_ones():
    fail, _ = sv.edge_checks(_evidence([5], [0], live=((1, 0.5, True), (10, -0.1, True))))
    thin, _ = sv.edge_checks(_evidence([5], [0], live=((1, 0.5, True), (20, -9.0, False))))
    none, _ = sv.edge_checks(_evidence([5], [0], live=((20, 3.0, False),)))
    assert _status(fail)["live_paper_beats_benchmark"] == "FAIL"
    assert _status(thin)["live_paper_beats_benchmark"] == "PASS"
    assert _status(none)["live_paper_beats_benchmark"] == "NOT_EVALUATED"


def test_ineligible_evidence_is_recorded_not_borrowed():
    checks, _ = sv.edge_checks(_evidence([5], [0], ineligible=[
        {"check": "beats_random_membership_baseline", "source": "p19", "reason": "withdrawn: contaminated"}]))
    c = next(c for c in checks if c.name == "beats_random_membership_baseline")
    assert c.status == "NOT_EVALUATED" and "contaminated" in c.detail


ALL_ELIGIBLE = [
    {"check": name, "source": "exp", "measured": 1.0, "threshold": 0.0, "comparison": ">", "detail": name}
    for name in sv.COMPARISON_CHECKS
]
ALL_MEASUREMENT_PASS = {node: {"status": "passed", "message": ""} for node in sv.referenced_tests()}
GREEN = {"tests": 10, "passed": 10, "failures": 0, "errors": 0, "skipped": 0, "xfailed": 0}


def _verdict(evidence, outcomes=ALL_MEASUREMENT_PASS, suite=GREEN):
    return sv.compute_verdict(evidence=evidence, evidence_sha256="abc", outcomes=outcomes, suite=suite,
                              code_revision="rev", generated_at="2026-09-13T00:00:00+00:00")


def test_an_edge_pass_is_reachable_with_eligible_evidence():
    v = _verdict(_evidence([6, 4, 5, 7], [2, 1, 2, 3], eligible=ALL_ELIGIBLE))
    assert (v.measurement_verdict, v.edge_verdict, v.promotion_verdict) == ("PASS", "PASS", "DEFERRED")


def test_approval_needs_governance_too():
    gov = {k: {"status": "PASS", "detail": "done"} for k in ("data_terms_reviewed", "scope_approved")}
    v = _verdict(_evidence([6, 4, 5, 7], [2, 1, 2, 3], eligible=ALL_ELIGIBLE, governance=gov))
    assert v.promotion_verdict == "APPROVED"


def test_missing_comparison_evidence_makes_the_edge_inconclusive_not_passed():
    v = _verdict(_evidence([6, 4, 5, 7], [2, 1, 2, 3]))
    assert (v.edge_verdict, v.promotion_verdict) == ("INCONCLUSIVE", "DEFERRED")


def test_a_known_defect_blocks_the_edge_even_when_every_edge_check_passes():
    outcomes = dict(ALL_MEASUREMENT_PASS)
    gap = next(n for n in sv.referenced_tests() if "gap_down" in n)
    outcomes[gap] = {"status": "xfailed", "message": "KNOWN DEFECT"}
    v = _verdict(_evidence([6, 4, 5, 7], [2, 1, 2, 3], eligible=ALL_ELIGIBLE), outcomes=outcomes)
    assert (v.measurement_verdict, v.edge_verdict, v.promotion_verdict) == ("FAIL", "BLOCKED", "BLOCKED")


def test_an_unreconciled_live_ledger_fails_measurement():
    ev = _evidence([6, 4, 5, 7], [2, 1, 2, 3], eligible=ALL_ELIGIBLE)
    ev["paper_ledger"]["reconciliation"]["residual"] = "0.02"
    assert _verdict(ev).measurement_verdict == "FAIL"


def test_a_red_suite_fails_measurement():
    red = dict(GREEN, failures=1)
    assert _verdict(_evidence([6], [2], eligible=ALL_ELIGIBLE), suite=red).measurement_verdict == "FAIL"
