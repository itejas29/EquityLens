/**
 * Shared UI primitives for the EquityLens redesign.
 *
 * Every one of these renders only what it is given. None invents a price, a
 * change, a score or a symbol — when a value is missing they render an em dash
 * or an explicit empty state, so a gap in the API is always visible as a gap.
 */

import { Link } from "react-router-dom";

/* ------------------------------------------------------------ formatting -- */

export function inr(v, digits = 2) {
  if (v == null || Number.isNaN(v)) return "—";
  return `₹${v.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

export function pct(v, digits = 2) {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

export function compactNum(v) {
  if (v == null) return "—";
  if (v >= 1e7) return `${(v / 1e7).toFixed(2)} Cr`;
  if (v >= 1e5) return `${(v / 1e5).toFixed(2)} L`;
  return v.toLocaleString("en-IN");
}

export function fmtDate(d) {
  if (!d) return "—";
  return new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", {
    day: "numeric", month: "short", year: "numeric",
  });
}

/** Direction class + glyph. The glyph matters: colour alone must never be the
 *  only carrier of up/down for colour-blind readers. */
export function dir(v) {
  if (v == null || v === 0) return { cls: "flat", glyph: "" };
  return v > 0 ? { cls: "up", glyph: "▲" } : { cls: "down", glyph: "▼" };
}

/* ---------------------------------------------------------------- layout -- */

export function SectionHeader({ title, sub, action }) {
  return (
    <div className="section-head">
      <div>
        <h2>{title}</h2>
        {sub && <div className="section-sub">{sub}</div>}
      </div>
      {action}
    </div>
  );
}

/* ---------------------------------------------------------------- states -- */

export function LoadingState({ rows = 5 }) {
  return (
    <div className="panel" style={{ padding: 14 }} aria-busy="true" aria-live="polite">
      <span className="visually-hidden">Loading</span>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} style={{ display: "flex", gap: 12, alignItems: "center", padding: "9px 0" }}>
          <div className="skel" style={{ width: 22, height: 22, borderRadius: 4 }} />
          <div style={{ flex: 1 }}>
            <div className="skel" style={{ width: "26%", height: 11, marginBottom: 6 }} />
            <div className="skel" style={{ width: "42%", height: 9 }} />
          </div>
          <div className="skel" style={{ width: 74, height: 13 }} />
          <div className="skel" style={{ width: 58, height: 13 }} />
        </div>
      ))}
    </div>
  );
}

export function EmptyState({ title, body, action, icon }) {
  return (
    <div className="panel">
      <div className="state">
        <div className="state-icon" aria-hidden="true">
          {icon || (
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <rect x="3" y="4" width="18" height="16" rx="2" />
              <path d="M3 10h18M8 4v16" />
            </svg>
          )}
        </div>
        <div className="state-title">{title}</div>
        {body && <p className="state-body">{body}</p>}
        {action && <div className="state-action">{action}</div>}
      </div>
    </div>
  );
}

export function ErrorState({ message, onRetry }) {
  return (
    <div className="panel">
      <div className="state" role="alert">
        <div className="state-icon" aria-hidden="true">
          <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 8v5M12 16h.01" />
          </svg>
        </div>
        <div className="state-title">Could not load this data</div>
        <p className="state-body">{message || "The request failed."}</p>
        {onRetry && (
          <div className="state-action">
            <button className="btn" onClick={onRetry}>Try again</button>
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- badges -- */

/** Maps a v1 trigger_state to an action. Only ever reflects what the API said;
 *  it never derives a call from prices on the client. */
const CALL = {
  IN_ZONE: { label: "IN ENTRY ZONE", cls: "badge-buy" },
  BELOW_ZONE: { label: "BELOW ENTRY", cls: "badge-buy" },
  // Explicitly directive: price has run past the frozen zone, so the call is
  // not to buy it here. Naming the action stops "ABOVE ENTRY" being read as a
  // bullish confirmation.
  ABOVE_ZONE: { label: "ABOVE ENTRY — DO NOT CHASE", cls: "badge-wait" },
  UNKNOWN: { label: "WAIT", cls: "badge-mute" },
};

export function SignalBadge({ triggerState, signal }) {
  if (triggerState) {
    const c = CALL[triggerState] || CALL.UNKNOWN;
    return <span className={`badge ${c.cls}`}>{c.label}</span>;
  }
  if (!signal) return <span className="badge badge-mute">—</span>;
  const cls = signal.includes("ACCUMULATE") ? "badge-buy"
    : signal.includes("AVOID") ? "badge-avoid" : "badge-mute";
  return <span className={`badge ${cls}`}>{signal.replace(/_/g, " ")}</span>;
}

export function ScoreBadge({ value, showBar = true }) {
  if (value == null) return <span className="flat">—</span>;
  return (
    <div>
      <span className="score num">
        {value.toFixed(0)}<span className="score-max">/100</span>
      </span>
      {showBar && (
        <div className="score-bar" aria-hidden="true">
          <div className="score-fill" style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
        </div>
      )}
    </div>
  );
}

export function Change({ value, absolute }) {
  const d = dir(value);
  return (
    <span className={`num ${d.cls}`} style={{ fontWeight: 600 }}>
      {d.glyph} {absolute != null ? `${inr(Math.abs(absolute))} ` : ""}
      {value == null ? "—" : `${Math.abs(value).toFixed(2)}%`}
    </span>
  );
}

/* ----------------------------------------------------------- stock cells -- */

export function StockCell({ symbol, company, sector }) {
  return (
    <div style={{ minWidth: 0 }}>
      <Link to={`/stocks/${symbol}`} className="sym">{symbol}</Link>
      <div className="co">{company || sector || "—"}</div>
    </div>
  );
}

/* ------------------------------------------------------------ provenance -- */

export function ProvenanceStrip({ data }) {
  if (!data) return null;
  const stale = data.is_current === false;
  return (
    <div className={`prov ${stale ? "prov--stale" : ""}`}>
      <span className="prov-item">
        <span className="prov-k">Signals</span>
        <span className="prov-v num">{fmtDate(data.date)}</span>
      </span>
      <span className="prov-item">
        <span className="prov-k">Market data through</span>
        <span className="prov-v num">{fmtDate(data.market_data_through)}</span>
      </span>
      <span className="prov-item">
        <span className="prov-k">Pipeline</span>
        <span className={`prov-v ${data.pipeline_status === "complete" ? "up" : "down"}`}>
          {data.pipeline_status === "complete" ? "Complete" : "Incomplete"}
        </span>
      </span>
      {data.regime && (
        <span className="prov-item">
          <span className="prov-k">Regime</span>
          <span className={`prov-v ${data.regime === "bull" ? "up" : "down"}`}>
            {data.regime === "bull" ? "Bull" : "Bear"} · {Math.round((data.regime_exposure ?? 1) * 100)}% exposure
          </span>
        </span>
      )}
      <span className="prov-item">
        <span className="prov-k">Strategy</span>
        <span className="prov-v" title={data.strategy_description}>{data.strategy_version}</span>
      </span>
      {stale && (
        <span className="prov-note">
          Not today’s list — these are the latest available picks, from {fmtDate(data.date)}.
        </span>
      )}
    </div>
  );
}
