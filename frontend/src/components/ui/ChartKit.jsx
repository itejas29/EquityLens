import { ReferenceLine } from "recharts";

/* ============================================================================
   Shared chart furniture.

   Built once here rather than per page because three pages draw equity curves
   (AI Trading, Paper Trading, Backtest) and they were drifting apart — each had
   its own tooltip styling, its own gradient id, its own axis defaults.

   The house style, and why:

     - A tooltip states the DELTA, not just the two values. A reader comparing a
       portfolio to a benchmark is doing that subtraction in their head; doing
       it for them is the whole job of the panel.
     - Numbers use tabular figures everywhere. Digits that shift column between
       frames are the fastest way to make a live chart feel amateur.
     - The benchmark is dashed and the portfolio solid. Colour alone would fail
       for a colour-blind reader and in a printed screenshot.
     - A reference line at starting capital, because "is it up or down" should
       not require reading the axis.
   ========================================================================= */

export const CHART_MARGIN = { top: 12, right: 14, left: 4, bottom: 0 };

/** Gradient defs. `id` must be unique per chart on the page. */
export function AreaGradient({ id, color = "var(--accent)", from = 0.28, to = 0 }) {
  return (
    <defs>
      <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor={color} stopOpacity={from} />
        <stop offset="100%" stopColor={color} stopOpacity={to} />
      </linearGradient>
    </defs>
  );
}

/** Break-even marker. Rendered only when a baseline is known. */
export function BaselineRule({ y, label = "start" }) {
  if (y == null) return null;
  return (
    <ReferenceLine
      y={y}
      stroke="var(--text-3)"
      strokeDasharray="2 4"
      strokeOpacity={0.55}
      label={{
        value: label,
        position: "insideTopLeft",
        fill: "var(--text-3)",
        fontSize: 10,
        offset: 6,
      }}
    />
  );
}

/**
 * Tooltip that reports the comparison, not just the readings.
 *
 * `series` maps dataKey -> { label, color, dashed }. When exactly two are
 * present the difference between them is shown beneath, which is the number
 * the reader actually wants.
 */
export function CompareTooltip({ active, payload, label, series, format, labelFormat }) {
  if (!active || !payload?.length) return null;

  const rows = payload
    .filter((p) => series[p.dataKey])
    .map((p) => ({ ...series[p.dataKey], key: p.dataKey, value: p.value }));
  if (!rows.length) return null;

  const [a, b] = rows;
  const delta = rows.length === 2 && a.value != null && b.value != null ? a.value - b.value : null;
  const deltaPct = delta != null && b.value ? (delta / b.value) * 100 : null;

  return (
    <div
      style={{
        background: "var(--surface)",
        border: "1px solid var(--line-strong)",
        borderRadius: "var(--r)",
        boxShadow: "var(--shadow-pop)",
        padding: "9px 11px",
        minWidth: 176,
        fontVariantNumeric: "tabular-nums",
      }}
    >
      <div
        style={{
          fontSize: 10.5,
          fontWeight: 600,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          color: "var(--text-3)",
          marginBottom: 7,
        }}
      >
        {labelFormat ? labelFormat(label) : label}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
        {rows.map((r) => (
          <div key={r.key} style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span
              aria-hidden="true"
              style={{
                width: 10,
                height: r.dashed ? 0 : 10,
                borderRadius: r.dashed ? 0 : 2,
                borderTop: r.dashed ? `2px dashed ${r.color}` : undefined,
                background: r.dashed ? undefined : r.color,
                flex: "0 0 auto",
              }}
            />
            <span style={{ fontSize: 12, color: "var(--text-3)", flex: 1 }}>{r.label}</span>
            <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-1)" }}>
              {format ? format(r.value) : r.value}
            </span>
          </div>
        ))}
      </div>

      {delta != null && (
        <div
          style={{
            marginTop: 7,
            paddingTop: 7,
            borderTop: "1px solid var(--line)",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <span style={{ fontSize: 11.5, color: "var(--text-3)", flex: 1 }}>Difference</span>
          <span
            className={delta > 0 ? "up" : delta < 0 ? "down" : ""}
            style={{ fontSize: 12.5, fontWeight: 700 }}
          >
            {delta > 0 ? "+" : ""}
            {format ? format(delta) : delta}
            {deltaPct != null && (
              <span style={{ fontSize: 11, fontWeight: 500, opacity: 0.8 }}>
                {"  "}
                {deltaPct > 0 ? "+" : ""}
                {deltaPct.toFixed(2)}%
              </span>
            )}
          </span>
        </div>
      )}
    </div>
  );
}

/** Legend row. Mirrors the tooltip's swatch language so the two agree. */
export function ChartLegend({ series }) {
  return (
    <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 11.5 }}>
      {Object.values(series).map((s) => (
        <span
          key={s.label}
          style={{ display: "inline-flex", alignItems: "center", gap: 7, color: "var(--text-3)" }}
        >
          <span
            aria-hidden="true"
            style={{
              width: 12,
              height: s.dashed ? 0 : 10,
              borderRadius: s.dashed ? 0 : 2,
              borderTop: s.dashed ? `2px dashed ${s.color}` : undefined,
              background: s.dashed ? undefined : s.color,
            }}
          />
          {s.label}
        </span>
      ))}
    </div>
  );
}

/** Axis props shared by every equity chart, so they cannot drift. */
export const axisProps = {
  stroke: "var(--text-3)",
  fontSize: 11,
  tickLine: false,
  axisLine: false,
};
