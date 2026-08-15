import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { apiClient, apiErrorMessage } from "../api/client";
import ErrorMessage from "../components/ErrorMessage";
import EmptyState from "../components/EmptyState";
import PageHeader from "../components/PageHeader";
import SignalBadge from "../components/SignalBadge";
import { SkeletonTable } from "../components/Skeleton";

const COLUMNS = [
  { key: "symbol", label: "Symbol" },
  { key: "sector", label: "Sector" },
  { key: "latest_price", label: "Price" },
  { key: "overall_score", label: "Score" },
  { key: "signal", label: "Signal" },
];

function compareValues(a, b) {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  if (typeof a === "string") return a.localeCompare(b);
  return a - b;
}

export default function WatchlistPage() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [symbolInput, setSymbolInput] = useState("");
  const [adding, setAdding] = useState(false);
  const [sortKey, setSortKey] = useState("symbol");
  const [sortDir, setSortDir] = useState("asc");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const res = await apiClient.get("/watchlist");
      setItems(res.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleAdd(e) {
    e.preventDefault();
    if (!symbolInput.trim()) return;
    setAdding(true);
    setError("");
    try {
      await apiClient.post("/watchlist", { symbol: symbolInput.trim().toUpperCase() });
      setSymbolInput("");
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setAdding(false);
    }
  }

  async function handleRemove(symbol) {
    try {
      await apiClient.delete(`/watchlist/${symbol}`);
      setItems((prev) => prev.filter((i) => i.symbol !== symbol));
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  }

  function toggleSort(key) {
    if (key === sortKey) {
      setSortDir((prev) => (prev === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  const sortedItems = useMemo(() => {
    const copy = [...items];
    copy.sort((a, b) => {
      const cmp = compareValues(a[sortKey], b[sortKey]);
      return sortDir === "asc" ? cmp : -cmp;
    });
    return copy;
  }, [items, sortKey, sortDir]);

  return (
    <div>
      <PageHeader
        title="Watchlist"
        subtitle="Track symbols you want to follow. Scores and signals update whenever the universe is re-scored."
        actions={
          items.length > 0 ? (
            <span className="muted mono">
              {items.length} {items.length === 1 ? "symbol" : "symbols"}
            </span>
          ) : null
        }
      />
      <form onSubmit={handleAdd} className="card" style={{ marginBottom: 20, display: "flex", gap: 8, maxWidth: 420 }}>
        <input
          aria-label="NSE symbol"
          placeholder="Symbol, e.g. TCS"
          value={symbolInput}
          onChange={(e) => setSymbolInput(e.target.value)}
          style={{ flex: 1 }}
        />
        <button className="btn btn-primary" type="submit" disabled={adding}>
          {adding ? "Adding..." : "Add"}
        </button>
      </form>

      <ErrorMessage message={error} />
      {loading ? (
        <SkeletonTable rows={5} columns={6} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 17.3 5.8 21l1.7-7.1L2 9.2l7.2-.6L12 2l2.8 6.6 7.2.6-5.5 4.7 1.7 7.1Z" />
            </svg>
          }
          title="Your watchlist is empty"
          description="Add an NSE symbol above, or open any stock from Recommendations to start following it."
          action={
            <Link className="btn" to="/recommendations">
              Browse recommendations
            </Link>
          }
        />
      ) : (
        <div className="card table-wrap">
          <table>
            <thead>
              <tr>
                {COLUMNS.map((col) => (
                  <th key={col.key} className="sortable" onClick={() => toggleSort(col.key)}>
                    {col.label}
                    {sortKey === col.key && <span className="sort-arrow">{sortDir === "asc" ? "▲" : "▼"}</span>}
                  </th>
                ))}
                <th></th>
              </tr>
            </thead>
            <tbody>
              {sortedItems.map((item) => (
                <tr key={item.stock_id}>
                  <td>
                    <Link to={`/stocks/${item.symbol}`} style={{ fontWeight: 700 }}>
                      {item.symbol}
                    </Link>
                  </td>
                  <td style={{ fontFamily: "var(--font-ui)", color: "var(--text-muted)" }}>{item.sector || "—"}</td>
                  <td>{item.latest_price != null ? item.latest_price.toFixed(2) : "—"}</td>
                  <td>{item.overall_score ?? "—"}</td>
                  <td>
                    <SignalBadge signal={item.signal} />
                  </td>
                  <td>
                    <button className="btn btn-ghost" onClick={() => handleRemove(item.symbol)}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
