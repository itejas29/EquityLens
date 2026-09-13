import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiClient, apiErrorMessage } from "../api/client";
import { REPO_URL } from "../lib/constants";
import { explainVerdict, humanize, shortHash } from "../lib/verdictDisplay";

/* ============================================================================
   Strategy status — the machine-generated verdict on the strategy under test.

   Displays GET /strategy-verdict as served. It computes nothing that could
   disagree with the artifact: the three verdicts are shown verbatim, and the
   reasons and blocking conditions are read off the checks the API returns
   (lib/verdictDisplay.js, tested with node --test).

   Colour is never the only signal. Every verdict carries a glyph and its full
   label; FAIL carries its explanation; BLOCKED carries its blocking conditions.
   ========================================================================= */

const MONO = "var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace)";

function Tile({ question, d, children }) {
  return (
    <div
      role="group"
      aria-label={`${question}: ${d.label}`}
      style={{
        border: "1px solid var(--line)", borderTop: `3px solid var(--${d.tone})`,
        borderRadius: "var(--r)", background: "var(--surface)", padding: "13px 15px",
        display: "flex", flexDirection: "column", gap: 7, minWidth: 0,
      }}
    >
      <span style={{
        fontSize: 10, fontWeight: 700, letterSpacing: "0.09em",
        textTransform: "uppercase", color: "var(--text-3)",
      }}>{question}</span>
      <span style={{ display: "flex", alignItems: "baseline", gap: 8, color: `var(--${d.tone})` }}>
        <span aria-hidden="true" style={{ fontSize: 19, fontWeight: 700, lineHeight: 1 }}>{d.glyph}</span>
        <span style={{ fontSize: 19, fontWeight: 800, letterSpacing: "0.04em", lineHeight: 1.1 }}>{d.label}</span>
      </span>
      <span style={{ fontSize: 12.5, color: "var(--text-2)", lineHeight: 1.55 }}>{d.reason}</span>
      {children}
    </div>
  );
}

function CheckList({ checks, detail }) {
  if (!checks?.length) return null;
  return (
    <ul style={{ margin: 0, paddingLeft: 16, display: "flex", flexDirection: "column", gap: 5 }}>
      {checks.map((c) => (
        <li key={c.name} style={{ fontSize: 12, color: "var(--text-2)", lineHeight: 1.5 }}>
          <span style={{ fontFamily: MONO, fontSize: 11.5, color: "var(--text-1)" }}>{c.name}</span>
          {detail && <div style={{ color: "var(--text-3)", marginTop: 2 }}>{c.detail}</div>}
        </li>
      ))}
    </ul>
  );
}

function Meta({ label, children }) {
  return (
    <span style={{ whiteSpace: "nowrap" }}>
      <span style={{ color: "var(--text-3)" }}>{label} </span>
      <span style={{ color: "var(--text-2)", fontFamily: MONO }}>{children}</span>
    </span>
  );
}

export default function StrategyVerdictPanel({ compact = false }) {
  const [v, setV] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    apiClient
      .get("/strategy-verdict")
      .then((res) => !cancelled && setV(res.data))
      .catch((err) => !cancelled && setError(apiErrorMessage(err)));
    return () => { cancelled = true; };
  }, []);

  const shell = {
    border: "1px solid var(--line-strong, var(--line))", borderRadius: "var(--r)",
    background: "var(--surface)", padding: compact ? "14px 16px" : "18px 20px", marginBottom: compact ? 28 : 8,
  };

  if (error) {
    // Not a silent gap: a missing verdict is itself worth saying.
    return (
      <div style={shell} role="alert">
        <span style={{ fontSize: 12.5, color: "var(--warn)" }}>⚠ Strategy verdict unavailable — {error}</span>
      </div>
    );
  }
  if (!v) {
    return <div style={{ ...shell, minHeight: 96 }} aria-busy="true" aria-label="Loading strategy verdict" />;
  }

  const { measurement, edge, promotion } = explainVerdict(v);
  const liveCheck = v.edge_checks.find((c) => c.name === "live_paper_beats_benchmark");
  const suite = v.test_suite;
  const evaluated = v.generated_at
    ? new Date(v.generated_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })
    : "—";

  return (
    <section style={shell} aria-labelledby="strategy-status-title">
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: "4px 12px", marginBottom: 12 }}>
        <h2 id="strategy-status-title" style={{
          fontSize: 10.5, fontWeight: 700, letterSpacing: "0.16em", textTransform: "uppercase",
          color: "var(--accent)", margin: 0,
        }}>Strategy status</h2>
        <span style={{ fontFamily: MONO, fontSize: 13, fontWeight: 650, color: "var(--text-1)" }}>{v.strategy_id}</span>
        <span style={{ fontFamily: MONO, fontSize: 11, color: "var(--text-3)" }}>{v.strategy_version}</span>
      </div>

      <div style={{
        display: "grid", gap: 11,
        gridTemplateColumns: "repeat(auto-fit, minmax(min(230px, 100%), 1fr))",
      }}>
        <Tile question="Measurement validity" d={measurement}>
          <CheckList checks={measurement.items} detail={!compact} />
        </Tile>
        <Tile question="Strategy edge" d={edge}>
          <CheckList checks={edge.items} detail={!compact} />
        </Tile>
        <Tile question="Promotion status" d={promotion}>
          {promotion.blocking.length > 0 && (
            <ul style={{ margin: 0, paddingLeft: 16, display: "flex", flexDirection: "column", gap: 4 }}>
              {promotion.blocking.map(({ group, check }) => (
                <li key={`${group}:${check.name}`} style={{ fontSize: 12, color: "var(--text-2)", lineHeight: 1.45 }}>
                  <span style={{ color: "var(--text-3)" }}>{group}: </span>
                  <span title={check.detail}>{humanize(check.name)}</span>
                </li>
              ))}
            </ul>
          )}
        </Tile>
      </div>

      <p style={{ fontSize: 12.5, color: "var(--text-2)", lineHeight: 1.65, margin: "14px 0 0", maxWidth: 900 }}>
        <span style={{ fontWeight: 700, color: "var(--text-1)" }}>Reason. </span>{v.summary}
      </p>

      <div style={{
        display: "flex", flexWrap: "wrap", gap: "6px 18px", marginTop: 12, paddingTop: 11,
        borderTop: "1px solid var(--line)", fontSize: 11.5,
      }}>
        <Meta label="Evaluated">{evaluated}</Meta>
        <Meta label="Code">{v.code_revision.slice(0, 7)}</Meta>
        <Meta label="Data snapshot">{shortHash(v.data_snapshot)}</Meta>
        <Meta label="Tests">
          {suite.passed}/{suite.tests} passed · {suite.xfailed} known defects · {suite.skipped} skipped
        </Meta>
        <Meta label="Live paper trading">
          {liveCheck ? `${liveCheck.status.replace("_", " ")} · signals through ${v.live_signals_through ?? "—"}` : "—"}
        </Meta>
        <span style={{ whiteSpace: "nowrap", display: "flex", gap: 14 }}>
          <a href={`${REPO_URL}/blob/main/${v.report_path}`} target="_blank" rel="noreferrer"
             style={{ color: "var(--accent)" }}>Full audit report ↗</a>
          {compact && <Link to="/methodology" style={{ color: "var(--accent)" }}>All evidence →</Link>}
        </span>
      </div>
    </section>
  );
}
