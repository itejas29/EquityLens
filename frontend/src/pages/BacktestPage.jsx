import { useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { apiClient, apiErrorMessage } from "../api/client";
import ErrorMessage from "../components/ErrorMessage";
import Loading from "../components/Loading";
import PageHeader from "../components/PageHeader";
import { chartColors, tooltipStyle } from "../lib/chartTheme";

const METRIC_LABELS = {
  total_return_pct: "Total return",
  cagr_pct: "CAGR",
  sharpe_ratio: "Sharpe",
  sortino_ratio: "Sortino",
  max_drawdown_pct: "Max drawdown",
  max_drawdown_duration_days: "Drawdown days",
  final_equity: "Final equity",
  num_trades: "Trades",
  win_rate_pct: "Win rate",
  avg_win: "Avg win",
  avg_loss: "Avg loss",
  profit_factor: "Profit factor",
  avg_holding_period_days: "Avg hold (days)",
};

function formatMetric(key, value) {
  if (value == null) return "—";
  if (key.endsWith("_pct")) return `${value}%`;
  if (["avg_win", "avg_loss", "final_equity"].includes(key)) return `₹${value.toLocaleString("en-IN")}`;
  return value;
}

function MetricsTable({ metrics, title }) {
  return (
    <div className="card">
      <div className="section-title">{title}</div>
      <div className="metrics-grid">
        {Object.entries(METRIC_LABELS).map(([key, label]) =>
          metrics[key] !== undefined ? (
            <div className="metric-tile" key={key}>
              <div className="metric-label">{label}</div>
              <div className="metric-value">{formatMetric(key, metrics[key])}</div>
            </div>
          ) : null
        )}
      </div>
    </div>
  );
}

export default function BacktestPage() {
  const [form, setForm] = useState({
    start_date: "2025-03-01",
    end_date: "2026-08-01",
    initial_capital: 500000,
    risk_appetite: "moderate",
    rebalance_frequency: "monthly",
    horizon_days: 90,
  });
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  function update(field, value) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    setResult(null);
    try {
      const payload = {
        ...form,
        initial_capital: Number(form.initial_capital),
        horizon_days: Number(form.horizon_days),
      };
      const res = await apiClient.post("/backtest", payload);
      setResult(res.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  const chartData = result
    ? mergeCurves(result.equity_curve, result.benchmark_equity_curve)
    : [];

  return (
    <div>
      <PageHeader
        title="Backtest"
        subtitle="Replay the scoring strategy over a historical window and compare it against NIFTY 50 buy & hold. Past performance does not indicate future returns."
      />
      <ErrorMessage message={error} />
      <form onSubmit={handleSubmit} className="card" style={{ marginBottom: 24 }}>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="start">Start date</label>
            <input id="start" type="date" value={form.start_date} onChange={(e) => update("start_date", e.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="end">End date</label>
            <input id="end" type="date" value={form.end_date} onChange={(e) => update("end_date", e.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="capital">Initial capital</label>
            <input id="capital" type="number" value={form.initial_capital} onChange={(e) => update("initial_capital", e.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="risk">Risk appetite</label>
            <select id="risk" value={form.risk_appetite} onChange={(e) => update("risk_appetite", e.target.value)}>
              <option value="low">Low</option>
              <option value="moderate">Moderate</option>
              <option value="high">High</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="freq">Rebalance frequency</label>
            <select id="freq" value={form.rebalance_frequency} onChange={(e) => update("rebalance_frequency", e.target.value)}>
              <option value="monthly">Monthly</option>
              <option value="quarterly">Quarterly</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="horizon">Horizon (days)</label>
            <input id="horizon" type="number" value={form.horizon_days} onChange={(e) => update("horizon_days", e.target.value)} />
          </div>
        </div>
        <button className="btn btn-primary" type="submit" disabled={loading}>
          {loading ? "Running backtest..." : "Run backtest"}
        </button>
      </form>

      {loading && <Loading label="Running simulation — this can take up to a minute..." />}

      {result && (
        <>
          <p className="muted" style={{ marginBottom: 16 }}>{result.methodology_note}</p>

          <div className="split-grid" style={{ marginBottom: 20 }}>
            <MetricsTable metrics={result.metrics} title="Strategy" />
            <MetricsTable metrics={result.benchmark_metrics} title="NIFTY 50 buy & hold" />
          </div>

          <div className="card" style={{ marginBottom: 20 }}>
            <div className="section-title">Equity curve vs benchmark</div>
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke={chartColors.grid} />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: chartColors.axis }} minTickGap={40} stroke={chartColors.grid} />
                <YAxis domain={["auto", "auto"]} tick={{ fontSize: 11, fill: chartColors.axis }} stroke={chartColors.grid} />
                <Tooltip {...tooltipStyle} />
                <Legend />
                <Line type="monotone" dataKey="strategy" stroke={chartColors.accent} dot={false} name="Strategy" strokeWidth={2} />
                <Line type="monotone" dataKey="benchmark" stroke={chartColors.muted} dot={false} name="NIFTY 50" strokeWidth={1.5} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="card">
            <div className="section-title">Trade log ({result.trade_log.length})</div>
            <div className="table-wrap" style={{ maxHeight: 400, overflowY: "auto" }}>
              <table>
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Entry</th>
                    <th>Exit</th>
                    <th>Entry px</th>
                    <th>Exit px</th>
                    <th>Qty</th>
                    <th>P&L</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {result.trade_log.map((t, i) => (
                    <tr key={i}>
                      <td style={{ fontWeight: 700 }}>{t.symbol}</td>
                      <td>{t.entry_date}</td>
                      <td>{t.exit_date}</td>
                      <td>{t.entry_price}</td>
                      <td>{t.exit_price}</td>
                      <td>{t.quantity}</td>
                      <td className={t.pnl >= 0 ? "pnl-positive" : "pnl-negative"}>{t.pnl}</td>
                      <td style={{ fontFamily: "var(--font-ui)", color: "var(--text-muted)" }}>{t.exit_reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function mergeCurves(strategyCurve, benchmarkCurve) {
  const benchByDate = {};
  for (const p of benchmarkCurve) benchByDate[p.date] = p.equity;
  return strategyCurve.map((p) => ({ date: p.date, strategy: p.equity, benchmark: benchByDate[p.date] ?? null }));
}
