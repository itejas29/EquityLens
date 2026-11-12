/**
 * The AI paper account's equity against NIFTY 50 at the same starting capital.
 * Both lines come from paper_equity_snapshots: total_equity, and nifty_return
 * (cumulative since the account opened) applied to the account's capital — so
 * the comparison starts on the account's own first day, in the same rupees.
 * A snapshot with no NIFTY figure leaves a gap in that line, not a flat zero.
 */

import { useMemo, useState } from "react";
import {
  Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis
} from "recharts";
import { EmptyState, compactNum, inr, fmtDate } from "./ui/Primitives";
import {
  AreaGradient, BaselineRule, CHART_MARGIN, ChartLegend, CompareTooltip, axisProps
} from "./ui/ChartKit";

const EQUITY_SERIES = {
  total_equity: { label: "AI Portfolio", color: "var(--accent)" },
  nifty_equity: { label: "NIFTY 50 (same capital)", color: "var(--warn)", dashed: true },
};

const PERIODS = [
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "All", days: Infinity },
];

export default function EquityChart({ data, virtualCapital }) {
  const [period, setPeriod] = useState("All");

  // NIFTY-equivalent equity: what the same starting capital would be worth
  // just holding the index, computed from nifty_return — already a cumulative
  // % return from account inception, same basis as total_equity's own
  // cumulative_return (see record_paper_snapshot in forward_testing.py) — so
  // the two lines are directly comparable in the same ₹ units, no separate axis.
  const withBenchmark = useMemo(() => {
    if (!data?.length || !virtualCapital) return data || [];
    return data.map((d) => ({
      ...d,
      nifty_equity: d.nifty_return == null ? null : virtualCapital * (1 + d.nifty_return / 100),
    }));
  }, [data, virtualCapital]);

  const filtered = useMemo(() => {
    if (!withBenchmark.length) return [];
    const p = PERIODS.find((x) => x.label === period);
    if (!p || p.days === Infinity) return withBenchmark;
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - p.days);
    return withBenchmark.filter((d) => new Date(d.date) >= cutoff);
  }, [withBenchmark, period]);

  const stats = useMemo(() => {
    if (!filtered.length) return null;
    const first = filtered[0].total_equity;
    const last = filtered[filtered.length - 1].total_equity;
    const ret = ((last - first) / first) * 100;
    const niftyFirst = filtered[0].nifty_equity;
    const niftyLast = filtered[filtered.length - 1].nifty_equity;
    const niftyRet = niftyFirst && niftyLast != null ? ((niftyLast - niftyFirst) / niftyFirst) * 100 : null;
    return { first, last, ret, niftyRet };
  }, [filtered]);

  if (!data || !data.length) {
    return (
      <EmptyState
        title="No equity history yet"
        body="A point is recorded after each trading day's 20:00 data update. The curve appears once the account has its first one."
      />
    );
  }

  const allValues = filtered.flatMap((d) => [d.total_equity, d.nifty_equity]).filter((v) => v != null);
  const minE = Math.min(...allValues);
  const maxE = Math.max(...allValues);
  const pad = (maxE - minE) * 0.12 || 1000;
  // Break-even line. Falls back to the first plotted point when the account's
  // starting capital was not passed, so the rule never sits at an arbitrary y.
  const baseline = virtualCapital ?? filtered[0]?.total_equity ?? null;

  return (
    <div className="panel">
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "14px 18px", borderBottom: "1px solid var(--line)", flexWrap: "wrap", gap: 12,
      }}>
        <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
          {stats && (
            <>
              <div>
                <div style={{ fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-3)" }}>Starting</div>
                <div className="num" style={{ fontSize: 14, fontWeight: 700, color: "var(--text-1)" }}>{inr(stats.first, 0)}</div>
              </div>
              <div>
                <div style={{ fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-3)" }}>Current</div>
                <div className="num" style={{ fontSize: 14, fontWeight: 700, color: "var(--text-1)" }}>{inr(stats.last, 0)}</div>
              </div>
              <div>
                <div style={{ fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-3)" }}>Return</div>
                <div className={`num ${stats.ret > 0 ? "up" : stats.ret < 0 ? "down" : ""}`} style={{ fontSize: 14, fontWeight: 700 }}>
                  {stats.ret > 0 ? "+" : ""}{stats.ret.toFixed(2)}%
                </div>
              </div>
              {stats.niftyRet != null && (
                <div>
                  <div style={{ fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-3)" }}>vs NIFTY</div>
                  <div className={`num ${stats.ret - stats.niftyRet > 0 ? "up" : stats.ret - stats.niftyRet < 0 ? "down" : ""}`} style={{ fontSize: 14, fontWeight: 700 }}>
                    {stats.ret - stats.niftyRet > 0 ? "+" : ""}{(stats.ret - stats.niftyRet).toFixed(2)}%
                  </div>
                </div>
              )}
            </>
          )}
        </div>
        <div style={{ display: "flex", gap: 4, background: "var(--surface-2)", borderRadius: "var(--r)", padding: 3 }}>
          {PERIODS.map((p) => (
            <button
              key={p.label}
              onClick={() => setPeriod(p.label)}
              style={{
                padding: "5px 12px", borderRadius: "calc(var(--r) - 1px)", border: "none",
                background: period === p.label ? "var(--surface)" : "transparent",
                color: period === p.label ? "var(--text-1)" : "var(--text-3)",
                fontWeight: period === p.label ? 700 : 500, fontSize: 12.5, cursor: "pointer",
                boxShadow: period === p.label ? "var(--shadow-sm)" : "none", transition: "all 0.12s",
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      <div style={{ padding: "10px 18px 0" }}>
        <ChartLegend series={EQUITY_SERIES} />
      </div>

      <div style={{ width: "100%", height: 280, padding: "12px 8px 0" }}>
        <ResponsiveContainer>
          <ComposedChart data={filtered} margin={CHART_MARGIN}>
            <AreaGradient id="aiEqGrad" />
            <CartesianGrid strokeDasharray="2 4" vertical={false} stroke="var(--line)" />
            <XAxis
              dataKey="date" {...axisProps}
              tickFormatter={(v) => new Date(v).toLocaleDateString("en-IN", { month: "short", day: "numeric" })}
            />
            {/* Axis shown, not hidden. An equity curve without a scale asks the
                reader to trust the shape; the numbers are the point. */}
            <YAxis
              {...axisProps}
              width={62}
              domain={[Math.max(0, minE - pad), maxE + pad]}
              tickFormatter={(v) => compactNum(v)}
              tick={{ fontVariantNumeric: "tabular-nums" }}
            />
            <BaselineRule y={baseline} label="capital" />
            <Tooltip
              cursor={{ stroke: "var(--line-strong)", strokeDasharray: "3 3" }}
              content={<CompareTooltip series={EQUITY_SERIES} format={inr} labelFormat={fmtDate} />}
            />
            <Area
              type="monotone" dataKey="total_equity" stroke="var(--accent)" strokeWidth={2}
              fillOpacity={1} fill="url(#aiEqGrad)" dot={false}
              activeDot={{ r: 4, fill: "var(--accent)", stroke: "var(--surface)", strokeWidth: 2 }}
            />
            <Line
              type="monotone" dataKey="nifty_equity" stroke="var(--warn)" strokeWidth={1.5}
              strokeDasharray="4 3" dot={false}
              activeDot={{ r: 3, fill: "var(--warn)", stroke: "var(--surface)", strokeWidth: 2 }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

