// node --test "src/lib/*.test.js" — Node's built-in runner; no test dependency added.
import assert from "node:assert/strict";
import test from "node:test";

import { VERDICT_DISPLAY, displayFor, explainVerdict, shortHash } from "./verdictDisplay.js";

const check = (name, status, required = true) => ({ name, status, required, detail: `${name} is ${status}`, evidence: [] });

// Shaped like GET /api/v1/strategy-verdict for the committed momentum_v1.0 verdict.
const blocked = {
  measurement_verdict: "FAIL",
  edge_verdict: "BLOCKED",
  promotion_verdict: "BLOCKED",
  measurement_checks: [
    check("no_lookahead_in_scoring", "PASS"),
    check("gap_down_stop_execution", "FAIL"),
    check("next_session_entry_execution", "FAIL"),
  ],
  edge_checks: [
    check("beats_primary_benchmark_after_costs", "FAIL"),
    check("drawdown_within_tolerance", "PASS"),
    check("beats_naive_momentum_baseline", "NOT_EVALUATED"),
  ],
  governance_checks: [check("scope_approved", "FAIL")],
};

test("every verdict value has a glyph and a full label, not just a colour", () => {
  for (const [value, d] of Object.entries(VERDICT_DISPLAY)) {
    assert.ok(d.glyph, `${value} has no glyph`);
    assert.equal(d.label, value);
    assert.ok(["up", "down", "warn"].includes(d.tone));
  }
});

test("a blocked promotion shows BLOCKED and names what blocks it", () => {
  const { promotion } = explainVerdict(blocked);
  assert.equal(promotion.label, "BLOCKED");
  assert.equal(promotion.glyph, "⊘");
  assert.equal(promotion.tone, "warn");
  const names = promotion.blocking.map((b) => b.check.name);
  assert.deepEqual(names, [
    "gap_down_stop_execution", "next_session_entry_execution",
    "beats_primary_benchmark_after_costs", "scope_approved",
  ]);
  assert.equal(promotion.reason, "Blocked by 4 condition(s).");
});

test("a blocked edge says why it was not judged and still reports what failed", () => {
  const { edge } = explainVerdict(blocked);
  assert.equal(edge.label, "BLOCKED");
  assert.match(edge.reason, /measurement is FAIL/);
  assert.match(edge.reason, /Of the 2 edge checks that could be evaluated, 1 fail/);
  assert.deepEqual(edge.items.map((c) => c.name), ["beats_primary_benchmark_after_costs"]);
});

test("a failed measurement counts and lists its failures", () => {
  const { measurement } = explainVerdict(blocked);
  assert.equal(measurement.label, "FAIL");
  assert.equal(measurement.tone, "down");
  assert.equal(measurement.reason, "2 of 3 validity checks failed.");
  assert.equal(measurement.items.length, 2);
});

test("a deferred promotion also lists evidence that is missing", () => {
  const v = {
    ...blocked,
    measurement_verdict: "PASS", edge_verdict: "INCONCLUSIVE", promotion_verdict: "DEFERRED",
    measurement_checks: [check("a", "PASS")],
    edge_checks: [check("b", "PASS"), check("c", "NOT_EVALUATED")],
  };
  const { promotion, edge } = explainVerdict(v);
  assert.equal(promotion.label, "DEFERRED");
  assert.deepEqual(promotion.blocking.map((b) => `${b.group}:${b.check.name}`), ["Governance:scope_approved", "Evidence:c"]);
  assert.match(edge.reason, /1 could not be evaluated/);
});

test("an unrecognised verdict value is never shown as favourable", () => {
  const d = displayFor("LOOKS_GOOD");
  assert.notEqual(d.tone, "up");
  assert.equal(d.label, "LOOKS_GOOD");
  assert.equal(d.unknown, true);
  assert.equal(displayFor(undefined).label, "UNKNOWN");
});

test("non-required checks do not appear as blocking conditions", () => {
  const v = { ...blocked, measurement_checks: [...blocked.measurement_checks, check("advisory", "FAIL", false)] };
  const names = explainVerdict(v).promotion.blocking.map((b) => b.check.name);
  assert.ok(!names.includes("advisory"));
});

test("short hashes drop the algorithm prefix", () => {
  assert.equal(shortHash("sha256:b9a7552043188e6905e6"), "b9a755204318");
  assert.equal(shortHash(null), "—");
});
