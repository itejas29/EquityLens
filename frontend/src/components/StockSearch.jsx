import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiClient } from "../api/client";

const DEBOUNCE_MS = 220;
const MIN_QUERY = 1;

export default function StockSearch() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [ingesting, setIngesting] = useState(null);
  const [error, setError] = useState("");
  const boxRef = useRef(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (query.trim().length < MIN_QUERY) {
      setResults([]);
      return;
    }
    // Debounced so a fast typist fires one request, not one per keystroke.
    const timer = setTimeout(async () => {
      setBusy(true);
      try {
        const res = await apiClient.get("/stocks/search", { params: { q: query.trim(), limit: 8 } });
        setResults(res.data.results);
        setError("");
      } catch {
        setResults([]);
        setError("Search is unavailable right now.");
      } finally {
        setBusy(false);
      }
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    function onClickAway(e) {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickAway);
    return () => document.removeEventListener("mousedown", onClickAway);
  }, []);

  async function choose(hit) {
    if (hit.is_ingested) {
      setOpen(false);
      setQuery("");
      navigate(`/stocks/${hit.symbol}`);
      return;
    }
    // Not ingested yet — pull it, then go. Takes a few seconds; the row shows
    // its own progress rather than blanking the whole dropdown.
    setIngesting(hit.symbol);
    setError("");
    try {
      await apiClient.post(`/stocks/${hit.symbol}/ingest`);
      setOpen(false);
      setQuery("");
      navigate(`/stocks/${hit.symbol}`);
    } catch (err) {
      setError(err?.response?.data?.error?.message || `Could not load data for ${hit.symbol}.`);
    } finally {
      setIngesting(null);
    }
  }

  return (
    <div className="stock-search" ref={boxRef}>
      <svg className="stock-search-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-3.5-3.5" />
      </svg>
      <input
        type="text"
        value={query}
        placeholder="Search any NSE stock..."
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            if (results.length > 0) {
              choose(results[0]);
            } else if (query.trim()) {
              setOpen(false);
              navigate(`/stocks?q=${encodeURIComponent(query.trim())}`);
              setQuery("");
            }
          }
          if (e.key === "Escape") setOpen(false);
        }}
        aria-label="Search NSE stocks"
      />

      {open && (query.trim().length >= MIN_QUERY || error) && (
        <div className="stock-search-panel">
          {error && <div className="stock-search-error">{error}</div>}
          {busy && results.length === 0 && <div className="stock-search-note">Searching...</div>}
          {!busy && results.length === 0 && !error && (
            <div className="stock-search-note">No NSE listing matches "{query.trim()}".</div>
          )}
          {results.map((hit) => (
            <button
              key={hit.symbol}
              className="stock-search-hit"
              onClick={() => choose(hit)}
              disabled={ingesting !== null}
            >
              <span className="stock-search-sym">{hit.symbol}</span>
              <span className="stock-search-name">{hit.company_name}</span>
              <span className={`stock-search-tag ${hit.is_ingested ? "ready" : "new"}`}>
                {ingesting === hit.symbol ? "Loading data..." : hit.is_ingested ? "Ready" : "Fetch"}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
