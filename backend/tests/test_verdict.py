"""The verdict schema, its controlled vocabularies, and the gating rules.

These pin the logic, not any particular strategy's result. The committed
momentum_v1.0 verdict has its own reproducibility test."""

import json
from datetime import date
from decimal import Decimal

import pytest

from app.core.canonical import canonical_json_bytes, first_difference, sha256_hex
from app.core.verdict import (
    Check,
    StrategyVerdict,
    build_summary,
    decide_edge,
    decide_measurement,
    decide_promotion,
)


def c(name, status, required=True):
    return Check(name=name, status=status, required=required, detail=f"{name} is {status}")


def verdict(**overrides):
    base = dict(
        strategy_id="s", strategy_version="v", measurement_verdict="PASS", edge_verdict="FAIL",
        promotion_verdict="BLOCKED", generated_at="2026-09-13T00:00:00+00:00",
        code_revision="abc", data_snapshot="sha256:def", summary="x", evidence={"k": 1},
    )
    base.update(overrides)
    return StrategyVerdict(**base)


# ---------------------------------------------------------------- vocab --

@pytest.mark.parametrize("field,value", [
    ("measurement_verdict", "OK"), ("edge_verdict", "MAYBE"), ("promotion_verdict", "LIVE"),
])
def test_values_outside_the_controlled_vocabulary_are_rejected(field, value):
    with pytest.raises(ValueError):
        verdict(**{field: value})


def test_a_check_status_outside_the_vocabulary_is_rejected():
    with pytest.raises(ValueError):
        Check(name="x", status="PASSED", required=True, detail="d")


def test_a_check_with_no_reason_is_rejected():
    with pytest.raises(ValueError):
        Check(name="x", status="PASS", required=True, detail="")


@pytest.mark.parametrize("field", ["strategy_id", "strategy_version", "code_revision", "data_snapshot", "summary"])
def test_identity_fields_may_not_be_empty(field):
    with pytest.raises(ValueError):
        verdict(**{field: ""})


# ------------------------------------------------------- object invariants --

def test_an_edge_verdict_on_unvalidated_measurement_cannot_be_constructed():
    """The gating holds even for a hand-assembled object, not just for the
    functions that normally build one."""
    with pytest.raises(ValueError):
        verdict(measurement_verdict="FAIL", edge_verdict="FAIL")
    with pytest.raises(ValueError):
        verdict(measurement_verdict="BLOCKED", edge_verdict="PASS")


def test_promotion_cannot_be_approved_without_an_edge():
    with pytest.raises(ValueError):
        verdict(edge_verdict="INCONCLUSIVE", promotion_verdict="APPROVED")


# ------------------------------------------------------------ measurement --

def test_measurement_passes_only_when_every_required_check_passes():
    assert decide_measurement([c("a", "PASS"), c("b", "PASS")]) == "PASS"


def test_one_failed_required_check_fails_measurement():
    assert decide_measurement([c("a", "PASS"), c("b", "FAIL")]) == "FAIL"


def test_an_unevaluated_required_check_blocks_measurement():
    assert decide_measurement([c("a", "PASS"), c("b", "NOT_EVALUATED")]) == "BLOCKED"


def test_fail_outranks_not_evaluated():
    assert decide_measurement([c("a", "NOT_EVALUATED"), c("b", "FAIL")]) == "FAIL"


def test_a_non_required_check_cannot_fail_measurement():
    assert decide_measurement([c("a", "PASS"), c("b", "FAIL", required=False)]) == "PASS"


def test_no_checks_is_blocked_not_a_vacuous_pass():
    assert decide_measurement([]) == "BLOCKED"


# -------------------------------------------------------------------- edge --

@pytest.mark.parametrize("measurement", ["FAIL", "BLOCKED"])
def test_edge_is_blocked_unless_measurement_passes_even_if_checks_pass(measurement):
    assert decide_edge([c("a", "PASS")], measurement) == "BLOCKED"


@pytest.mark.parametrize("measurement", ["FAIL", "BLOCKED"])
def test_edge_is_blocked_unless_measurement_passes_even_if_checks_fail(measurement):
    """Not FAIL: a defect in the measurement can move a result either way."""
    assert decide_edge([c("a", "FAIL")], measurement) == "BLOCKED"


def test_edge_fail_inconclusive_pass():
    assert decide_edge([c("a", "PASS"), c("b", "FAIL")], "PASS") == "FAIL"
    assert decide_edge([c("a", "PASS"), c("b", "NOT_EVALUATED")], "PASS") == "INCONCLUSIVE"
    assert decide_edge([c("a", "PASS"), c("b", "PASS")], "PASS") == "PASS"


def test_edge_fail_outranks_inconclusive():
    assert decide_edge([c("a", "NOT_EVALUATED"), c("b", "FAIL")], "PASS") == "FAIL"


# --------------------------------------------------------------- promotion --

GOV_OK = [c("data_terms_reviewed", "PASS"), c("scope_approved", "PASS")]
GOV_PENDING = [c("data_terms_reviewed", "PASS"), c("scope_approved", "FAIL")]


@pytest.mark.parametrize("measurement,edge,governance,expected", [
    ("PASS", "PASS", GOV_OK, "APPROVED"),
    ("PASS", "PASS", GOV_PENDING, "DEFERRED"),
    ("PASS", "PASS", [], "DEFERRED"),
    ("PASS", "INCONCLUSIVE", GOV_OK, "DEFERRED"),
    ("PASS", "FAIL", GOV_OK, "BLOCKED"),
    ("FAIL", "BLOCKED", GOV_OK, "BLOCKED"),
    ("BLOCKED", "BLOCKED", GOV_OK, "BLOCKED"),
])
def test_promotion_table(measurement, edge, governance, expected):
    assert decide_promotion(measurement, edge, governance) == expected


def test_governance_cannot_rescue_a_failed_edge():
    assert decide_promotion("PASS", "FAIL", GOV_OK) == "BLOCKED"


# ----------------------------------------------------------------- summary --

def test_summary_names_the_checks_that_decided_it():
    m = [c("gap_down_stop_execution", "FAIL"), c("metrics_validated", "PASS")]
    e = [c("beats_primary_benchmark_after_costs", "FAIL"), c("beats_naive_momentum_baseline", "NOT_EVALUATED")]
    text = build_summary("FAIL", "BLOCKED", "BLOCKED", m, e, [])
    assert "Measurement FAIL" in text and "gap_down_stop_execution" in text
    assert "Edge BLOCKED" in text and "beats_primary_benchmark_after_costs" in text
    assert "Promotion BLOCKED" in text


# ----------------------------------------------------------- serialization --

def test_json_round_trip_preserves_the_verdict():
    v = verdict()
    assert StrategyVerdict.from_json(v.to_json()) == v


def test_the_content_hash_ignores_generation_time():
    assert verdict(generated_at="2026-01-01").content_sha256() == verdict(generated_at="2027-01-01").content_sha256()


def test_the_content_hash_changes_with_any_other_field():
    assert verdict().content_sha256() != verdict(evidence={"k": 2}).content_sha256()


def test_an_artifact_edited_after_generation_is_refused():
    """The artifact carries its own hash; changing a verdict by hand breaks it."""
    payload = json.loads(verdict().to_json())
    payload["edge_verdict"] = "PASS"
    payload["promotion_verdict"] = "DEFERRED"
    with pytest.raises(ValueError, match="hash mismatch"):
        StrategyVerdict.from_json(json.dumps(payload))


# ---------------------------------------------------------------- canonical --

def test_canonical_bytes_do_not_depend_on_dict_insertion_order():
    assert canonical_json_bytes({"a": 1, "b": 2}) == canonical_json_bytes({"b": 2, "a": 1})


def test_canonical_keeps_full_float_precision():
    """Rounding here would let two runs that disagree in the 12th digit pass."""
    assert sha256_hex({"x": 0.1 + 0.2}) != sha256_hex({"x": 0.3})


def test_canonical_types():
    out = json.loads(canonical_json_bytes({
        "money": Decimal("1234.50"), "day": date(2026, 9, 13), "nan": float("nan"),
        "tags": {"b", "a"}, "pair": (1, 2),
    }))
    assert out == {"money": "1234.50", "day": "2026-09-13", "nan": "NaN", "tags": ["a", "b"], "pair": [1, 2]}


def test_first_difference_names_artifact_record_field_and_both_values():
    one = {"trade_log": [{"symbol": "AAA", "exit_price": 101.0}, {"symbol": "BBB", "exit_price": 99.5}]}
    two = {"trade_log": [{"symbol": "AAA", "exit_price": 101.0}, {"symbol": "BBB", "exit_price": 99.25}]}
    d = first_difference(one, two)
    assert (d.artifact, d.record, d.field, d.run_one, d.run_two) == ("trade_log", 1, "exit_price", 99.5, 99.25)
    assert "trade_log[1].exit_price" in d.describe()


def test_first_difference_reports_a_record_present_in_only_one_run():
    d = first_difference({"trade_log": [{"s": 1}]}, {"trade_log": [{"s": 1}, {"s": 2}]})
    assert (d.artifact, d.record, d.run_one) == ("trade_log", 1, "<absent>")


def test_first_difference_distinguishes_int_from_float():
    assert first_difference({"x": 1}, {"x": 1.0}) is not None


def test_identical_structures_have_no_difference():
    assert first_difference({"a": [1, {"b": 2.5}]}, {"a": [1, {"b": 2.5}]}) is None
