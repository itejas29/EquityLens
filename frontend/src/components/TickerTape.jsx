/**
 * TickerTape — scrolling strip of NSE stocks with live prices.
 *
 * Data sources (in priority order):
 *  1. WebSocket live prices (price + % change, updated every ~60 s)
 *  2. Recommendations API (score + signal, polled every 60 s as fallback)
 *
 * The tape always shows: symbol · live price (₹) · % change · score signal dot
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiClient } from "../api/client";
import { useLivePrices } from "../lib/useLivePrices";

const SIGNAL_DIRECTION = {
  STRONG_ACCUMULATE: "up",
  ACCUMULATE: "up",
  WATCH: "flat",
  AVOID: "down",
  STRONG_AVOID: "down",
};

export default function TickerTape() {
  const [recs, setRecs] = useState([]);
  // `status` is what matters here, not `connected`. The socket being up does
  // not mean NSE is open — outside market hours the server sends the frozen
  // last-session snapshot, and the tape holds at those closing prices.
  const { prices, status, sessionDate } = useLivePrices();

  // Poll recommendations for signal + score (these change rarely)
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await apiClient.get("/recommendations", { params: { limit: 40 } });
        if (!cancelled) setRecs(res.data);
      } catch {
        // decorative — fail silently
      }
    }
    load();
    const id = setInterval(load, 60_000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  if (recs.length === 0 && Object.keys(prices).length === 0) return null;

  // Merge: use recs as the base list (for signal/score), overlay live prices
  const items = recs.length > 0 ? recs : Object.keys(prices).map((sym) => ({ symbol: sym }));
  const loop = [...items, ...items]; // double for seamless CSS scroll loop

  return (
    <div className={`ticker-tape ticker-tape--${status}`} aria-hidden="true">
      {status === "live" ? (
        <span className="ticker-live-dot" title="Live prices — market open" aria-hidden="true" />
      ) : status === "closed" ? (
        <span className="ticker-closed-flag" title={`Market closed — last prices from ${sessionDate || "the previous session"}`}>
          CLOSED
        </span>
      ) : null}
      <div className="ticker-tape-track">
        {loop.map((rec, i) => {
          const sym = rec.symbol;
          const lp = prices[sym];
          const direction = SIGNAL_DIRECTION[rec.signal] || "flat";
          const hasPrice = lp != null;
          const isUp = hasPrice && lp.change_pct > 0;
          const isDown = hasPrice && lp.change_pct < 0;

          return (
            <Link
              to={`/stocks/${sym}`}
              className={`ticker-item ticker-${direction}`}
              key={`${sym}-${i}`}
              tabIndex={-1}
            >
              <span className="ticker-dot" />
              <span className="ticker-symbol">{sym}</span>

              {hasPrice ? (
                <>
                  <span className="ticker-price">₹{lp.price.toLocaleString("en-IN")}</span>
                  <span className={`ticker-change ${isUp ? "ticker-up" : isDown ? "ticker-down" : "ticker-flat"}`}>
                    {isUp ? "▲" : isDown ? "▼" : ""}
                    {Math.abs(lp.change_pct).toFixed(2)}%
                  </span>
                </>
              ) : (
                <span className="ticker-score">
                  {rec.overall_score != null ? rec.overall_score.toFixed(0) : "—"}
                </span>
              )}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
