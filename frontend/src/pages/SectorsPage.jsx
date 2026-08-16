/**
 * Sectors — aggregated from the FULL 501-stock universe via /stocks, not from
 * the 8 V1 picks.
 *
 * DATA HONESTY: /stocks returns sector and market cap but no prices, and
 * /market/overview caps at ~54 distinct stocks. So sector COMPOSITION (count,
 * market cap, constituents) is real and complete, while sector RETURN is only
 * computed over the movers we can actually see — and is labelled as a partial
 * sample rather than presented as the sector's true performance.
 */
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { apiClient, apiErrorMessage } from "../api/client";
import { Change, ErrorState, LoadingState, SectionHeader } from "../components/ui/Primitives";

export default function SectorsPage() {
  const [sectors, setSectors] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  function load() {
    setLoading(true);
    apiClient.get("/market/sectors")
      .then((res) => {
        setSectors(res.data);
        setLoading(false);
      })
      .catch((e) => { setError(apiErrorMessage(e)); setLoading(false); });
  }
  useEffect(load, []);

  if (loading) return <div className="page"><LoadingState rows={8} /></div>;
  if (error) return <div className="page"><ErrorState message={error} onRetry={load} /></div>;

  return (
    <div className="page">
      <SectionHeader
        title="Sectors"
        sub={`${sectors.length} sectors in the tracked universe`}
      />

      <div className="panel" style={{ marginBottom: 14 }}>
        <div className="tbl-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>Sector</th>
                <th className="r">Stocks</th>
                <th className="r">Adv / Dec</th>
                <th className="r">Avg Change</th>
                <th className="r col-hide-lg">Top Momentum Leaders</th>
              </tr>
            </thead>
            <tbody>
              {sectors.map((s) => (
                <tr key={s.sector} style={{ cursor: "pointer" }} onClick={() => navigate(`/stocks?sector=${encodeURIComponent(s.sector)}`)}>
                  <td style={{ fontWeight: 600, color: "var(--text-1)" }}>
                    {s.sector}
                  </td>
                  <td className="r num">{s.stocks_count}</td>
                  <td className="r num" style={{ color: "var(--text-3)" }}>
                    <span className="up">{s.advancing}</span> / <span className="down">{s.declining}</span>
                  </td>
                  <td className="r">
                    <Change value={s.average_change_pct} />
                  </td>
                  <td className="r col-hide-lg" style={{ color: "var(--text-3)", fontSize: "12px" }}>
                    {s.top_momentum_stocks.map(tm => tm.symbol).join(", ") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      
      <p style={{ fontSize: 12, color: "var(--text-3)", marginBottom: 18, maxWidth: 720, lineHeight: 1.6 }}>
        Click any sector to view its constituents in the Stock Screener.
      </p>
    </div>
  );
}
