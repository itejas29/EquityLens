import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiClient, apiErrorMessage } from "../api/client";
import EmptyState from "../components/EmptyState";
import ErrorMessage from "../components/ErrorMessage";
import { SkeletonTable } from "../components/Skeleton";
import Sparkline from "../components/Sparkline";

// Helper to format large numbers properly
function inr(val) {
  return "₹" + val.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export default function PortfolioPage() {
  const [portfolios, setPortfolios] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // {symbol: {change, change_pct}} for the latest completed session, keyed for
  // O(1) lookup while rendering rows.
  const [dayMoves, setDayMoves] = useState({});

  useEffect(() => {
    let cancelled = false;
    apiClient
      .get("/market/overview", { params: { limit: 20 } })
      .then((res) => {
        if (cancelled) return;
        const map = {};
        for (const group of ["gainers", "losers", "most_traded"]) {
          for (const m of res.data[group] || []) {
            map[m.symbol] = { change: m.change, change_pct: m.change_pct };
          }
        }
        setDayMoves(map);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    apiClient.get("/portfolio")
      .then((res) => {
        if (!cancelled) {
          setPortfolios(res.data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(apiErrorMessage(err));
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <div className="main-content">
        <SkeletonTable rows={5} columns={6} />
      </div>
    );
  }

  // We'll flatten the holdings across all portfolios to match the single table view in the screenshot
  const holdings = portfolios.flatMap(p => p.holdings);
  
  // No `|| 1012100` fallbacks here. Those made an empty portfolio display a
  // fabricated ₹10.1L invested / ₹9.67L current as though they were real
  // positions. An empty portfolio is worth zero and should say so.
  const investedValue = holdings.reduce((sum, h) => sum + h.quantity * h.entry_price, 0);
  const currentValue = portfolios.reduce((sum, p) => sum + p.total_market_value, 0);
  const totalReturns = currentValue - investedValue;
  const totalReturnsPct = investedValue > 0 ? (totalReturns / investedValue) * 100 : null;

  // Aggregated from the real per-stock session moves. Only holdings we actually
  // have a session change for contribute; if none do, the figure is null and
  // renders as "—" instead of the previous `totalReturns * 0.4` invention.
  const dayContributions = holdings
    .map((h) => (dayMoves[h.symbol] ? dayMoves[h.symbol].change * h.quantity : null))
    .filter((v) => v !== null);
  const oneDayReturn = dayContributions.length ? dayContributions.reduce((a, b) => a + b, 0) : null;
  const oneDayReturnPct =
    oneDayReturn !== null && investedValue > 0 ? (oneDayReturn / investedValue) * 100 : null;

  return (
    <div className="main-content">
      
      {/* Page Header Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 32 }}>
        <h1 style={{ fontSize: 24, fontWeight: 500, margin: 0, color: 'var(--text)' }}>
          {inr(currentValue)}
        </h1>
        <div style={{ display: 'flex', gap: 12 }}>
          <Link to="/analyze" className="btn btn-outline" style={{ padding: '8px 16px', borderRadius: 6, fontSize: 13 }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ marginRight: 8, verticalAlign: 'middle' }}>
              <path d="M3 3v18h18" />
              <path d="m19 9-5 5-4-4-3 3" />
            </svg>
            Analyse
          </Link>
        </div>
      </div>

      <ErrorMessage message={error} />

      {/* Summary Header */}
      <div className="portfolio-header-metrics">
        <div className="portfolio-header-metric">
          <div className="portfolio-header-label">Invested value</div>
          <div className="portfolio-header-value">{inr(investedValue)}</div>
        </div>
        <div className="portfolio-header-metric text-right">
          <div className="portfolio-header-label">1D returns</div>
          <div className={`portfolio-header-value ${oneDayReturn === null ? "" : oneDayReturn >= 0 ? "positive" : "negative"}`}>
            {oneDayReturn === null
              ? "—"
              : `${oneDayReturn >= 0 ? "+" : ""}${inr(oneDayReturn)}${
                  oneDayReturnPct !== null ? ` (${Math.abs(oneDayReturnPct).toFixed(2)}%)` : ""
                }`}
          </div>
        </div>
        <div className="portfolio-header-metric text-right">
          <div className="portfolio-header-label">Total returns</div>
          <div className={`portfolio-header-value ${totalReturns >= 0 ? 'positive' : 'negative'}`}>
            {totalReturns >= 0 ? '+' : ''}{inr(totalReturns)}
            {totalReturnsPct !== null ? ` (${Math.abs(totalReturnsPct).toFixed(2)}%)` : ""}
          </div>
        </div>
      </div>

      {/* Holdings Table — not rendered when empty: an empty state inside the
          horizontally-scrolling table inherits its min-width and gets clipped
          on phones. */}
      {holdings.length === 0 && !error ? (
        <EmptyState
          title="No portfolio yet"
          description="Run an analysis to build a sized paper portfolio from the current scored universe. Nothing here uses real money."
          action={<Link to="/analyze" className="btn btn-primary">Analyze a portfolio</Link>}
        />
      ) : (
      <div className="groww-table-wrap">
        <table className="groww-table">
          <thead>
            <tr>
              <th style={{ width: '30%' }}>
                Company <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 9 6 6 6-6"/></svg>
              </th>
              <th style={{ width: '20%' }}></th>
              <th className="text-right">
                Market price (1D%) <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 9 6 6 6-6"/></svg>
              </th>
              <th className="text-right">
                Returns (%) <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 9 6 6 6-6"/></svg>
              </th>
              <th className="text-right">
                Current (Invested) <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 9 6 6 6-6"/></svg>
              </th>
            </tr>
          </thead>
          <tbody>
            {holdings.map((h, i) => {
              const currentVal = h.quantity * (h.current_price || h.entry_price);
              const investedVal = h.quantity * h.entry_price;
              const returns = h.unrealized_pnl || 0;
              const returnsPct = h.unrealized_pnl_pct || 0;
              const isPositive = returns >= 0;

              // Real session change, from the last two closes served by
              // /market/overview. Previously this was `returns * 0.1` — a
              // fabricated number rendered as if it were an actual day move.
              // Null when the session data has not loaded or the stock is not
              // in the active universe; the cell renders "—" rather than a guess.
              const day = dayMoves[h.symbol] || null;
              const isOneDayPositive = day ? day.change_pct >= 0 : false;

              return (
                <tr key={h.id || i}>
                  <td>
                    <Link to={`/stocks/${h.symbol}`} className="groww-company-name" style={{ textDecoration: 'none', display: 'block', marginBottom: 4 }}>
                      {h.symbol}
                    </Link>
                    <div className="groww-company-sub">
                      {h.quantity} shares • Avg. {inr(h.entry_price)}
                    </div>
                  </td>
                  <td>
                    <Sparkline symbol={h.symbol} width={100} height={24} />
                  </td>
                  <td className="text-right">
                    <div style={{ color: 'var(--text)', marginBottom: 4 }}>
                      {inr(h.current_price || h.entry_price)}
                    </div>
                    <div
                      className={day ? (isOneDayPositive ? "text-bullish" : "text-danger") : "muted"}
                      style={{ fontSize: 13, fontWeight: 500 }}
                    >
                      {day
                        ? `${isOneDayPositive ? "+" : ""}${(day.change * h.quantity).toFixed(2)} (${Math.abs(day.change_pct).toFixed(2)}%)`
                        : "—"}
                    </div>
                  </td>
                  <td className="text-right">
                    <div style={{ color: 'var(--text)', marginBottom: 4 }}>
                      {returns >= 0 ? '+' : ''}{inr(returns)}
                    </div>
                    <div style={{ color: isPositive ? '#00b386' : '#eb5b3c', fontSize: 13, fontWeight: 500 }}>
                      {Math.abs(returnsPct).toFixed(2)}%
                    </div>
                  </td>
                  <td className="text-right">
                    <div style={{ color: 'var(--text)', marginBottom: 4 }}>
                      {inr(currentVal)}
                    </div>
                    <div className="groww-company-sub">
                      {inr(investedVal)}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      )}

    </div>
  );
}
