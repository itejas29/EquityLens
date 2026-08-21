
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { apiClient, apiErrorMessage } from "../api/client";
import LiveQuote from "../components/LiveQuote";
import {
  Change, EmptyState, ErrorState, LoadingState, SectionHeader, inr, pct, fmtDate
} from "../components/ui/Primitives";
import { useLivePrices } from "../lib/useLivePrices";
import {
  Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis
} from "recharts";

/* ---------------------------------------------------------------- helpers -- */

/**
 * Live mark for one holding, plus P&L restated against it.
 *
 * The cost basis is recovered from the API's own two numbers rather than from
 * entry_price * quantity. entry_price is a per-share figure rounded to the
 * paisa, so multiplying it back out drifts from the ledger — the backend is
 * explicit about this (see the P&L IDENTITY note in services/paper_trading.py).
 * Recovering the basis keeps the live figure on exactly the basis the server
 * used, so the two never disagree.
 *
 * Historical fills are untouched: this restates the mark only.
 */
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
        {icon && (
          <span style={{ color: borderColor, opacity: 0.7, fontSize: 16 }}>{icon}</span>
        )}
      </div>
      <div className={`num ${tone || ""}`} style={{ fontSize: 20, fontWeight: 700, color: tone ? undefined : "var(--text-1)", lineHeight: 1.2 }}>
        {value}
      </div>
    </div>
  );
}

function OrderTicket({ quotes, onOrderSuccess }) {
  const [side, setSide] = useState("buy");
  const [symbol, setSymbol] = useState("");
  const [qty, setQty] = useState(10);
  const [placing, setPlacing] = useState(false);
  const [msg, setMsg] = useState(null);

  // Read ?buy= query param on mount
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const buySym = params.get("buy");
    if (buySym) setSymbol(buySym.toUpperCase());
  }, []);

  // Estimated cost from live quote if available
  const livePrice = quotes[symbol.trim().toUpperCase()]?.price ?? null;
  const estimatedCost = livePrice != null && qty > 0 ? livePrice * Number(qty) : null;

  async function handleOrder() {
    const sym = symbol.trim().toUpperCase();
    if (!sym) { setMsg({ ok: false, text: "Enter a stock symbol." }); return; }
    setPlacing(true);
    setMsg(null);
    try {
      const body = side === "buy" ? { symbol: sym, quantity: Number(qty) } : { symbol: sym };
      const r = await apiClient.post(`/paper/${side}`, body);
      setMsg({
        ok: true,
        text: `${side === "buy" ? "Bought" : "Sold"} ${r.data.quantity} ${r.data.symbol} at ${inr(r.data.price)}${
          r.data.pnl != null ? ` · P&L ${inr(r.data.pnl)}` : ""
        }`,
      });
      setSymbol("");
      setQty(10);
      onOrderSuccess?.();
    } catch (e) {
      setMsg({ ok: false, text: apiErrorMessage(e) });
    } finally {
      setPlacing(false);
    }
  }

  return (
    <div className="panel" style={{ padding: "20px 22px" }}>
      {/* BUY / SELL toggle */}
      <div style={{
        display: "inline-flex",
        background: "var(--surface-2)",
        borderRadius: "var(--r)",
        padding: 3,
        marginBottom: 18,
        gap: 3,
      }}>
        {["buy", "sell"].map((s) => (
          <button
            key={s}
            onClick={() => { setSide(s); setMsg(null); }}
            style={{
              padding: "7px 22px",
              borderRadius: "calc(var(--r) - 1px)",
              border: "none",
              fontWeight: 700,
              fontSize: 13,
              letterSpacing: "0.03em",
              textTransform: "uppercase",
              cursor: "pointer",
              transition: "all 0.15s",
              background: side === s
                ? s === "buy" ? "var(--up)" : "var(--down)"
                : "transparent",
              color: side === s ? "#fff" : "var(--text-3)",
              boxShadow: side === s ? "0 2px 8px rgba(0,0,0,0.15)" : "none",
            }}
          >
            {s === "buy" ? "▲ Buy" : "▼ Sell"}
          </button>
        ))}
      </div>

      {/* Inputs row */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
        <div style={{ flex: "1 1 160px" }}>
          <label htmlFor="pt-sym" style={{ display: "block", fontSize: 11, fontWeight: 600, letterSpacing: "0.05em", textTransform: "uppercase", color: "var(--text-3)", marginBottom: 6 }}>
            Symbol
          </label>
          <input
            id="pt-sym"
            className="search-in"
            style={{ padding: "0 12px", width: "100%", height: 38, fontFamily: "var(--font-num)", fontWeight: 700, fontSize: 14, letterSpacing: "0.04em" }}
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="e.g. HINDALCO"
            autoComplete="off"
            onKeyDown={(e) => e.key === "Enter" && handleOrder()}
          />
        </div>

        {side === "buy" && (
          <div style={{ flex: "0 0 110px" }}>
            <label htmlFor="pt-qty" style={{ display: "block", fontSize: 11, fontWeight: 600, letterSpacing: "0.05em", textTransform: "uppercase", color: "var(--text-3)", marginBottom: 6 }}>
              Quantity
            </label>
            <input
              id="pt-qty"
              type="number"
              min="1"
              className="search-in"
              style={{ padding: "0 12px", width: "100%", height: 38, fontFamily: "var(--font-num)", fontSize: 14 }}
              value={qty}
              onChange={(e) => setQty(e.target.value)}
            />
          </div>
        )}

        <button
          className={`btn ${side === "buy" ? "" : "btn-danger"}`}
          disabled={placing}
          onClick={handleOrder}
          style={{
            height: 38,
            padding: "0 22px",
            fontWeight: 700,
            fontSize: 13.5,
            flexShrink: 0,
            background: side === "buy"
              ? "var(--up)"
              : "var(--down)",
            borderColor: "transparent",
            color: "#fff",
            boxShadow: side === "buy"
              ? "0 4px 14px rgba(15,157,88,0.35)"
              : "0 4px 14px rgba(217,48,37,0.3)",
            transition: "all 0.15s",
            opacity: placing ? 0.7 : 1,
          }}
        >
          {placing ? "Placing…" : side === "buy" ? "Place Buy" : "Sell Position"}
        </button>
      </div>

      {/* Estimated cost preview */}
      {side === "buy" && estimatedCost != null && (
        <div style={{ marginTop: 10, fontSize: 12.5, color: "var(--text-3)" }}>
          Estimated cost: <span className="num" style={{ color: "var(--text-1)", fontWeight: 600 }}>{inr(estimatedCost)}</span>
          {" "}at live price <span className="num" style={{ color: "var(--text-1)" }}>{inr(livePrice)}</span>
        </div>
      )}

      {/* Result message */}
      {msg && (
        <div role="status" style={{
          marginTop: 14,
          padding: "10px 14px",
          borderRadius: "var(--r)",
          background: msg.ok ? "var(--up-soft)" : "var(--down-soft)",
          border: `1px solid ${msg.ok ? "rgba(15,157,88,0.25)" : "rgba(217,48,37,0.25)"}`,
          color: msg.ok ? "var(--up)" : "var(--down)",
          fontSize: 13,
          fontWeight: 600,
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}>
          <span>{msg.ok ? "✓" : "✕"}</span>
          {msg.text}
        </div>
      )}

      <p style={{ fontSize: 11.5, color: "var(--text-3)", marginTop: 14, marginBottom: 0, lineHeight: 1.65 }}>
        Orders fill at the latest stored close plus costs. Selling closes the whole position; partial exits are not supported.
      </p>
    </div>
  );
}

function HoldingsPanel({ holdings, acct, quotes, onSell, placing }) {
  const totalPnl = holdings.reduce((acc, h) => {
    const m = markOf(h, quotes);
    return acc + (m.pnl ?? 0);
  }, 0);

  if (!holdings.length) {
    return (
      <EmptyState
        title="No open positions"
        body="Place a paper buy above, or open a stock and trade it from there."
        action={<Link to="/today" className="btn btn-primary">See today's picks</Link>}
      />
    );
  }

  return (
    <div className="panel">
      {/* Summary bar */}
      <div style={{
        display: "flex",
        gap: 24,
        padding: "12px 16px",
        borderBottom: "1px solid var(--line)",
        background: "var(--surface-2)",
        flexWrap: "wrap",
      }}>
        <div>
          <div style={{ fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-3)" }}>Positions</div>
          <div className="num" style={{ fontSize: 15, fontWeight: 700, color: "var(--text-1)", marginTop: 2 }}>{holdings.length}</div>
        </div>
        <div>
          <div style={{ fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-3)" }}>Invested</div>
          <div className="num" style={{ fontSize: 15, fontWeight: 700, color: "var(--text-1)", marginTop: 2 }}>{inr(acct.market_value)}</div>
        </div>
        <div>
          <div style={{ fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-3)" }}>Unrealised P&L</div>
          <div className={`num ${totalPnl > 0 ? "up" : totalPnl < 0 ? "down" : ""}`} style={{ fontSize: 15, fontWeight: 700, marginTop: 2 }}>
            {totalPnl > 0 ? "+" : ""}{inr(totalPnl)}
          </div>
        </div>
      </div>

      <div className="tbl-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>Stock</th>
              <th className="r">Qty</th>
              <th className="r">Avg Price</th>
              <th className="r">Current</th>
              <th className="r">P&L</th>
              <th className="r col-hide-lg">Weight</th>
              <th className="r" style={{ width: 70 }}></th>
            </tr>
          </thead>
          <tbody>
            {holdings.map((h) => {
              const m = markOf(h, quotes);
              const weight = acct.equity
                ? ((m.price ?? h.entry_price) * h.quantity / acct.equity) * 100
                : null;

              return (
                <tr key={h.trade_id}>
                  <td>
                    <Link to={`/stocks/${h.symbol}`} className="sym">{h.symbol}</Link>
                  </td>
                  <td className="r num" style={{ fontWeight: 600 }}>{h.quantity}</td>
                  {/* Entry price is the historical fill and never moves. */}
                  <td className="r num" style={{ color: "var(--text-3)" }}>{inr(h.entry_price)}</td>
                  <td className="r num price">
                    <div>{inr(m.price)}</div>
                    <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 2 }}>
                      <LiveQuote quote={m.quote} />
                    </div>
                  </td>
                  <td className="r">
                    <Change value={m.pnlPct} absolute={m.pnl} />
                  </td>
                  <td className="r col-hide-lg">
                    {weight != null ? (
                      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
                        <span className="num" style={{ fontSize: 12.5, color: "var(--text-3)" }}>{weight.toFixed(1)}%</span>
                        <div style={{ width: 48, height: 3, borderRadius: 99, background: "var(--surface-2)", overflow: "hidden" }}>
                          <div style={{ width: `${Math.min(100, weight)}%`, height: "100%", background: "var(--accent)", borderRadius: 99 }} />
                        </div>
                      </div>
                    ) : "—"}
                  </td>
                  <td className="r">
                    <button
                      className="btn btn-sm"
                      disabled={placing}
                      onClick={() => onSell(h.symbol)}
                      style={{
                        background: "var(--down-soft)",
                        borderColor: "rgba(217,48,37,0.25)",
                        color: "var(--down)",
                        fontWeight: 700,
                      }}
                    >
                      Sell
                    </button>
                  </td>
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
      {/* Chart header */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "14px 18px",
        borderBottom: "1px solid var(--line)",
        flexWrap: "wrap",
        gap: 12,
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
        {/* Period selector */}
        <div style={{ display: "flex", gap: 4, background: "var(--surface-2)", borderRadius: "var(--r)", padding: 3 }}>
          {PERIODS.map((p) => (
            <button
              key={p.label}
              onClick={() => setPeriod(p.label)}
              style={{
                padding: "5px 12px",
                borderRadius: "calc(var(--r) - 1px)",
                border: "none",
                background: period === p.label ? "var(--surface)" : "transparent",
                color: period === p.label ? "var(--text-1)" : "var(--text-3)",
                fontWeight: period === p.label ? 700 : 500,
                fontSize: 12.5,
                cursor: "pointer",
                boxShadow: period === p.label ? "var(--shadow-sm)" : "none",
                transition: "all 0.12s",
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Chart area */}
      <div style={{ width: "100%", height: 260, padding: "12px 8px 0" }}>
        <ResponsiveContainer>
          <AreaChart data={filtered} margin={{ top: 10, right: 10, left: 10, bottom: 0 }}>
            <defs>
              <linearGradient id="ptEqGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.25} />
                <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--line)" />
            <XAxis
              dataKey="date"
              stroke="var(--text-3)"
              fontSize={11}
              tickLine={false}
              axisLine={false}
              tickFormatter={(v) =>
                new Date(v).toLocaleDateString("en-IN", { month: "short", day: "numeric" })
              }
            />
            <YAxis
              domain={[Math.max(0, minE - pad), maxE + pad]}
              hide
            />
            <Tooltip
              contentStyle={{
                background: "var(--surface)",
                border: "1px solid var(--line)",
                borderRadius: "var(--r)",
                boxShadow: "var(--shadow-pop)",
                fontSize: 13,
              }}
              itemStyle={{ color: "var(--text-1)", fontWeight: 600 }}
              formatter={(value) => [inr(value), "Portfolio Value"]}
              labelFormatter={(label) => fmtDate(label)}
            />
            <Area
              type="monotone"
              dataKey="total_equity"
              stroke="var(--accent)"
              strokeWidth={2}
              fillOpacity={1}
              fill="url(#ptEqGrad)"
              dot={false}
              activeDot={{ r: 4, fill: "var(--accent)", stroke: "var(--surface)", strokeWidth: 2 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

const TX_FILTERS = ["All", "BUY", "SELL"];

function TransactionHistory({ trades }) {
  const [filter, setFilter] = useState("All");

  const filtered = useMemo(() => {
    if (!trades) return [];
    if (filter === "All") return trades;
    return trades.filter((t) => t.side === filter);
  }, [trades, filter]);

  const sellPnl = useMemo(() => {
    return filtered
      .filter((t) => t.side === "SELL" && t.pnl != null)
      .reduce((acc, t) => acc + t.pnl, 0);
  }, [filtered]);

  if (!trades || !trades.length) {
    return <EmptyState title="No transactions yet" body="Your executed orders will appear here." />;
  }

  return (
    <div className="panel">
      {/* Filter chips + tally */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "12px 16px",
        borderBottom: "1px solid var(--line)",
        background: "var(--surface-2)",
        gap: 12,
        flexWrap: "wrap",
      }}>
        <div style={{ display: "flex", gap: 6 }}>
          {TX_FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              style={{
                padding: "5px 14px",
                borderRadius: 100,
                border: "1px solid",
                borderColor: filter === f
                  ? f === "BUY" ? "rgba(15,157,88,0.4)"
                    : f === "SELL" ? "rgba(217,48,37,0.4)"
                    : "var(--accent)"
                  : "var(--line)",
                background: filter === f
                  ? f === "BUY" ? "var(--up-soft)"
                    : f === "SELL" ? "var(--down-soft)"
                    : "var(--accent-soft)"
                  : "var(--surface)",
                color: filter === f
                  ? f === "BUY" ? "var(--up)"
                    : f === "SELL" ? "var(--down)"
                    : "var(--accent)"
                  : "var(--text-3)",
                fontWeight: 600,
                fontSize: 12.5,
                cursor: "pointer",
                transition: "all 0.12s",
              }}
            >
              {f}
            </button>
          ))}
        </div>
        {(filter === "SELL" || filter === "All") && sellPnl !== 0 && (
          <div style={{ fontSize: 12.5, color: "var(--text-3)" }}>
            Realised P&L:{" "}
            <span className={`num ${sellPnl > 0 ? "up" : "down"}`} style={{ fontWeight: 700 }}>
              {sellPnl > 0 ? "+" : ""}{inr(sellPnl)}
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
              <th className="r">Side</th>
              <th className="r">Qty</th>
              <th className="r">Price</th>
              <th className="r">P&L</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((t) => (
              <tr key={t.id}>
                <td style={{ width: 130 }}>
                  <div className="num" style={{ color: "var(--text-1)", fontSize: 13 }}>
                    {relativeTime(t.executed_at)}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text-3)", marginTop: 1 }}>
                    {new Date(t.executed_at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}
                  </div>
                </td>
                <td>
                  <Link to={`/stocks/${t.symbol}`} className="sym">{t.symbol}</Link>
                </td>
                <td className="r">
                  <span style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    padding: "3px 9px",
                    borderRadius: "var(--r-sm)",
                    fontSize: 11,
                    fontWeight: 700,
                    letterSpacing: "0.04em",
                    background: t.side === "BUY" ? "var(--up-soft)" : "var(--down-soft)",
                    color: t.side === "BUY" ? "var(--up)" : "var(--down)",
                    border: `1px solid ${t.side === "BUY" ? "rgba(15,157,88,0.25)" : "rgba(217,48,37,0.25)"}`,
                  }}>
                    {t.side === "BUY" ? "▲" : "▼"} {t.side}
                  </span>
                </td>
                <td className="r num" style={{ fontWeight: 600 }}>{t.quantity}</td>
                <td className="r num">{inr(t.price)}</td>
                <td className="r num">
                  {t.side === "SELL" && t.pnl != null ? (
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

export default function PaperTradingPage() {
  const [acct, setAcct] = useState(null);
  const [curve, setCurve] = useState(null);
  const [trades, setTrades] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [placing, setPlacing] = useState(false);
  // Holdings are in the fast tier, so their marks arrive over the socket
  // between the 60s account polls.
  const { quotes } = useLivePrices();

  // `background` polls skip the spinner: without it the whole panel would
  // flash a loading state every refresh tick.
  function load({ background = false } = {}) {
    if (!background) setLoading(true);
    Promise.allSettled([
      apiClient.get("/paper/account"),
      apiClient.get("/paper/equity-curve"),
      apiClient.get("/paper/transactions"),
    ]).then(([a, c, t]) => {
      if (a.status === "fulfilled") {
        setAcct(a.value.data);
        // Cleared on success, so a transient blip during polling doesn't leave
        // a stale error banner sitting above data that has since recovered.
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
    // Holdings are marked at live prices server-side, so the values are only as
    // fresh as the last request. Without this the page would show the prices
    // from the moment it mounted for the rest of the session. 60s matches the
    // scheduler's price-refresh cadence — polling faster just returns the same
    // numbers.
    const id = setInterval(() => load({ background: true }), 60_000);
    return () => clearInterval(id);
  }, []);

  async function handleSell(symbol) {
    setPlacing(true);
    try {
      await apiClient.post("/paper/sell", { symbol });
      load();
    } catch (e) {
      // Sell errors surface in the holdings table area silently; the order
      // ticket shows its own message for direct actions.
    } finally {
      setPlacing(false);
    }
  }

  if (loading) return <div className="page"><LoadingState rows={6} /></div>;
  if (error) return <div className="page"><ErrorState message={error} onRetry={load} /></div>;
  if (!acct) return null;

  const holdings = acct.holdings || [];

  return (
    <div className="page">
      {/* ── Page title ── */}
      <div style={{ marginBottom: 22 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: "var(--text-1)", marginBottom: 4 }}>
          Paper Trading
        </h1>
        <p style={{ fontSize: 13, color: "var(--text-3)", margin: 0 }}>
          Virtual money against real prices — same transaction-cost model as the backtest
        </p>
      </div>

      {/* ── Portfolio stat cards ── */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
        gap: 12,
        marginBottom: 24,
      }}>
        <StatCard
          label="Portfolio Value"
          value={inr(acct.equity)}
          accent="neutral"
          icon="◈"
        />
        <StatCard
          label="Available Cash"
          value={inr(acct.cash)}
          accent="neutral"
          icon="◎"
        />
        <StatCard
          label="Invested"
          value={inr(acct.market_value ?? 0)}
          accent="neutral"
          icon="▣"
        />
        <StatCard
          label="Unrealised P&L"
          value={inr(acct.unrealized_pnl)}
          tone={acct.unrealized_pnl > 0 ? "up" : acct.unrealized_pnl < 0 ? "down" : ""}
          accent={acct.unrealized_pnl > 0 ? "up" : acct.unrealized_pnl < 0 ? "down" : "neutral"}
          icon={acct.unrealized_pnl > 0 ? "▲" : acct.unrealized_pnl < 0 ? "▼" : "—"}
        />
        <StatCard
          label="Realised P&L"
          value={inr(acct.realized_pnl)}
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

      {/* ── Main grid: Order ticket + Holdings ── */}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1.6fr)", gap: 16, marginBottom: 24, alignItems: "start" }}>
        {/* Order ticket */}
        <section>
          <SectionHeader title="Order Ticket" />
          <OrderTicket quotes={quotes} onOrderSuccess={load} />
        </section>

        {/* Holdings */}
        <section>
          <SectionHeader
            title="Holdings"
            sub={`${holdings.length} open position${holdings.length === 1 ? "" : "s"}`}
          />
          <HoldingsPanel
            holdings={holdings}
            acct={acct}
            quotes={quotes}
            onSell={handleSell}
            placing={placing}
          />
        </section>
      </div>

      {/* ── Performance chart ── */}
      <section style={{ marginBottom: 24 }}>
        <SectionHeader title="Performance" />
        <PerformanceChart data={curve} />
      </section>

      {/* ── Transaction history ── */}
      <section>
        <SectionHeader
          title="Transaction History"
          sub={trades?.length ? `${trades.length} trade${trades.length === 1 ? "" : "s"}` : ""}
        />
        <TransactionHistory trades={trades} />
      </section>
    </div>
  );
}
