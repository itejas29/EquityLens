/**
 * Shared liveness labelling for Tier-2 fast quotes.
 *
 * The rule this file exists to enforce: a price is only ever labelled "Live"
 * when a fast quote for that symbol actually arrived recently. If the fast tier
 * stalls, the last known price stays on screen with its real age shown — it is
 * never relabelled, and the previous close is never quietly substituted and
 * called live.
 */

import { useEffect, useState } from "react";

// Mirrors QUOTE_STALE_AFTER_SECONDS in backend/app/core/fast_quotes_config.py
// (3 ticks of a 10s loop). Kept in sync by hand — the two sides must agree on
// what "live" means or the badge will contradict the data.
export const STALE_AFTER_SECONDS = 30;

/** Seconds since `isoTimestamp`, re-rendering every second. null if absent. */
export function useQuoteAge(isoTimestamp) {
  const [age, setAge] = useState(() => ageOf(isoTimestamp));

  useEffect(() => {
    setAge(ageOf(isoTimestamp));
    if (!isoTimestamp) return undefined;
    const id = setInterval(() => setAge(ageOf(isoTimestamp)), 1_000);
    return () => clearInterval(id);
  }, [isoTimestamp]);

  return age;
}

function ageOf(isoTimestamp) {
  if (!isoTimestamp) return null;
  const t = Date.parse(isoTimestamp);
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.round((Date.now() - t) / 1000));
}

/**
 * "Live" while fresh, "Updated Xs ago" once past STALE_AFTER_SECONDS.
 * Renders nothing when there is no quote at all — an absent price must not be
 * dressed up with a freshness label.
 */
export default function LiveQuote({ quote, marketOpen = true }) {
  const age = useQuoteAge(quote?.timestamp);
  if (!quote || age == null) return null;

  const fresh = marketOpen && age <= STALE_AFTER_SECONDS;

  return (
    <span
      title={`Quote timestamp: ${new Date(quote.timestamp).toLocaleTimeString()}`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        fontSize: 11,
        fontWeight: 600,
        letterSpacing: 0.2,
        color: fresh ? "var(--up, #16a34a)" : "var(--text-3, #888)",
        whiteSpace: "nowrap",
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: fresh ? "var(--up, #16a34a)" : "var(--text-3, #888)",
          opacity: fresh ? 1 : 0.6,
        }}
      />
      {fresh ? "Live" : marketOpen ? `Updated ${formatAge(age)} ago` : `At close · ${formatAge(age)} ago`}
    </span>
  );
}

function formatAge(seconds) {
  if (seconds < 60) return `${seconds}s`;
  const mins = Math.floor(seconds / 60);
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h`;
}
