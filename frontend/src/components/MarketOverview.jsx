import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiClient } from "../api/client";

const TABS = [
  { key: "gainers", label: "Top gainers" },
  { key: "losers", label: "Top losers" },
  { key: "most_traded", label: "Most traded" },
];

function rupees(v, digits = 2) {
  if (v == null) return "—";
  return `₹${v.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

// Turnover reads better in the units Indian market coverage actually uses.
function crores(v) {
  if (v == null) return "—";
  const cr = v / 10_000_000;
  return cr >= 100 ? `₹${cr.toFixed(0)} Cr` : `₹${cr.toFixed(1)} Cr`;
}

function Breadth({ advancing, declining, unchanged }) {
  const total = advancing + declining + unchanged;
  if (!total) return null;
  const pct = (n) => (n / total) * 100;
  return (
    <div className="breadth">
      <div className="breadth-bar" role="img" aria-label={`${advancing} advancing, ${declining} declining`}>
        <span className="breadth-up" style={{ width: `${pct(advancing)}%` }} />
        <span className="breadth-flat" style={{ width: `${pct(unchanged)}%` }} />
        <span className="breadth-down" style={{ width: `${pct(declining)}%` }} />
      </div>
      <div className="breadth-legend">
        <span className="text-bullish">{advancing} advancing</span>
        <span className="muted">{unchanged} flat</span>
        <span className="text-danger">{declining} declining</span>
      </div>
    </div>
  );
}

export default function MarketOverview() {
  const [data, setData] = useState(null);
  const [tab, setTab] = useState("gainers");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    apiClient
      .get("/market/overview", { params: { limit: 6 } })
      .then((res) => !cancelled && setData(res.data))
      .catch(() => !cancelled && setError("Market data is unavailable."));
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <div className="card"><p className="muted">{error}</p></div>;
  if (!data) return <div className="card"><p className="muted">Loading market data...</p></div>;

  const rows = data[tab] || [];
  const showTurnover = tab === "most_traded";
  const session = data.as_of
    ? new Date(`${data.as_of}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" })
    : null;

  return (
    <div className="card">
      <div className="section-title section-title--split">
        <span>Market today</span>
        {/* Never claim "live" unless the refresher actually has intraday data —
            outside market hours these are closing prices and must say so. */}
        <span className="muted" style={{ fontSize: 12 }}>
          {data.source === "live"
            ? "Live prices"
            : data.source === "closed"
              ? `Closed · last traded ${session}`
              : `Close, ${session}`}{" "}
          · {data.universe_size} stocks
        </span>
      </div>

      <Breadth advancing={data.advancing} declining={data.declining} unchanged={data.unchanged} />

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

      {rows.length === 0 ? (
        <p className="muted">No data for this session.</p>
      ) : (
        <div className="mover-list">
          {rows.map((m) => {
            const up = m.change_pct > 0;
            const down = m.change_pct < 0;
            return (
              <Link to={`/stocks/${m.symbol}`} className="mover-row" key={m.symbol}>
                <span className="mover-id">
                  <span className="mover-sym">{m.symbol}</span>
                  <span className="mover-name">{m.company_name || m.sector || "—"}</span>
                </span>
                <span className="mover-price mono">{rupees(m.close)}</span>
                <span
                  className={`mover-change mono ${up ? "positive" : down ? "negative" : ""}`}
                >
                  {up ? "▲" : down ? "▼" : ""} {Math.abs(m.change_pct).toFixed(2)}%
                </span>
                {showTurnover && <span className="mover-turnover mono">{crores(m.traded_value)}</span>}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
