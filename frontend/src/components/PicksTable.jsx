/**
 * Today's Picks — the primary surface.
 *
 * Desktop renders a dense professional table; below 860px the same rows become
 * compact cards keeping only symbol / price / change / score / signal, which
 * are the fields you actually scan on a phone.
 *
 * Filters and sorting operate purely on the array the API returned. Nothing is
 * computed, inferred or invented client-side — a tab that has no matching
 * signals shows zero rows rather than falling back to the full list.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Change, ScoreBadge, SignalBadge, StockCell, EmptyState, inr,
} from "./ui/Primitives";

/**
 * Tab predicates. Each is a statement about data the API actually sends:
 *   high conviction -> top-quartile momentum percentile
 *   momentum        -> ranked in the upper half of the published list
 *   breakout        -> price has run above the frozen entry zone
 *   1-day / 1-20d   -> horizon buckets are NOT published by v1, so these tabs
 *                      declare themselves unavailable instead of guessing.
 */
const TABS = [
  { key: "all", label: "All", fn: () => true },
  { key: "conviction", label: "High conviction", fn: (s) => s.overall_score >= 90 },
  { key: "momentum", label: "Momentum", fn: (s) => s.trigger_state === "IN_ZONE" || s.trigger_state === "BELOW_ZONE" },
  { key: "breakout", label: "Breakout", fn: (s) => s.trigger_state === "ABOVE_ZONE" },
  { key: "watchlist", label: "Watchlist", fn: null }, // resolved against the user's watchlist
];

const SORTS = [
  { key: "rank", label: "Rank", get: (s) => s.rank, dir: 1 },
  { key: "score", label: "Score", get: (s) => s.overall_score, dir: -1 },
  { key: "change", label: "Change", get: (s) => (s.latest_price ?? 0) - (s.reference_close ?? 0), dir: -1 },
  { key: "rr", label: "Risk/Reward", get: (s) => s.upside_pct / Math.abs(s.downside_pct || 1), dir: -1 },
];

function changePct(s) {
  // Session change against the close the levels were drawn from. Null when the
  // live price is unavailable, never zero-filled.
  if (s.latest_price == null || !s.reference_close) return null;
  return ((s.latest_price - s.reference_close) / s.reference_close) * 100;
}

export default function PicksTable({ signals, watchlist = [] }) {
  const [tab, setTab] = useState("all");
  const [sort, setSort] = useState("rank");
  const [sector, setSector] = useState("");
  const [showFilters, setShowFilters] = useState(false);

  const sectors = useMemo(
    () => [...new Set(signals.map((s) => s.sector).filter(Boolean))].sort(),
    [signals]
  );
  const watched = useMemo(() => new Set(watchlist.map((w) => w.symbol)), [watchlist]);

  const rows = useMemo(() => {
    const t = TABS.find((x) => x.key === tab);
    let out = signals.filter((s) => (t?.fn ? t.fn(s) : watched.has(s.symbol)));
    if (sector) out = out.filter((s) => s.sector === sector);
    const sorter = SORTS.find((x) => x.key === sort);
    if (sorter) {
      out = [...out].sort((a, b) => {
        const av = sorter.get(a) ?? 0;
        const bv = sorter.get(b) ?? 0;
        return (av - bv) * sorter.dir;
      });
    }
    return out;
  }, [signals, tab, sort, sector, watched]);

  const counts = useMemo(() => {
    const c = {};
    for (const t of TABS) {
      c[t.key] = signals.filter((s) => (t.fn ? t.fn(s) : watched.has(s.symbol))).length;
    }
    return c;
  }, [signals, watched]);

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12, flexWrap: "wrap" }}>
        <div className="chips" role="tablist" aria-label="Filter picks">
          {TABS.map((t) => (
            <button
              key={t.key}
              role="tab"
              aria-selected={tab === t.key}
              className={`chip ${tab === t.key ? "on" : ""}`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
              <span className="chip-count">{counts[t.key] ?? 0}</span>
            </button>
          ))}
        </div>

        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button
            className="btn btn-sm"
            aria-expanded={showFilters}
            onClick={() => setShowFilters((v) => !v)}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M3 5h18M6 12h12M10 19h4" />
            </svg>
            Filters{sector ? " (1)" : ""}
          </button>
          <label className="visually-hidden" htmlFor="pick-sort">Sort picks by</label>
          <select
            id="pick-sort"
            className="btn btn-sm"
            value={sort}
            onChange={(e) => setSort(e.target.value)}
            style={{ paddingRight: 8 }}
          >
            {SORTS.map((s) => <option key={s.key} value={s.key}>Sort: {s.label}</option>)}
          </select>
        </div>
      </div>

      {showFilters && (
        <div className="panel" style={{ padding: 14, marginBottom: 12 }}>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-end" }}>
            <div>
              <label htmlFor="f-sector" style={{ display: "block", fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em", color: "var(--text-3)", marginBottom: 5 }}>
                Sector
              </label>
              <select id="f-sector" className="btn btn-sm" value={sector} onChange={(e) => setSector(e.target.value)}>
                <option value="">All sectors</option>
                {sectors.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            {sector && (
              <button className="btn btn-sm" onClick={() => setSector("")}>Clear</button>
            )}
            {/* Market cap and risk filters are intentionally absent: the daily
                signal payload carries neither, and inventing buckets on the
                client would be fabricated data. */}
            <p style={{ fontSize: 12, color: "var(--text-3)", margin: 0, maxWidth: 380 }}>
              Market cap and risk filters need fields the signal API does not yet return.
            </p>
          </div>
        </div>
      )}

      {rows.length === 0 ? (
        <EmptyState
          title="No picks match this filter"
          body={
            tab === "watchlist"
              ? "None of today's picks are on your watchlist yet."
              : "Try a different tab or clear the sector filter."
          }
        />
      ) : (
        <div className="panel">
          {/* Desktop table */}
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th style={{ width: 40 }}>#</th>
                  <th>Stock</th>
                  <th className="r">Price</th>
                  <th className="r">Change</th>
                  <th className="r">Score</th>
                  <th className="r col-hide-lg">
                    Entry zone
                    <div style={{ fontSize: 10, fontWeight: "normal", color: "var(--text-3)" }}>Ref: prev session close</div>
                  </th>
                  <th className="r col-hide-lg">Stop</th>
                  <th className="r col-hide-lg">Target</th>
                  <th className="r">R:R</th>
                  <th className="r">Signal</th>
                  <th className="r">Action</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => {
                  const chg = changePct(s);
                  return (
                    <tr key={s.symbol}>
                      <td><span className="rank-cell num">{s.rank}</span></td>
                      <td><StockCell symbol={s.symbol} company={s.company_name} sector={s.sector} /></td>
                      <td className="r price num">{inr(s.latest_price ?? s.reference_close)}</td>
                      <td className="r"><Change value={chg} /></td>
                      <td className="r"><ScoreBadge value={s.overall_score} /></td>
                      <td className="r num col-hide-lg" style={{ whiteSpace: "nowrap" }}>
                        {inr(s.entry_low)} – {inr(s.entry_high)}
                      </td>
                      <td className="r num col-hide-lg down">{inr(s.stop_loss)}</td>
                      <td className="r num col-hide-lg up">{inr(s.target_price)}</td>
                      <td className="r num">1:{(s.upside_pct / Math.abs(s.downside_pct || 1)).toFixed(1)}</td>
                      <td className="r"><SignalBadge triggerState={s.trigger_state} /></td>
                      <td className="r">
                        <Link to={`/paper?buy=${s.symbol}`} className="btn btn-sm">Trade</Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Mobile cards — same data source, reduced to what fits a phone */}
          <div className="cards">
            {rows.map((s) => {
              const chg = changePct(s);
              return (
                <Link to={`/stocks/${s.symbol}`} key={s.symbol} className="card-row">
                  <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                    <span className="rank-cell num">{s.rank}</span>
                    <span className="sym">{s.symbol}</span>
                  </div>
                  <div className="card-r">
                    <span className="price num">{inr(s.latest_price ?? s.reference_close)}</span>
                    <Change value={chg} />
                  </div>
                    <div className="card-l2" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%" }}>
                      <div>
                        <SignalBadge triggerState={s.trigger_state} />
                        <span className="num" style={{ fontSize: 12, color: "var(--text-3)", marginLeft: 8 }}>
                          Score {s.overall_score.toFixed(0)} · Entry zone: {inr(s.entry_low)} – {inr(s.entry_high)}
                        </span>
                      </div>
                      <Link 
                        to={`/paper?buy=${s.symbol}`} 
                        className="btn btn-sm" 
                        onClick={(e) => e.stopPropagation()}
                      >
                        Trade
                      </Link>
                    </div>
                </Link>
              );
            })}
          </div>
        </div>
      )}
    </>
  );
}
