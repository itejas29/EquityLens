import { useEffect, useState } from "react";
import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { apiClient, apiErrorMessage } from "../api/client";
import Loading from "../components/Loading";
import ErrorMessage from "../components/ErrorMessage";

const PIE_COLORS = ["#2563eb", "#15803d", "#b91c1c", "#92400e", "#7c3aed", "#0891b2", "#db2777", "#65a30d"];

function sectorBreakdown(holdings) {
  const totals = {};
  for (const h of holdings) {
    const key = h.sector || "Unknown";
    totals[key] = (totals[key] || 0) + h.allocated_amount;
  }
  return Object.entries(totals).map(([name, value]) => ({ name, value: Math.round(value) }));
}

function PortfolioCard({ portfolio }) {
  const pieData = sectorBreakdown(portfolio.holdings);
  return (
    <div className="card" style={{ marginBottom: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h3 style={{ margin: 0 }}>{portfolio.name}</h3>
        <span className="muted">
          {portfolio.risk_appetite} · ₹{portfolio.capital.toLocaleString("en-IN")} · opened {portfolio.created_at.slice(0, 10)}
        </span>
      </div>

      <div className="metrics-grid" style={{ marginTop: 16 }}>
        <div className="metric-tile">
          <div className="metric-label">Market value</div>
          <div className="metric-value">₹{portfolio.total_market_value.toLocaleString("en-IN")}</div>
        </div>
        <div className="metric-tile">
          <div className="metric-label">Unrealized P&L</div>
          <div className="metric-value" style={{ color: (portfolio.total_unrealized_pnl ?? 0) >= 0 ? "#15803d" : "#b91c1c" }}>
            {portfolio.total_unrealized_pnl != null ? `₹${portfolio.total_unrealized_pnl.toLocaleString("en-IN")}` : "—"}
          </div>
        </div>
        <div className="metric-tile">
          <div className="metric-label">Holdings</div>
          <div className="metric-value">{portfolio.holdings.length}</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 20, marginTop: 16 }}>
        <div>
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Qty</th>
                <th>Entry</th>
                <th>Current</th>
                <th>P&L</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {portfolio.holdings.map((h) => (
                <tr key={h.id}>
                  <td>{h.symbol}</td>
                  <td>{h.quantity}</td>
                  <td>{h.entry_price.toFixed(2)}</td>
                  <td>{h.current_price != null ? h.current_price.toFixed(2) : "—"}</td>
                  <td style={{ color: (h.unrealized_pnl ?? 0) >= 0 ? "#15803d" : "#b91c1c" }}>
                    {h.unrealized_pnl != null ? `${h.unrealized_pnl.toFixed(2)} (${h.unrealized_pnl_pct.toFixed(1)}%)` : "—"}
                  </td>
                  <td>{h.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div>
          <ResponsiveContainer width="100%" height={220}>
            <PieChart>
              <Pie data={pieData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={80} label={(d) => d.name}>
                {pieData.map((_, i) => (
                  <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip formatter={(v) => `₹${v.toLocaleString("en-IN")}`} />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

export default function PortfolioPage() {
  const [portfolios, setPortfolios] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    apiClient
      .get("/portfolio")
      .then((res) => setPortfolios(res.data))
      .catch((err) => setError(apiErrorMessage(err)))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <h2>Portfolio</h2>
      <ErrorMessage message={error} />
      {loading ? (
        <Loading label="Loading portfolios..." />
      ) : portfolios.length === 0 ? (
        <p className="muted">
          No saved portfolios yet. Run an analysis on the Analyze page and save the result.
        </p>
      ) : (
        portfolios.map((p) => <PortfolioCard key={p.id} portfolio={p} />)
      )}
    </div>
  );
}
