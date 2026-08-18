/**
 * Compact market overview.
 *
 * Shows only what the backend actually provides:
 *   NIFTY 50 live level       <- fast-quote tier (^NSEI), when available
 *   NIFTY 50 regime close      <- /daily-signals (regime fields)
 *   market status             <- /market/overview `source`
 *   advancers / decliners     <- /market/overview
 *
 * SENSEX IS DELIBERATELY ABSENT. No endpoint publishes it, and a placeholder
 * number on a market overview is exactly the kind of fabrication that makes a
 * financial product untrustworthy. When a feed exists it can be added here.
 */

import LiveQuote from "./LiveQuote";
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

export default function MarketStrip({ overview, signals, loading, error, onRetry, quotes = {} }) {
  if (loading) return <LoadingState rows={2} />;
  if (error) return <ErrorState message={error} onRetry={onRetry} />;
  if (!overview) return null;

  const total = overview.advancing + overview.declining + overview.unchanged;
  const upW = total ? (overview.advancing / total) * 100 : 0;
  const downW = total ? (overview.declining / total) * 100 : 0;

  const live = overview.source === "live";
  const regimeClose = signals?.nifty_close;
  const ma = signals?.nifty_200dma;
  // Distance from the 200DMA is the regime input the strategy actually uses.
  // Measured against the REGIME close, never the live tick: this is the number
  // that decided today's exposure, and recomputing it intraday would show a
  // regime the strategy never acted on.
  const vsMa = regimeClose != null && ma ? ((regimeClose - ma) / ma) * 100 : null;

  // The live index, when the fast tier has it. Shown as the headline because it
  // is what "NIFTY 50 right now" means; the regime close stays visible beneath
  // it rather than being replaced.
  const indexQuote = quotes["^NSEI"] || null;
  const headline = indexQuote ? indexQuote.price : regimeClose;

  return (
    <div className="mkt">
      <Cell
        label="NIFTY 50"
        value={headline != null ? headline.toLocaleString("en-IN", { minimumFractionDigits: 2 }) : "—"}
        meta={
          <>
            {indexQuote && (
              <span className={indexQuote.change >= 0 ? "up" : "down"}>
                {indexQuote.change >= 0 ? "+" : ""}{indexQuote.change} ({indexQuote.change_pct}%)
              </span>
            )}
            {vsMa == null ? (
              <span className="flat">200DMA unavailable</span>
            ) : (
              <span className={vsMa >= 0 ? "up" : "down"} style={{ display: "block" }}>
                {pct(vsMa)} vs 200-day avg
                {indexQuote && regimeClose != null && (
                  <span className="flat"> · regime close {regimeClose.toLocaleString("en-IN")}</span>
                )}
              </span>
            )}
          </>
        }
      >
        <LiveQuote quote={indexQuote} />
      </Cell>

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
