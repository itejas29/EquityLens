/**
 * Home — answers, in order: what is the market doing, what does EquityLens
 * recommend today, what is moving, and what news matters.
 *
 * Today's Picks is the primary section and sits directly under a compact
 * market strip. Everything below it is secondary and deliberately shorter.
 *
 * All data comes from /market/overview, /daily-signals and /watchlist. There
 * is no fallback list: if a request fails the section says so.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiClient, apiErrorMessage } from "../api/client";
import MarketStrip from "../components/MarketStrip";
import { useLivePrices } from "../lib/useLivePrices";
import MoversTable from "../components/MoversTable";
import PicksTable from "../components/PicksTable";
import {
  EmptyState, ErrorState, LoadingState, ProvenanceStrip, SectionHeader, fmtDate,
} from "../components/ui/Primitives";

export default function HomePage() {
  const [overview, setOverview] = useState(null);
  const [signals, setSignals] = useState(null);
  const [watchlist, setWatchlist] = useState([]);
  const [errors, setErrors] = useState({});
  const [loading, setLoading] = useState(true);
  // Fast-tier quotes: carries ^NSEI for the live index level, plus the picks.
  const { quotes } = useLivePrices();

  function load() {
    setLoading(true);
    // Each panel owns its own error so one failing endpoint degrades a single
    // section rather than blanking the page.
    const jobs = [
      ["overview", () => apiClient.get("/market/overview", { params: { limit: 6 } })],
      ["signals", () => apiClient.get("/daily-signals")],
      ["watchlist", () => apiClient.get("/watchlist")],
    ];
    Promise.allSettled(jobs.map(([, f]) => f())).then((res) => {
      const errs = {};
      res.forEach((r, i) => {
        const key = jobs[i][0];
        if (r.status === "rejected") { errs[key] = apiErrorMessage(r.reason); return; }
        if (key === "overview") setOverview(r.value.data);
        if (key === "signals") setSignals(r.value.data);
        if (key === "watchlist") setWatchlist(r.value.data);
      });
      setErrors(errs);
      setLoading(false);
    });
  }

  useEffect(load, []);

  const picksTitle = signals?.is_current === false ? "Latest available picks" : "Today's picks";

  return (
    <div className="page">
      <section className="section">
        <SectionHeader title="Market overview" />
        <MarketStrip
          overview={overview}
          signals={signals}
          quotes={quotes}
          loading={loading && !overview}
          error={errors.overview}
          onRetry={load}
        />
      </section>

      <section className="section">
        <SectionHeader
          title={picksTitle}
          sub={
            signals?.count != null
              ? `${signals.count} of ${signals.universe_size} stocks screened by EquityLens ${signals.strategy_version}`
              : "Generated daily at 9:15 AM IST"
          }
          action={<Link to="/today" className="section-link">View all {signals?.count ?? 8} →</Link>}
        />

        {signals && <ProvenanceStrip data={signals} />}

        {loading && !signals ? (
          <LoadingState rows={3} />
        ) : errors.signals ? (
          <ErrorState message={errors.signals} onRetry={load} />
        ) : !signals?.signals?.length ? (
          <EmptyState
            title="No picks published yet"
            body="Signals are generated automatically at 9:15 AM IST on trading days."
          />
        ) : (
          /* PREVIEW ONLY — the full V1 list lives on Today's Picks. Home is a
             market dashboard; duplicating all 8 here is what made the two
             pages feel identical. */
          <PicksTable signals={signals.signals.slice(0, 3)} watchlist={watchlist} quotes={quotes} compact />
        )}
      </section>

      <section className="section">
        <SectionHeader
          title="Most active"
          sub="By traded value in the last completed session"
          action={<Link to="/discover" className="section-link">More in Discover →</Link>}
        />
        {errors.overview ? (
          <ErrorState message={errors.overview} onRetry={load} />
        ) : (
          <MoversTable rows={overview?.most_traded?.slice(0, 5) || []} showTurnover />
        )}
      </section>

      <section className="section">
        <SectionHeader title="Market movers" action={<Link to="/discover" className="section-link">All movers →</Link>} />
        <div className="g-2">
          <div>
            <h3 style={{ fontSize: 13, marginBottom: 8, color: "var(--text-3)" }}>Top gainers</h3>
            <MoversTable rows={overview?.gainers?.slice(0, 5) || []} showSpark={false} />
          </div>
          <div>
            <h3 style={{ fontSize: 13, marginBottom: 8, color: "var(--text-3)" }}>Top losers</h3>
            <MoversTable rows={overview?.losers?.slice(0, 5) || []} showSpark={false} />
          </div>
        </div>
      </section>

      <section className="section">
        <SectionHeader title="Sector snapshot" action={<Link to="/sectors" className="section-link">All sectors →</Link>} />
        <EmptyState
          title="Sector performance needs a universe-wide quote endpoint"
          body="Sector composition is available on the Sectors page. True sector returns require change data for all 500 stocks; the movers feed only exposes about 54."
        />
      </section>

      <section className="section">
        <SectionHeader title="News" sub="Market and stock coverage" />
        <EmptyState
          title="No news feed connected"
          body="No news provider is wired to the backend yet. Headlines will appear here once one is, rather than being generated."
        />
      </section>

      {signals?.market_data_through && (
        <p style={{ fontSize: 12, color: "var(--text-3)", marginTop: 4 }}>
          Prices reflect the session ending {fmtDate(signals.market_data_through)}. Analysis only — not investment advice.
        </p>
      )}
    </div>
  );
}
