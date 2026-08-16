/**
 * Watchlist. Backed by the real /watchlist endpoints (GET/POST/DELETE), so
 * additions and removals persist server-side. No local-only fallback: if the
 * API fails the page says so rather than pretending an item was saved.
 */
import { useEffect, useState } from "react";
import { apiClient, apiErrorMessage } from "../api/client";
import {
  EmptyState, ErrorState, LoadingState, ScoreBadge, SectionHeader, SignalBadge, StockCell, inr,
} from "../components/ui/Primitives";

export default function WatchlistPage() {
  const [items, setItems] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(null);

  function load() {
    setLoading(true);
    apiClient.get("/watchlist")
      .then((r) => { setItems(r.data); setError(""); })
      .catch((e) => setError(apiErrorMessage(e)))
      .finally(() => setLoading(false));
  }
  useEffect(load, []);

  async function remove(symbol) {
    setBusy(symbol);
    try {
      await apiClient.delete(`/watchlist/${symbol}`);
      setItems((prev) => prev.filter((i) => i.symbol !== symbol));
    } catch (e) {
      setError(apiErrorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="page">
      <SectionHeader title="Watchlist" sub={items.length ? `${items.length} stock${items.length === 1 ? "" : "s"}` : null} />
      {loading ? <LoadingState rows={4} />
        : error ? <ErrorState message={error} onRetry={load} />
        : !items.length ? (
          <EmptyState
            title="Your watchlist is empty"
            body="Search for a stock and add it to follow its EquityLens score and signal here."
          />
        ) : (
          <div className="panel">
            <div className="tbl-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Stock</th><th className="r">Price</th><th className="r">Score</th>
                    <th className="r">Signal</th><th className="r" style={{ width: 70 }}></th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((i) => (
                    <tr key={i.symbol}>
                      <td><StockCell symbol={i.symbol} sector={i.sector} /></td>
                      <td className="r price num">{inr(i.latest_price)}</td>
                      <td className="r"><ScoreBadge value={i.overall_score} showBar={false} /></td>
                      <td className="r"><SignalBadge signal={i.signal} /></td>
                      <td className="r">
                        <button className="btn btn-sm" disabled={busy === i.symbol} onClick={() => remove(i.symbol)}>
                          {busy === i.symbol ? "…" : "Remove"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="cards">
              {items.map((i) => (
                <div key={i.symbol} className="card-row">
                  <div style={{ minWidth: 0 }}>
                    <span className="sym">{i.symbol}</span>
                    <div className="co">{i.sector || "—"}</div>
                  </div>
                  <div className="card-r">
                    <span className="price num">{inr(i.latest_price)}</span>
                    <SignalBadge signal={i.signal} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
    </div>
  );
}
