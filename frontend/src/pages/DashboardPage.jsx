import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiClient } from "../api/client";
import { SkeletonTable } from "../components/Skeleton";
import Sparkline from "../components/Sparkline";

/**
 * Explore — the market read for the current session.
 *
 * Every number here comes from /market/overview, derived from stored bars. The
 * version this replaced fabricated nearly all of it: direction picked with
 * Math.random(), a hardcoded "+15.22 (1.24%)" on every card, randomised volume,
 * and the recommendations list relabelled as "most traded".
 */

const TABS = [
  { key: "gainers", label: "Gainers" },
  { key: "losers", label: "Losers" },
  { key: "most_traded", label: "Most traded" },
];

function inr(v, digits = 2) {
  if (v == null) return "—";
  return `₹${v.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function compactVolume(v) {
  if (v == null) return "—";
  if (v >= 10_000_000) return `${(v / 10_000_000).toFixed(2)} Cr`;
  if (v >= 100_000) return `${(v / 100_000).toFixed(2)} L`;
  return v.toLocaleString("en-IN");
}

function Change({ change, pct }) {
  const up = pct > 0;
  const down = pct < 0;
  return (
    <span className={up ? "text-bullish" : down ? "text-danger" : "muted"} style={{ fontSize: 13, fontWeight: 500 }}>
      {up ? "+" : ""}
      {change.toFixed(2)} ({Math.abs(pct).toFixed(2)}%)
    </span>
  );
}

export default function DashboardPage() {
  const [overview, setOverview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("gainers");

  useEffect(() => {
    let cancelled = false;
    apiClient
      .get("/market/overview", { params: { limit: 10 } })
      .then((res) => !cancelled && setOverview(res.data))
      .catch(() => !cancelled && setError("Could not load market data."))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) return <SkeletonTable rows={5} columns={4} />;

  if (error || !overview) {
    return <div className="card"><p className="muted">{error || "No market data available."}</p></div>;
  }

  const session = overview.as_of
    ? new Date(`${overview.as_of}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "long" })
    : null;
  const rows = overview[tab] || [];
  // Turnover is a fact about the session — unlike the previous "most traded"
  // list, which was just the top rows of the recommendations table.
  const headline = overview.most_traded.slice(0, 4);

  return (
    <div>
      <div style={{ marginBottom: 40 }}>
        <h2 style={{ fontSize: 20, fontWeight: 500, color: "var(--text)", margin: "0 0 4px" }}>
          Most traded on NSE
        </h2>
        <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 20 }}>
          By turnover ·{" "}
          {overview.source === "live"
            ? "live prices"
            : overview.source === "closed"
              ? `market closed, last traded ${session}`
              : `close, ${session}`}{" "}
          ·{" "}
          {overview.universe_size} stocks tracked
        </div>

        <div className="groww-most-traded-grid">
          {headline.map((m) => (
            <Link to={`/stocks/${m.symbol}`} key={m.symbol} className="groww-mt-card">
              <div className="groww-mt-logo">{m.symbol.substring(0, 2)}</div>
              <div className="groww-mt-name">{m.company_name || m.symbol}</div>
              <div className="groww-mt-price">{inr(m.close)}</div>
              <div className={`groww-mt-change ${m.change_pct >= 0 ? "positive" : "negative"}`}>
                {m.change_pct >= 0 ? "+" : ""}
                {m.change.toFixed(2)} ({Math.abs(m.change_pct).toFixed(2)}%)
              </div>
            </Link>
          ))}
        </div>

        <Link to="/recommendations" style={{ fontWeight: 500, fontSize: 14 }}>
          See the full scored universe &gt;
        </Link>
      </div>

      <div>
        <h2 style={{ fontSize: 20, fontWeight: 500, color: "var(--text)", margin: "0 0 4px" }}>Top movers</h2>
        <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 16 }}>
          {overview.advancing} advancing · {overview.declining} declining · {overview.unchanged} unchanged
        </div>

        <div className="groww-pill-tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.key}
              role="tab"
              aria-selected={tab === t.key}
              className={`groww-pill-tab ${tab === t.key ? "active" : ""}`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="groww-table-wrap">
          <table className="groww-table">
            <thead>
              <tr>
                <th style={{ width: "34%" }}>Company</th>
                <th style={{ width: "22%" }}>
                  <span className="visually-hidden">30-day trend</span>
                </th>
                <th className="text-right">Market price (1D)</th>
                <th className="text-right">Volume</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((m) => (
                <tr key={m.symbol}>
                  <td>
                    <div className="groww-company-cell">
                      <div className="groww-company-logo">{m.symbol.substring(0, 1)}</div>
                      <div>
                        <Link to={`/stocks/${m.symbol}`} className="groww-company-name" style={{ textDecoration: "none" }}>
                          {m.company_name || m.symbol}
                        </Link>
                        <div className="groww-company-sub">{m.sector || "—"}</div>
                      </div>
                    </div>
                  </td>
                  <td>
                    <Sparkline symbol={m.symbol} width={110} height={28} />
                  </td>
                  <td className="text-right">
                    <div style={{ color: "var(--text)", marginBottom: 4 }}>{inr(m.close)}</div>
                    <Change change={m.change} pct={m.change_pct} />
                  </td>
                  <td className="text-right">{compactVolume(m.volume)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
