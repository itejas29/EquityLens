import { useEffect, useState } from "react";
import { apiClient } from "../api/client";

/**
 * Inline price sparkline from a stock's real closing prices.
 *
 * Renders nothing when there is no series behind it. That is deliberate: the
 * versions this replaced drew a fixed upward polyline for every symbol, so a
 * falling stock still showed a rising chart. An absent chart is honest; a
 * decorative one is not.
 */
export default function Sparkline({ symbol, width = 120, height = 30, days = 30 }) {
  const [closes, setCloses] = useState(null);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .get(`/market/sparkline/${symbol}`, { params: { days } })
      .then((res) => !cancelled && setCloses(res.data.closes))
      .catch(() => !cancelled && setCloses([]));
    return () => {
      cancelled = true;
    };
  }, [symbol, days]);

  if (!closes || closes.length < 2) {
    return <span className="sparkline-empty" style={{ width, height }} aria-hidden="true" />;
  }

  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const span = max - min || 1; // a perfectly flat series would divide by zero
  const step = width / (closes.length - 1);
  const points = closes
    .map((c, i) => `${(i * step).toFixed(1)},${(height - ((c - min) / span) * height).toFixed(1)}`)
    .join(" ");

  const rising = closes[closes.length - 1] >= closes[0];
  const pct = ((closes[closes.length - 1] - closes[0]) / closes[0]) * 100;

  return (
    <svg
      width={width}
      height={height}
      className="sparkline-svg"
      role="img"
      aria-label={`${days}-day trend for ${symbol}: ${rising ? "up" : "down"} ${Math.abs(pct).toFixed(1)}%`}
    >
      <polyline
        points={points}
        fill="none"
        stroke={rising ? "var(--emerald)" : "var(--rose)"}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
