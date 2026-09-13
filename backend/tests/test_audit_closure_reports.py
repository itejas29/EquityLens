"""The audit-closure deliverables agree with the machine-readable verdict.

Enforced three ways: the committed files are byte-identical to a fresh build;
the ledger ties out to the reconciliation it came with; and the report's
conclusions are derived, not typed — built from a different verdict, they
change. (The first version of the builder hardcoded "Decision: reject" and
"Does it beat the benchmark? No." — right today, and silently wrong the day the
evidence changed.)
"""

import csv
import importlib.util
import io
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
OUT = REPO / "docs/audit/momentum_v1.0"


def _builder():
    spec = importlib.util.spec_from_file_location("closure", BACKEND / "scripts/build_audit_closure.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_committed_deliverables_are_exactly_what_the_builder_produces(tmp_path):
    files = _builder().build_all(tmp_path)
    assert set(files) == {
        "momentum_v1.0_audit_closure.md", "momentum_v1.0_metrics.json",
        "momentum_v1.0_trade_ledger.csv", "momentum_v1.0_bug_impact_register.md",
    }
    for name in files:
        assert (OUT / name).read_bytes() == (tmp_path / name).read_bytes(), (
            f"{name} differs from a fresh build — regenerate with scripts/build_audit_closure.py"
        )


def test_the_report_and_metrics_state_the_artifacts_verdict():
    b = _builder()
    verdict = b.StrategyVerdict.from_json(b.ARTIFACT.read_text())
    closure = (OUT / "momentum_v1.0_audit_closure.md").read_text()
    for label, value in (("Is the measurement valid?", verdict.measurement_verdict),
                         ("Does the strategy have an edge?", verdict.edge_verdict),
                         ("May it be promoted beyond research?", verdict.promotion_verdict)):
        assert f"| {label} | **{value}** |" in closure
    assert verdict.summary in closure

    metrics = json.loads((OUT / "momentum_v1.0_metrics.json").read_text())
    assert metrics["verdict"] == {
        "measurement": verdict.measurement_verdict, "edge": verdict.edge_verdict,
        "promotion": verdict.promotion_verdict, "content_sha256": verdict.content_sha256(),
    }


def test_the_trade_ledger_ties_out_to_its_reconciliation():
    rec = json.loads((OUT / "raw/paper_ledger.json").read_text())["reconciliation"]
    rows = list(csv.DictReader(io.StringIO((OUT / "momentum_v1.0_trade_ledger.csv").read_text())))
    assert len(rows) == rec["trades"]
    assert sum(r["status"] == "open" for r in rows) == rec["open_trades"]
    closed_pnl = sum(Decimal(r["net_pnl"]) for r in rows if r["status"] == "closed")
    assert closed_pnl == Decimal(rec["sum_closed_pnl"])
    # Not stored separately, so exported empty — never back-filled.
    assert all(r["gross_pnl"] == r["fees"] == r["slippage"] == r["model_version"] == "" for r in rows)


def test_the_register_covers_every_audit_finding():
    register = (OUT / "momentum_v1.0_bug_impact_register.md").read_text()
    readme = (REPO / "docs/audit/README.md").read_text()
    nums = [line.split(".")[0][4:] for line in readme.splitlines() if _builder().HEADING.match(line)]
    assert nums == [str(i) for i in range(1, len(nums) + 1)], f"findings are not numbered 1..n: {nums}"
    for n in nums:
        assert f"| ID | {n} |" in register
    meta = json.loads((REPO / "docs/audit/findings_metadata.json").read_text())
    assert {k for k in meta if not k.startswith("_")} == set(nums), "metadata and README findings disagree"


def test_conclusions_follow_the_verdict_rather_than_being_typed():
    b = _builder()
    verdict = b.StrategyVerdict.from_json(b.ARTIFACT.read_text())
    evidence = json.loads(b.EVIDENCE.read_text())
    ledger = json.loads(b.LEDGER.read_text())

    # A counterfactual verdict: measurement PASS, edge FAIL.
    ev = json.loads(json.dumps(verdict.evidence))
    for c in ev["measurement_checks"]:
        c["status"], c["detail"] = "PASS", "passed"
    counterfactual = replace(verdict, measurement_verdict="PASS", edge_verdict="FAIL",
                             promotion_verdict="BLOCKED", evidence=ev)
    text = b.build_closure(counterfactual, evidence, b.build_metrics(counterfactual, evidence, ledger), ledger)

    assert "1. **Is the measurement system correct?** Yes — PASS." in text
    assert "The strategy failed `beats_primary_benchmark_after_costs`" in text
    assert "cannot yet certify" not in text
    assert "Fix the open execution defects" not in text

    original = (OUT / "momentum_v1.0_audit_closure.md").read_text()
    assert "1. **Is the measurement system correct?** Not yet — FAIL." in original
    assert "Fix the open execution defects" in original
