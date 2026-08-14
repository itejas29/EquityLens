import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiClient, apiErrorMessage } from "../api/client";
import Loading from "../components/Loading";
import ErrorMessage from "../components/ErrorMessage";
import SignalBadge from "../components/SignalBadge";

export default function WatchlistPage() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [symbolInput, setSymbolInput] = useState("");
  const [adding, setAdding] = useState(false);

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

  return (
    <div>
      <h2>Watchlist</h2>
      <form onSubmit={handleAdd} className="card" style={{ marginBottom: 20, display: "flex", gap: 8, maxWidth: 400 }}>
        <input
          placeholder="Symbol, e.g. TCS"
          value={symbolInput}
          onChange={(e) => setSymbolInput(e.target.value)}
          style={{ flex: 1, padding: "8px 10px", border: "1px solid var(--border)", borderRadius: 6 }}
        />
        <button className="btn btn-primary" type="submit" disabled={adding}>
          {adding ? "Adding..." : "Add"}
        </button>
      </form>

      <ErrorMessage message={error} />
      {loading ? (
        <Loading label="Loading watchlist..." />
      ) : items.length === 0 ? (
        <p className="muted">Your watchlist is empty.</p>
      ) : (
        <div className="card">
          {items.map((item) => (
            <div className="list-row" key={item.stock_id}>
              <div>
                <Link to={`/stocks/${item.symbol}`} style={{ fontWeight: 700 }}>
                  {item.symbol}
                </Link>
                <div className="muted">{item.sector || "—"}</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
                <span>{item.latest_price != null ? item.latest_price.toFixed(2) : "—"}</span>
                <span>{item.overall_score ?? "—"}</span>
                <SignalBadge signal={item.signal} />
                <button className="btn" onClick={() => handleRemove(item.symbol)}>
                  Remove
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
