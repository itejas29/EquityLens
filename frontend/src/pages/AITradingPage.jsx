import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { apiClient, apiErrorMessage } from "../api/client";
import LiveQuote from "../components/LiveQuote";
import {
  Change, EmptyState, ErrorState, LoadingState, SectionHeader, inr, fmtDate
} from "../components/ui/Primitives";
import { useLivePrices } from "../lib/useLivePrices";
import {
  Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis
} from "recharts";

/* ---------------------------------------------------------------- helpers -- */

// Same cost-basis-recovery approach as PaperTradingPage — see the P&L IDENTITY
// note in services/paper_trading.py for why this reads better than entry_price*qty.
function markOf(h, quotes) {
  const quote = quotes[h.symbol] || null;
  const price = quote?.price ?? h.current_price ?? null;

  if (h.current_price == null || h.unrealized_pnl == null || price == null) {
    return { price, quote, pnl: h.unrealized_pnl, pnlPct: h.unrealized_pnl_pct };
  }

  const basis = h.current_price * h.quantity - h.unrealized_pnl;
  const pnl = price * h.quantity - basis;
  return {
    price,
    quote,
    pnl: Math.round(pnl * 100) / 100,
    pnlPct: basis ? Math.round((pnl / basis) * 10000) / 100 : null,
  };
}

function relativeTime(isoString) {
  const diff = Date.now() - new Date(isoString).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(isoString).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

const REASON_LABEL = { stop: "Stop", target: "Target", horizon: "Horizon", regime: "Regime" };
const REASON_COLOR = {
  stop: "var(--down)", target: "var(--up)", horizon: "var(--text-3)", regime: "var(--warn)",
};

/* --------------------------------------------------------------- sub-components -- */

function StatCard({ label, value, tone, icon, accent }) {
  const borderColor = accent === "up" ? "var(--up)"
    : accent === "down" ? "var(--down)"
    : accent === "warn" ? "var(--warn)"
    : "var(--line)";

  return (
    <div style={{
      background: "var(--surface)",
      border: "1px solid var(--line)",
      borderLeft: `3px solid ${borderColor}`,
      borderRadius: "var(--r-lg)",
      padding: "14px 18px",
      display: "flex",
      flexDirection: "column",
      gap: 6,
      boxShadow: "var(--shadow-sm)",
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.05em", textTransform: "uppercase", color: "var(--text-3)" }}>
          {label}
        </span>
        {icon && <span style={{ color: borderColor, opacity: 0.7, fontSize: 16 }}>{icon}</span>}
      </div>
      <div className={`num ${tone || ""}`} style={{ fontSize: 20, fontWeight: 700, color: tone ? undefined : "var(--text-1)", lineHeight: 1.2 }}>
        {value}
      </div>
    </div>
  );
}

function HoldingsPanel({ holdings, quotes }) {
  if (!holdings.length) {
    return (
      <EmptyState
        title="No open positions"
        body="The AI hasn't opened a position yet — it only buys when a shortlist name is trading inside its published entry zone."
      />
    );
  }

  return (
    <div className="panel">
      <div className="tbl-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>Stock</th>
              <th className="r">Qty</th>
              <th className="r">Avg Price</th>
              <th className="r">Current</th>
              <th className="r">Stop</th>
              <th className="r">Target</th>
              <th className="r">P&L</th>
            </tr>
          </thead>
          <tbody>
            {holdings.map((h) => {
              const m = markOf(h, quotes);
              return (
                <tr key={h.trade_id}>
                  <td><Link to={`/stocks/${h.symbol}`} className="sym">{h.symbol}</Link></td>
                  <td className="r num" style={{ fontWeight: 600 }}>{h.quantity}</td>
                  <td className="r num" style={{ color: "var(--text-3)" }}>{inr(h.entry_price)}</td>
                  <td className="r num price">
                    <div>{inr(m.price)}</div>
                    <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 2 }}>
                      <LiveQuote quote={m.quote} />
                    </div>
                  </td>
                  <td className="r num" style={{ color: "var(--down)" }}>{h.stop_loss != null ? inr(h.stop_loss) : "—"}</td>
                  <td className="r num" style={{ color: "var(--up)" }}>{h.target_price != null ? inr(h.target_price) : "—"}</td>
                  <td className="r"><Change value={m.pnlPct} absolute={m.pnl} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const PERIODS = [
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "All", days: Infinity },
];

function PerformanceChart({ data }) {
  const [period, setPeriod] = useState("All");

  const filtered = useMemo(() => {
    if (!data?.length) return [];
    const p = PERIODS.find((x) => x.label === period);
    if (!p || p.days === Infinity) return data;
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - p.days);
    return data.filter((d) => new Date(d.date) >= cutoff);
  }, [data, period]);

  const stats = useMemo(() => {
    if (!filtered.length) return null;
    const first = filtered[0].total_equity;
    const last = filtered[filtered.length - 1].total_equity;
    const ret = ((last - first) / first) * 100;
    return { first, last, ret };
  }, [filtered]);

  if (!data || !data.length) {
    return <EmptyState title="No equity data" body="No snapshots recorded yet." />;
  }

  const minE = Math.min(...filtered.map((d) => d.total_equity));
  const maxE = Math.max(...filtered.map((d) => d.total_equity));
  const pad = (maxE - minE) * 0.12 || 1000;

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

      <div style={{ width: "100%", height: 260, padding: "12px 8px 0" }}>
        <ResponsiveContainer>
          <AreaChart data={filtered} margin={{ top: 10, right: 10, left: 10, bottom: 0 }}>
            <defs>
              <linearGradient id="aiEqGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.25} />
                <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--line)" />
            <XAxis
              dataKey="date" stroke="var(--text-3)" fontSize={11} tickLine={false} axisLine={false}
              tickFormatter={(v) => new Date(v).toLocaleDateString("en-IN", { month: "short", day: "numeric" })}
            />
            <YAxis domain={[Math.max(0, minE - pad), maxE + pad]} hide />
            <Tooltip
              contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r)", boxShadow: "var(--shadow-pop)", fontSize: 13 }}
              itemStyle={{ color: "var(--text-1)", fontWeight: 600 }}
              formatter={(value) => [inr(value), "Portfolio Value"]}
              labelFormatter={(label) => fmtDate(label)}
            />
            <Area type="monotone" dataKey="total_equity" stroke="var(--accent)" strokeWidth={2} fillOpacity={1} fill="url(#aiEqGrad)" dot={false} activeDot={{ r: 4, fill: "var(--accent)", stroke: "var(--surface)", strokeWidth: 2 }} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function TransactionHistory({ trades }) {
  const [filter, setFilter] = useState("All");

  const filtered = useMemo(() => {
    if (!trades) return [];
    if (filter === "All") return trades;
    if (filter === "Open") return trades.filter((t) => t.status === "open");
    return trades.filter((t) => t.status === "closed");
  }, [trades, filter]);

  const closedPnl = useMemo(
    () => filtered.filter((t) => t.status === "closed" && t.pnl != null).reduce((acc, t) => acc + t.pnl, 0),
    [filtered]
  );

  if (!trades || !trades.length) {
    return <EmptyState title="No trades yet" body="The AI's buys and sells will appear here once it acts on a shortlist." />;
  }

  return (
    <div className="panel">
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "12px 16px", borderBottom: "1px solid var(--line)", background: "var(--surface-2)", gap: 12, flexWrap: "wrap",
      }}>
        <div style={{ display: "flex", gap: 6 }}>
          {["All", "Open", "Closed"].map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              style={{
                padding: "5px 14px", borderRadius: 100, border: "1px solid",
                borderColor: filter === f ? "var(--accent)" : "var(--line)",
                background: filter === f ? "var(--accent-soft)" : "var(--surface)",
                color: filter === f ? "var(--accent)" : "var(--text-3)",
                fontWeight: 600, fontSize: 12.5, cursor: "pointer", transition: "all 0.12s",
              }}
            >
              {f}
            </button>
          ))}
        </div>
        {(filter === "Closed" || filter === "All") && closedPnl !== 0 && (
          <div style={{ fontSize: 12.5, color: "var(--text-3)" }}>
            Realised P&L:{" "}
            <span className={`num ${closedPnl > 0 ? "up" : "down"}`} style={{ fontWeight: 700 }}>
              {closedPnl > 0 ? "+" : ""}{inr(closedPnl)}
            </span>
          </div>
        )}
      </div>

      <div className="tbl-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>Date</th>
              <th>Stock</th>
              <th className="r">Qty</th>
              <th className="r">Entry</th>
              <th className="r">Exit</th>
              <th className="r">Reason</th>
              <th className="r">P&L</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((t) => (
              <tr key={t.id}>
                <td style={{ width: 130 }}>
                  <div className="num" style={{ color: "var(--text-1)", fontSize: 13 }}>{relativeTime(t.executed_at)}</div>
                  <div style={{ fontSize: 11, color: "var(--text-3)", marginTop: 1 }}>
                    {new Date(t.executed_at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}
                  </div>
                </td>
                <td><Link to={`/stocks/${t.symbol}`} className="sym">{t.symbol}</Link></td>
                <td className="r num" style={{ fontWeight: 600 }}>{t.quantity}</td>
                <td className="r num">{inr(t.price)}</td>
                <td className="r num">{t.exit_price != null ? inr(t.exit_price) : "—"}</td>
                <td className="r">
                  {t.exit_reason ? (
                    <span style={{
                      display: "inline-flex", alignItems: "center", padding: "3px 9px",
                      borderRadius: "var(--r-sm)", fontSize: 11, fontWeight: 700, letterSpacing: "0.04em",
                      background: "var(--surface-2)", color: REASON_COLOR[t.exit_reason] || "var(--text-3)",
                      border: "1px solid var(--line)",
                    }}>
                      {REASON_LABEL[t.exit_reason] || t.exit_reason}
                    </span>
                  ) : (
                    <span style={{ fontSize: 11, color: "var(--text-3)" }}>open</span>
                  )}
                </td>
                <td className="r num">
                  {t.pnl != null ? (
                    <span className={t.pnl > 0 ? "up" : t.pnl < 0 ? "down" : ""} style={{ fontWeight: 700 }}>
                      {t.pnl > 0 ? "+" : ""}{inr(t.pnl)}
                    </span>
                  ) : (
                    <span style={{ color: "var(--text-3)" }}>—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- page root -- */

export default function AITradingPage() {
  const [acct, setAcct] = useState(null);
  const [curve, setCurve] = useState(null);
  const [trades, setTrades] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const { quotes } = useLivePrices();

  function load({ background = false } = {}) {
    if (!background) setLoading(true);
    Promise.allSettled([
      apiClient.get("/ai-trading/account"),
      apiClient.get("/ai-trading/equity-curve"),
      apiClient.get("/ai-trading/transactions"),
    ]).then(([a, c, t]) => {
      if (a.status === "fulfilled") {
        setAcct(a.value.data);
        setError("");
      } else {
        setError(apiErrorMessage(a.reason));
      }
      if (c.status === "fulfilled") setCurve(c.value.data);
      if (t.status === "fulfilled") setTrades(t.value.data);
    }).finally(() => { if (!background) setLoading(false); });
  }

  useEffect(() => {
    load();
    const id = setInterval(() => load({ background: true }), 60_000);
    return () => clearInterval(id);
  }, []);

  if (loading) return <div className="page"><LoadingState rows={6} /></div>;
  if (error) return <div className="page"><ErrorState message={error} onRetry={load} /></div>;
  if (!acct) return null;

  const holdings = acct.holdings || [];

  return (
    <div className="page">
      <div style={{ marginBottom: 22 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: "var(--text-1)", marginBottom: 4 }}>
          AI Trading
        </h1>
        <p style={{ fontSize: 13, color: "var(--text-3)", margin: 0, maxWidth: 640 }}>
          A dedicated virtual account managed entirely by the V1 momentum strategy — the same rules
          behind today's picks, executed automatically with no manual clicks. It buys shortlist names
          trading inside their published entry zone and sells on the strategy's own stop-loss, target,
          time horizon, or market-regime rules. Research tool only — not investment advice.
        </p>
      </div>

      <div style={{
        display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(145px, 1fr))", gap: 12, marginBottom: 28,
      }}>
        <StatCard label="Portfolio Value" value={inr(acct.equity)} accent="neutral" icon="◈" />
        <StatCard label="Available Cash" value={inr(acct.cash)} accent="neutral" icon="◎" />
        <StatCard label="Invested" value={inr(acct.market_value ?? 0)} accent="neutral" icon="▣" />
        <StatCard
          label="Unrealised P&L" value={inr(acct.unrealized_pnl)}
          tone={acct.unrealized_pnl > 0 ? "up" : acct.unrealized_pnl < 0 ? "down" : ""}
          accent={acct.unrealized_pnl > 0 ? "up" : acct.unrealized_pnl < 0 ? "down" : "neutral"}
          icon={acct.unrealized_pnl > 0 ? "▲" : acct.unrealized_pnl < 0 ? "▼" : "—"}
        />
        <StatCard
          label="Realised P&L" value={inr(acct.realized_pnl)}
          tone={acct.realized_pnl > 0 ? "up" : acct.realized_pnl < 0 ? "down" : ""}
          accent={acct.realized_pnl > 0 ? "up" : acct.realized_pnl < 0 ? "down" : "neutral"}
          icon={acct.realized_pnl > 0 ? "▲" : acct.realized_pnl < 0 ? "▼" : "—"}
        />
        <StatCard
          label="Drawdown"
          value={acct.current_drawdown_pct != null ? acct.current_drawdown_pct.toFixed(1) + "%" : "0.0%"}
          tone={acct.current_drawdown_pct < 0 ? "down" : ""}
          accent={acct.current_drawdown_pct < 0 ? "warn" : "neutral"}
          icon="⬎"
        />
      </div>

      <section style={{ marginBottom: 28 }}>
        <SectionHeader title="Holdings" sub={`${holdings.length} open position${holdings.length === 1 ? "" : "s"}`} />
        <HoldingsPanel holdings={holdings} quotes={quotes} />
      </section>

      <section style={{ marginBottom: 28 }}>
        <SectionHeader title="Performance" />
        <PerformanceChart data={curve} />
      </section>

      <section>
        <SectionHeader title="Transactions" sub={trades?.length ? `${trades.length} trade${trades.length === 1 ? "" : "s"}` : ""} />
        <TransactionHistory trades={trades} />
      </section>
    </div>
  );
}
