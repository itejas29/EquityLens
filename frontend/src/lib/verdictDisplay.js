/* ============================================================================
   Strategy verdict -> what the panel shows. Pure, so it is testable without a
   browser (node --test src/lib/*.test.js).

   Every verdict value carries a glyph, a full label and a plain-language
   meaning, so the panel never relies on colour alone. A value outside the
   controlled vocabulary renders as a warning with its raw value, never in the
   favourable tone — the Methodology page once painted every verdict that was
   not REFUTED green, which is exactly the mistake this refuses to repeat.

   Nothing here decides a verdict. Reasons and blocking conditions are read off
   the checks the API returns; the three verdict values are displayed as served.
   ========================================================================= */

export const VERDICT_DISPLAY = {
  PASS: { glyph: "✓", label: "PASS", tone: "up" },
  FAIL: { glyph: "✕", label: "FAIL", tone: "down" },
  BLOCKED: { glyph: "⊘", label: "BLOCKED", tone: "warn" },
  INCONCLUSIVE: { glyph: "?", label: "INCONCLUSIVE", tone: "warn" },
  APPROVED: { glyph: "✓", label: "APPROVED", tone: "up" },
  DEFERRED: { glyph: "…", label: "DEFERRED", tone: "warn" },
};

export function displayFor(value) {
  const known = VERDICT_DISPLAY[value];
  if (known) return { ...known, value };
  return { glyph: "!", label: String(value ?? "UNKNOWN"), tone: "warn", value, unknown: true };
}

export function humanize(name) {
  return String(name).replaceAll("_", " ");
}

const required = (checks) => (checks || []).filter((c) => c.required);
const withStatus = (checks, status) => required(checks).filter((c) => c.status === status);

export function explainVerdict(v) {
  const mChecks = required(v.measurement_checks);
  const mFailed = withStatus(v.measurement_checks, "FAIL");
  const mMissing = withStatus(v.measurement_checks, "NOT_EVALUATED");
  const eFailed = withStatus(v.edge_checks, "FAIL");
  const ePassed = withStatus(v.edge_checks, "PASS");
  const eMissing = withStatus(v.edge_checks, "NOT_EVALUATED");
  const gPending = (v.governance_checks || []).filter((c) => c.status !== "PASS");

  const measurement = displayFor(v.measurement_verdict);
  if (v.measurement_verdict === "PASS") {
    measurement.reason = `All ${mChecks.length} validity checks passed.`;
    measurement.items = [];
  } else if (v.measurement_verdict === "FAIL") {
    measurement.reason = `${mFailed.length} of ${mChecks.length} validity checks failed.`;
    measurement.items = mFailed;
  } else {
    measurement.reason = `${mMissing.length} validity check(s) could not be evaluated.`;
    measurement.items = mMissing;
  }

  const edge = displayFor(v.edge_verdict);
  const evaluated = eFailed.length + ePassed.length;
  if (v.edge_verdict === "BLOCKED") {
    edge.reason =
      `Not judged while measurement is ${v.measurement_verdict}. ` +
      `Of the ${evaluated} edge checks that could be evaluated, ${eFailed.length} fail.`;
    edge.items = eFailed;
  } else if (v.edge_verdict === "FAIL") {
    edge.reason = `${eFailed.length} required edge check(s) failed.`;
    edge.items = eFailed;
  } else if (v.edge_verdict === "INCONCLUSIVE") {
    edge.reason = `No required check failed, but ${eMissing.length} could not be evaluated.`;
    edge.items = eMissing;
  } else {
    edge.reason = "Every required edge check passed.";
    edge.items = [];
  }

  const promotion = displayFor(v.promotion_verdict);
  const blocking = [
    ...mFailed.map((c) => ({ group: "Measurement", check: c })),
    ...mMissing.map((c) => ({ group: "Measurement", check: c })),
    ...eFailed.map((c) => ({ group: "Edge", check: c })),
    ...gPending.map((c) => ({ group: "Governance", check: c })),
  ];
  if (v.promotion_verdict === "APPROVED") {
    promotion.reason = "Measurement, edge and every governance check passed.";
    promotion.blocking = [];
  } else {
    promotion.blocking = v.promotion_verdict === "DEFERRED"
      ? [...blocking, ...eMissing.map((c) => ({ group: "Evidence", check: c }))]
      : blocking;
    const verb = v.promotion_verdict === "DEFERRED" ? "Deferred pending" : "Blocked by";
    promotion.reason = `${verb} ${promotion.blocking.length} condition(s).`;
  }

  return { measurement, edge, promotion };
}

export function shortHash(value, n = 12) {
  if (!value) return "—";
  const s = String(value).replace(/^sha256:/, "");
  return s.slice(0, n);
}
