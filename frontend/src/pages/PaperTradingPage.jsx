
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiClient, apiErrorMessage } from "../api/client";
import {
  Change, EmptyState, ErrorState, LoadingState, SectionHeader, inr, pct, fmtDate
} from "../components/ui/Primitives";
import {
  Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis
} from "recharts";

function Metric({ label, value, tone }) {
  return (
    <div className="mkt-cell">
      <div className="mkt-label">{label}</div>
      <div className={`mkt-value num ${tone || ""}`}>{value}</div>
    </div>
  );
}

function PerformanceChart({ data }) {
  if (!data || !data.length) {
    return <EmptyState title="No equity data" body="No snapshots recorded yet." />;
  }
  
  const minEquity = Math.min(...data.map(d => d.total_equity));
  const maxEquity = Math.max(...data.map(d => d.total_equity));
  const domainPad = (maxEquity - minEquity) * 0.1;

  return (
    <div style={{ width: "100%", height: 300 }}>
      <ResponsiveContainer>
        <AreaChart data={data} margin={{ top: 10, right: 10, left: 10, bottom: 0 }}>
          <defs>
            <linearGradient id="colorEq" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="var(--accent-1)" stopOpacity={0.3}/>
              <stop offset="95%" stopColor="var(--accent-1)" stopOpacity={0}/>
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border-1)" />
          <XAxis dataKey="date" stroke="var(--text-3)" fontSize={12} tickFormatter={(v) => new Date(v).toLocaleDateString(undefined, {month: "short", day: "numeric"})} />
          <YAxis domain={[Math.max(0, minEquity - domainPad), maxEquity + domainPad]} hide />
          <Tooltip 
            contentStyle={{ backgroundColor: "var(--bg-1)", borderColor: "var(--border-1)", borderRadius: 6 }}
            itemStyle={{ color: "var(--text-1)" }}
            formatter={(value, name) => [inr(value), "Total Equity"]}
            labelFormatter={(label) => fmtDate(label)}
          />
          <Area type="monotone" dataKey="total_equity" stroke="var(--accent-1)" fillOpacity={1} fill="url(#colorEq)" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function PaperTradingPage() {
  const [acct, setAcct] = useState(null);
  const [curve, setCurve] = useState(null);
  const [trades, setTrades] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [symbol, setSymbol] = useState("");
  const [qty, setQty] = useState(10);
  const [placing, setPlacing] = useState(false);
  const [msg, setMsg] = useState(null);

  function load() {
    setLoading(true);
    Promise.allSettled([
      apiClient.get("/paper/account"),
      apiClient.get("/paper/equity-curve"),
      apiClient.get("/paper/transactions")
    ]).then(([a, c, t]) => {
      if (a.status === "fulfilled") setAcct(a.value.data);
      else setError(apiErrorMessage(a.reason));
      if (c.status === "fulfilled") setCurve(c.value.data);
      if (t.status === "fulfilled") setTrades(t.value.data);
    }).finally(() => setLoading(false));
  }
  
  useEffect(() => {
    load();
    const params = new URLSearchParams(window.location.search);
    const buySym = params.get("buy");
    if (buySym) {
      setSymbol(buySym.toUpperCase());
    }
  }, []);

  async function order(side) {
    const sym = symbol.trim().toUpperCase();
    if (!sym) { setMsg({ ok: false, text: "Enter a stock symbol." }); return; }
    setPlacing(true); setMsg(null);
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
      load();
    } catch (e) {
      setMsg({ ok: false, text: apiErrorMessage(e) });
    } finally {
      setPlacing(false);
    }
  }

  if (loading) return <div className="page"><LoadingState rows={6} /></div>;
  if (error) return <div className="page"><ErrorState message={error} onRetry={load} /></div>;
  if (!acct) return null;

  const invested = acct.market_value ?? 0;
  const holdings = acct.holdings || [];

  return (
    <div className="page">
      <SectionHeader
        title="Paper trading"
        sub="Virtual money against real prices — same transaction-cost model as the backtest"
      />

      <div className="mkt" style={{ marginBottom: 16 }}>
        <Metric label="Portfolio value" value={inr(acct.equity)} />
        <Metric label="Available cash" value={inr(acct.cash)} />
        <Metric label="Invested" value={inr(invested)} />
        <Metric
          label="Unrealised P&L"
          value={inr(acct.unrealized_pnl)}
          tone={acct.unrealized_pnl > 0 ? "up" : acct.unrealized_pnl < 0 ? "down" : ""}
        />
        <Metric
          label="Realised P&L"
          value={inr(acct.realized_pnl)}
          tone={acct.realized_pnl > 0 ? "up" : acct.realized_pnl < 0 ? "down" : ""}
        />
        <Metric label="Drawdown" value={acct.current_drawdown_pct != null ? acct.current_drawdown_pct.toFixed(1) + "%" : "0.0%"} tone={acct.current_drawdown_pct < 0 ? "down" : ""} />
      </div>

      <div className="g-main">
        <section className="section" style={{ marginBottom: 0 }}>
        <SectionHeader title="Order ticket" />
        <div className="panel" style={{ padding: 16 }}>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
            <div>
              <label htmlFor="pt-sym" className="mkt-label" style={{ display: "block", marginBottom: 5 }}>Symbol</label>
              <input
                id="pt-sym" className="search-in" style={{ padding: "0 12px", width: 180 }}
                value={symbol} onChange={(e) => setSymbol(e.target.value)}
                placeholder="e.g. HINDALCO" autoComplete="off"
              />
            </div>
            <div>
              <label htmlFor="pt-qty" className="mkt-label" style={{ display: "block", marginBottom: 5 }}>Quantity</label>
              <input
                id="pt-qty" type="number" min="1" className="search-in" style={{ padding: "0 12px", width: 110 }}
                value={qty} onChange={(e) => setQty(e.target.value)}
              />
            </div>
            <button className="btn btn-primary" disabled={placing} onClick={() => order("buy")}>
              {placing ? "Placing…" : "Buy"}
            </button>
            <button className="btn" disabled={placing} onClick={() => order("sell")}>
              Sell entire position
            </button>
          </div>

          {msg && (
            <p role="status" className={msg.ok ? "up" : "down"} style={{ marginTop: 12, marginBottom: 0, fontSize: 13, fontWeight: 600 }}>
              {msg.text}
            </p>
          )}

          <p style={{ fontSize: 12, color: "var(--text-3)", marginTop: 12, marginBottom: 0, lineHeight: 1.6 }}>
            Orders fill at the latest stored close plus costs. The API accepts symbol and quantity only —
            stop-loss and target are not offered here because the backend would not persist them.
            Selling closes the whole position; partial exits are not supported.
          </p>
        </div>
        </section>
        <section className="section" style={{ marginBottom: 0 }}>
        <SectionHeader title="Holdings" sub={`${holdings.length} open position${holdings.length === 1 ? "" : "s"}`} />
        {!holdings.length ? (
          <EmptyState
            title="No open positions"
            body="Place a paper buy above, or open a stock and trade it from there."
            action={<Link to="/today" className="btn btn-primary">See today’s picks</Link>}
          />
        ) : (
          <div className="panel">
            <div className="tbl-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Stock</th><th className="r">Qty</th><th className="r">Avg price</th>
                    <th className="r">Current</th><th className="r">P&L</th>
                    <th className="r col-hide-lg">Weight</th><th className="r" style={{ width: 70 }}></th>
                  </tr>
                </thead>
                <tbody>
                  {holdings.map((h) => (
                    <tr key={h.trade_id}>
                      <td><Link to={`/stocks/${h.symbol}`} className="sym">{h.symbol}</Link></td>
                      <td className="r num">{h.quantity}</td>
                      <td className="r num">{inr(h.entry_price)}</td>
                      <td className="r num price">{inr(h.current_price)}</td>
                      <td className="r"><Change value={h.unrealized_pnl_pct} absolute={h.unrealized_pnl} /></td>
                      <td className="r num col-hide-lg" style={{ color: "var(--text-3)" }}>
                        {acct.equity ? `${(((h.current_price ?? h.entry_price) * h.quantity / acct.equity) * 100).toFixed(1)}%` : "—"}
                      </td>
                      <td className="r">
                        <button className="btn btn-sm" disabled={placing}
                          onClick={() => { setSymbol(h.symbol); order("sell"); }}>Sell</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        </section>
      </div>

      <section className="section">
        <SectionHeader title="Performance" />
        <div className="panel" style={{ padding: "20px 20px 0" }}>
          <PerformanceChart data={curve} />
        </div>
      </section>

      <section className="section">
        <SectionHeader title="Transaction History" sub={trades?.length ? `${trades.length} trades` : ""} />
        {!trades || !trades.length ? (
          <EmptyState title="No transactions yet" body="Your executed orders will appear here." />
        ) : (
          <div className="panel">
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
                  {trades.map((t) => (
                    <tr key={t.id}>
                      <td className="num" style={{ color: "var(--text-3)", fontSize: "13px" }}>
                        {new Date(t.executed_at).toLocaleString()}
                      </td>
                      <td><Link to={`/stocks/${t.symbol}`} className="sym">{t.symbol}</Link></td>
                      <td className="r">
                        <span className={`badge ${t.side === "BUY" ? "bg-up" : "bg-down"}`} style={{ fontSize: "11px", padding: "2px 6px", borderRadius: 4 }}>
                          {t.side}
                        </span>
                      </td>
                      <td className="r num">{t.quantity}</td>
                      <td className="r num">{inr(t.price)}</td>
                      <td className="r num">
                        {t.side === "SELL" && t.pnl != null ? (
                          <span className={t.pnl > 0 ? "up" : t.pnl < 0 ? "down" : ""}>
                            {inr(t.pnl)}
                          </span>
                        ) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
