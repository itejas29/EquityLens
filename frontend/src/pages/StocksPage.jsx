/**
 * Stocks — a screener over the FULL universe (501 stocks), plus catalogue
 * search across all 2,406 NSE listings.
 *
 * DATA HONESTY: /stocks returns symbol, company, sector, industry and market
 * cap — but no price, change or momentum. Those columns are therefore absent
 * rather than blank-filled, and the note below says which filters need a
 * backend endpoint. This page deliberately shares no data with Today's Picks.
 */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { apiClient, apiErrorMessage } from "../api/client";
import { EmptyState, ErrorState, LoadingState, SectionHeader, compactNum } from "../components/ui/Primitives";

const CAP_BANDS = [
  { key: "", label: "All caps", fn: () => true },
  { key: "large", label: "Large (>₹50k Cr)", fn: (s) => (s.market_cap || 0) >= 5e11 },
  { key: "mid", label: "Mid (₹10k–50k Cr)", fn: (s) => (s.market_cap || 0) >= 1e11 && (s.market_cap || 0) < 5e11 },
  { key: "small", label: "Small (<₹10k Cr)", fn: (s) => (s.market_cap || 0) > 0 && (s.market_cap || 0) < 1e11 },
];

export default function StocksPage() {
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState("");
  const [sector, setSector] = useState("");
  const [cap, setCap] = useState("");
  const [sort, setSort] = useState("mcap");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  function load() {
    setLoading(true);
    apiClient.get("/stocks/screener")
      .then((r) => {
        setRows(r.data);
        setError("");
      })
      .catch((e) => setError(apiErrorMessage(e)))
      .finally(() => setLoading(false));
  }
  useEffect(load, []);

  const [momFilter, setMomFilter] = useState("");
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 50;

  const sectors = useMemo(() => [...new Set(rows.map((r) => r.sector).filter(Boolean))].sort(), [rows]);

  const view = useMemo(() => {
    let out = rows;
    const term = q.trim().toUpperCase();
    if (term) out = out.filter((r) => r.symbol.includes(term) || (r.company_name || "").toUpperCase().includes(term));
    if (sector) out = out.filter((r) => r.sector === sector);
    const band = CAP_BANDS.find((b) => b.key === cap);
    if (band) out = out.filter(band.fn);
    
    if (momFilter === "high") out = out.filter((r) => (r.momentum_percentile || 0) >= 80);
    else if (momFilter === "low") out = out.filter((r) => (r.momentum_percentile || 0) < 20);
    
    return [...out].sort((a, b) => {
      if (sort === "symbol") return a.symbol.localeCompare(b.symbol);
      if (sort === "mcap") return (b.market_cap || 0) - (a.market_cap || 0);
      if (sort === "mom") return (b.momentum_percentile || 0) - (a.momentum_percentile || 0);
      if (sort === "vol") return (b.volume || 0) - (a.volume || 0);
      if (sort === "chg") return (b.change_pct || 0) - (a.change_pct || 0);
      if (sort === "pos52") return (b.position_52w || 0) - (a.position_52w || 0);
      return 0;
    });
  }, [rows, q, sector, cap, sort, momFilter]);

  const paginatedView = useMemo(() => {
    return view.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  }, [view, page]);

  // Reset page when filters change
  useEffect(() => {
    setPage(1);
  }, [q, sector, cap, sort, momFilter]);
  
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const sec = params.get("sector");
    if (sec) setSector(sec);
  }, []);

  if (loading) return <div className="page"><LoadingState rows={10} /></div>;
  if (error) return <div className="page"><ErrorState message={error} onRetry={load} /></div>;

  return (
    <div className="page">
      <SectionHeader
        title="Stocks"
        sub={`Browse and filter the full ${rows.length}-stock tracked universe`}
      />

      <div className="panel" style={{ padding: 12, marginBottom: 14 }}>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          <div className="search-wrap" style={{ flex: "1 1 240px", maxWidth: 340 }}>
            <span className="search-ico" aria-hidden="true">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
              </svg>
            </span>
            <input
              className="search-in" value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="Filter by symbol or company" aria-label="Filter stocks"
            />
          </div>
          <select className="btn btn-sm" value={sector} onChange={(e) => setSector(e.target.value)} aria-label="Sector">
            <option value="">All sectors</option>
            {sectors.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select className="btn btn-sm" value={cap} onChange={(e) => setCap(e.target.value)} aria-label="Market cap">
            {CAP_BANDS.map((b) => <option key={b.key} value={b.key}>{b.label}</option>)}
          </select>
          <select className="btn btn-sm" value={momFilter} onChange={(e) => setMomFilter(e.target.value)} aria-label="Momentum filter">
            <option value="">All Momentum</option>
            <option value="high">High (&gt;80)</option>
            <option value="low">Low (&lt;20)</option>
          </select>
          <select className="btn btn-sm" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort by">
            <option value="mcap">Sort: Market cap</option>
            <option value="mom">Sort: Momentum</option>
            <option value="vol">Sort: Volume</option>
            <option value="chg">Sort: Change %</option>
            <option value="pos52">Sort: 52W Pos %</option>
            <option value="symbol">Sort: Symbol</option>
          </select>
          <span style={{ marginLeft: "auto", fontSize: 12.5, color: "var(--text-3)" }}>
            {view.length} of {rows.length}
          </span>
        </div>
      </div>

      {view.length === 0 ? (
        <EmptyState title="No stocks match" body="Try a different sector, cap band or search term." />
      ) : (
        <div className="panel">
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th className="r">Price</th>
                  <th className="r">Change</th>
                  <th className="r col-hide-lg">Volume</th>
                  <th className="r">Momentum</th>
                  <th className="r">Market cap</th>
                  <th className="r col-hide-lg">52W Pos</th>
                </tr>
              </thead>
              <tbody>
                {paginatedView.map((s) => (
                  <tr key={s.symbol}>
                    <td>
                        <Link to={`/stocks/${s.symbol}`} className="sym">{s.symbol}</Link>
                        <div className="co" style={{fontSize: "11px", color: "var(--text-3)"}}>{s.sector || "—"}</div>
                    </td>
                    <td className="r num price">{s.price ? inr(s.price) : "—"}</td>
                    <td className="r num"><Change value={s.change_pct} /></td>
                    <td className="r num col-hide-lg">{s.volume ? compactNum(s.volume) : "—"}</td>
                    <td className="r"><ScoreBadge value={s.momentum_percentile} /></td>
                    <td className="r num">{s.market_cap ? compactNum(s.market_cap) : "—"}</td>
                    <td className="r num col-hide-lg">{s.position_52w != null ? s.position_52w.toFixed(1) + "%" : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          
          <div className="pagination" style={{ padding: 12, display: "flex", justifyContent: "space-between", alignItems: "center", borderTop: "1px solid var(--border-1)" }}>
             <button className="btn btn-sm" disabled={page === 1} onClick={() => setPage(page - 1)}>Prev</button>
             <span style={{ fontSize: 12, color: "var(--text-3)" }}>Page {page} of {Math.ceil(view.length / PAGE_SIZE)}</span>
             <button className="btn btn-sm" disabled={page >= Math.ceil(view.length / PAGE_SIZE)} onClick={() => setPage(page + 1)}>Next</button>
          </div>
        </div>
      )}
    </div>
  );
}
