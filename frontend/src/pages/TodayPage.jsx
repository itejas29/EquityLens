/**
 * Today's Picks — full view of the v1 signal snapshot.
 * Same data as Home's primary section, with the provenance strip and the
 * per-pick analysis panel available.
 */
import { useEffect, useState } from "react";
import { apiClient, apiErrorMessage } from "../api/client";
import PicksTable from "../components/PicksTable";
import { useLivePrices } from "../lib/useLivePrices";
import {
  EmptyState, ErrorState, LoadingState, ProvenanceStrip, SectionHeader, fmtDate,
} from "../components/ui/Primitives";

export default function TodayPage() {
  const [data, setData] = useState(null);
  const [watchlist, setWatchlist] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  // Fast-tier quotes arrive over the socket; the signal rows themselves are
  // fetched once and stay frozen, which is the point — only the price moves.
  const { quotes } = useLivePrices();

  function load() {
    setLoading(true);
    Promise.allSettled([apiClient.get("/daily-signals"), apiClient.get("/watchlist")])
      .then(([s, w]) => {
        if (s.status === "fulfilled") setData(s.value.data);
        else setError(apiErrorMessage(s.reason));
        if (w.status === "fulfilled") setWatchlist(w.value.data);
        setLoading(false);
      });
  }
  useEffect(load, []);

  const stale = data?.is_current === false;

  return (
    <div className="page">
      <SectionHeader
        title={stale ? "Latest available picks" : "Today's picks"}
        sub={
          data
            ? `${data.count} of ${data.universe_size} screened · priced off the ${fmtDate(data.market_data_through)} session`
            : "Generated automatically at 9:15 AM IST on trading days"
        }
      />

      {data && <ProvenanceStrip data={data} />}

      {loading && !data ? (
        <LoadingState rows={8} />
      ) : error ? (
        <ErrorState message={error} onRetry={load} />
      ) : !data?.signals?.length ? (
        <EmptyState
          title="No picks published yet"
          body="Once the 9:15 AM IST run completes, the stocks that passed the screen appear here."
        />
      ) : (
        <>
          <PicksTable signals={data.signals} watchlist={watchlist} quotes={quotes} />
          <p style={{ fontSize: 12, color: "var(--text-3)", marginTop: 14, maxWidth: 760, lineHeight: 1.6 }}>
            Levels come from a 14-day ATR against the {fmtDate(data.market_data_through)} close and are fixed for the
            day. Stops sit 4 ATR below entry; targets at twice the distance to the stop.
            {data.regime === "bear" && " NIFTY is below its 200-day average, so v1 calls for 25% of normal position size."}
            {" "}Analysis only — not investment advice.
          </p>
          <p style={{ fontSize: 12, color: "var(--text-3)", marginTop: 6, maxWidth: 760, lineHeight: 1.6 }}>
            <strong>12 months is how far back the ranking looks, not how long a pick is meant to be held.</strong>{" "}
            The screen re-ranks monthly and exits into whatever ranks higher, so a position rarely runs anywhere
            near that long — in backtesting, the typical holding period before rebalance, stop, or target ran its
            course was about 7–9 weeks.
          </p>
        </>
      )}
    </div>
  );
}
