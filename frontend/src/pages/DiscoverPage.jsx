/**
 * Discover — movers and momentum leaders, each section compact.
 * Momentum leaders come from the published v1 signal ranking (a real momentum
 * percentile), not from a client-side calculation.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiClient, apiErrorMessage } from "../api/client";
import MoversTable from "../components/MoversTable";
import MomentumTable from "../components/MomentumTable";
import {
  EmptyState, ErrorState, LoadingState, ScoreBadge, SectionHeader, SignalBadge, StockCell, inr,
} from "../components/ui/Primitives";

const TABS = [
  { key: "gainers", label: "Top gainers" },
  { key: "losers", label: "Top losers" },
  { key: "most_traded", label: "Most active" },
];

export default function DiscoverPage() {
  const [overview, setOverview] = useState(null);
  const [momentum, setMomentum] = useState(null);
  const [discover, setDiscover] = useState(null);
  const [tab, setTab] = useState("gainers");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  function load() {
    setLoading(true);
    Promise.allSettled([
      apiClient.get("/market/overview", { params: { limit: 12 } }),
      apiClient.get("/daily-signals/momentum-leaders"),
      apiClient.get("/market/discover")
    ]).then(([o, m, d]) => {
      if (o.status === "fulfilled") setOverview(o.value.data);
      else setErr(apiErrorMessage(o.reason));
      if (m.status === "fulfilled") setMomentum(m.value.data);
      if (d.status === "fulfilled") setDiscover(d.value.data);
      setLoading(false);
    });
  }
  useEffect(load, []);

  if (loading && !overview && !momentum && !discover) return <div className="page"><LoadingState rows={6} /></div>;

  return (
    <div className="page">
      <section className="section">
        <SectionHeader
          title="Momentum leaders"
          sub={momentum ? `Ranked across the full ${momentum.universe_size}-stock universe` : "Ranked across the full universe"}
        />
        {momentum ? (
          <MomentumTable rows={momentum.leaders.slice(0, 20)} />
        ) : err ? (
          <ErrorState message={err} onRetry={load} />
        ) : (
          <LoadingState rows={3} />
        )}
      </section>

      <section className="section">
        <SectionHeader title="Movers" sub={overview ? `${overview.universe_size} stocks tracked` : null} />
        <div className="chips" role="tablist" aria-label="Mover type" style={{ marginBottom: 12 }}>
          {TABS.map((t) => (
            <button key={t.key} role="tab" aria-selected={tab === t.key}
              className={`chip ${tab === t.key ? "on" : ""}`} onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </div>
        {err && !overview ? <ErrorState message={err} onRetry={load} />
          : <MoversTable rows={overview?.[tab] || []} showTurnover={tab === "most_traded"} />}
      </section>

      {discover && discover.near_52w_high?.length > 0 && (
        <section className="section">
          <SectionHeader title="Near 52-week High" sub="Within 5% of their 52-week high" />
          <div className="panel">
            <div className="tbl-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Stock</th>
                    <th className="r">Price</th>
                    <th className="r">Distance to High</th>
                    <th className="r">Momentum Percentile</th>
                  </tr>
                </thead>
                <tbody>
                  {discover.near_52w_high.map((s) => (
                    <tr key={s.symbol}>
                      <td><StockCell symbol={s.symbol} company={s.company_name} sector={s.sector} /></td>
                      <td className="r num">{inr(s.price)}</td>
                      <td className="r num">{s.distance_pct}%</td>
                      <td className="r"><ScoreBadge value={s.momentum_percentile} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      )}

      {discover && discover.strong_momentum_volume?.length > 0 && (
        <section className="section">
          <SectionHeader title="Strong Momentum + Volume" sub="Momentum > 80, Volume > 1.5x average" />
          <div className="panel">
            <div className="tbl-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Stock</th>
                    <th className="r">Price</th>
                    <th className="r">Volume Ratio</th>
                    <th className="r">Momentum Percentile</th>
                  </tr>
                </thead>
                <tbody>
                  {discover.strong_momentum_volume.map((s) => (
                    <tr key={s.symbol}>
                      <td><StockCell symbol={s.symbol} company={s.company_name} sector={s.sector} /></td>
                      <td className="r num">{inr(s.price)}</td>
                      <td className="r num">{s.volume_ratio}x</td>
                      <td className="r"><ScoreBadge value={s.momentum_percentile} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
