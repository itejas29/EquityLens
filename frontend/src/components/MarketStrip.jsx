/**
 * Compact market overview.
 *
 * Shows only what the backend actually provides:
 *   NIFTY 50 level + 200DMA   <- /daily-signals (regime fields)
 *   market status             <- /market/overview `source`
 *   advancers / decliners     <- /market/overview
 *
 * SENSEX IS DELIBERATELY ABSENT. No endpoint publishes it, and a placeholder
 * number on a market overview is exactly the kind of fabrication that makes a
 * financial product untrustworthy. When a feed exists it can be added here.
 */

import { LoadingState, ErrorState, pct } from "./ui/Primitives";

function Cell({ label, value, meta, children }) {
  return (
    <div className="mkt-cell">
      <div className="mkt-label">{label}</div>
      <div className="mkt-value num">{value}</div>
      {meta && <div className="mkt-meta">{meta}</div>}
      {children}
    </div>
  );
}

export default function MarketStrip({ overview, signals, loading, error, onRetry }) {
  if (loading) return <LoadingState rows={2} />;
  if (error) return <ErrorState message={error} onRetry={onRetry} />;
  if (!overview) return null;

  const total = overview.advancing + overview.declining + overview.unchanged;
  const upW = total ? (overview.advancing / total) * 100 : 0;
  const downW = total ? (overview.declining / total) * 100 : 0;

  const live = overview.source === "live";
  const nifty = signals?.nifty_close;
  const ma = signals?.nifty_200dma;
  // Distance from the 200DMA is the regime input the strategy actually uses,
  // so it is more informative here than a day change we do not have for the index.
  const vsMa = nifty != null && ma ? ((nifty - ma) / ma) * 100 : null;

  return (
    <div className="mkt">
      <Cell
        label="NIFTY 50"
        value={nifty != null ? nifty.toLocaleString("en-IN", { minimumFractionDigits: 2 }) : "—"}
        meta={
          vsMa == null ? (
            <span className="flat">200DMA unavailable</span>
          ) : (
            <span className={vsMa >= 0 ? "up" : "down"}>
              {pct(vsMa)} vs 200-day avg
            </span>
          )
        }
      />

      <Cell
        label="Market"
        value={live ? "Open" : "Closed"}
        meta={
          <span className="flat" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span className={`dot ${live ? "dot-live" : "dot-closed"}`} aria-hidden="true" />
            {live ? "Live prices" : "Last completed session"}
          </span>
        }
      />

      <Cell label="Advancing" value={overview.advancing} meta={<span className="up">of {total} tracked</span>}>
        <div className="breadth-bar" role="img" aria-label={`${overview.advancing} advancing, ${overview.declining} declining`}>
          <span className="breadth-up" style={{ width: `${upW}%` }} />
          <span style={{ width: `${100 - upW - downW}%` }} />
          <span className="breadth-down" style={{ width: `${downW}%` }} />
        </div>
      </Cell>

      <Cell label="Declining" value={overview.declining} meta={<span className="down">{overview.unchanged} unchanged</span>} />
    </div>
  );
}
