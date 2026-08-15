import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { apiClient, apiErrorMessage } from "../api/client";
import ErrorMessage from "../components/ErrorMessage";
import EmptyState from "../components/EmptyState";
import PageHeader from "../components/PageHeader";
import SignalBadge from "../components/SignalBadge";
import ScoreBar from "../components/ScoreBar";
import { SkeletonCards } from "../components/Skeleton";
import { SECTORS } from "../lib/constants";

const SUB_SCORES = ["technical_score", "fundamental_score", "valuation_score", "risk_score"];

// The per-input chips this replaced were accurate but unreadable — eight tags
// per card, most of them the same two universe-wide gaps. What a reader
// actually needs is whether the composite rests on all four measures or fewer;
// the field-level detail is still on the API response for anyone who wants it.
function ScoreBasis({ rec }) {
  const present = SUB_SCORES.filter((key) => rec[key] != null).length;
  if (present === SUB_SCORES.length) return null;
  return (
    <p className="score-basis">
      Scored on {present} of {SUB_SCORES.length} measures — price data incomplete for this stock.
    </p>
  );
}

function RecCard({ rec, holding }) {
  return (
    <div className="card card-hover rec-card">
      <div className="rec-card-header">
        <div>
          <Link to={`/stocks/${rec.symbol}`} className="rec-card-symbol">
            {rec.symbol}
          </Link>
          <div className="rec-card-sector">{rec.sector || "—"}</div>
        </div>
        <SignalBadge signal={rec.signal} />
      </div>

      <div>
        <ScoreBar label="Overall" value={rec.overall_score} />
        <ScoreBar label="Technical" value={rec.technical_score} />
        <ScoreBar label="Fundamental" value={rec.fundamental_score} />
        <ScoreBar label="Valuation" value={rec.valuation_score} />
        <ScoreBar label="Risk" value={rec.risk_score} />
      </div>

      <div>
        <div className="stat-row">
          <span className="stat-label">Entry zone</span>
          <span>
            {rec.entry_low != null ? `${rec.entry_low.toFixed(2)} - ${rec.entry_high.toFixed(2)}` : "—"}
          </span>
        </div>
        <div className="stat-row">
          <span className="stat-label">Stop / Target</span>
          <span>
            {rec.stop_loss != null ? rec.stop_loss.toFixed(2) : "—"} / {rec.target_price != null ? rec.target_price.toFixed(2) : "—"}
          </span>
        </div>
        <div className="stat-row">
          <span className="stat-label">Risk:Reward</span>
          <span>{rec.risk_reward != null ? `1:${rec.risk_reward}` : "—"}</span>
        </div>
        {rec.ml_probability != null && (
          <div className="stat-row">
            <span className="stat-label">ML probability</span>
            <span>{(rec.ml_probability * 100).toFixed(1)}%</span>
          </div>
        )}
        {holding && (
          <>
            <div className="stat-row">
              <span className="stat-label">Suggested shares</span>
              <span>{holding.shares}</span>
            </div>
            <div className="stat-row">
              <span className="stat-label">Allocation</span>
              <span>
                ₹{holding.allocated_amount.toLocaleString("en-IN")}
                {holding.allocation_capped ? " (capped)" : ""}
              </span>
            </div>
          </>
        )}
      </div>
      <ScoreBasis rec={rec} />
    </div>
  );
}

export default function RecommendationsPage() {
  const location = useLocation();
  const analyzeResult = location.state?.analyzeResult || null;
  const analyzeRequest = location.state?.analyzeRequest || null;

  const [recommendations, setRecommendations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [minScore, setMinScore] = useState("");
  const [sector, setSector] = useState("");
  const [saveName, setSaveName] = useState("My portfolio");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [saved, setSaved] = useState(false);

  async function handleSave() {
    if (!analyzeRequest) return;
    setSaving(true);
    setSaveError("");
    try {
      await apiClient.post("/portfolio", { ...analyzeRequest, name: saveName });
      setSaved(true);
    } catch (err) {
      setSaveError(apiErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  // Filters are passed explicitly rather than read from state so a chip click can
  // apply immediately, without waiting for the state update to land.
  async function loadRecommendations(overrides = {}) {
    const nextMinScore = overrides.minScore ?? minScore;
    const nextSector = overrides.sector ?? sector;
    setLoading(true);
    setError("");
    try {
      const params = { limit: 50 };
      if (nextMinScore) params.min_score = nextMinScore;
      if (nextSector) params.sector = nextSector;
      const res = await apiClient.get("/recommendations", { params });
      setRecommendations(res.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  function selectSector(value) {
    const next = sector === value ? "" : value;
    setSector(next);
    loadRecommendations({ sector: next });
  }

  function clearFilters() {
    setMinScore("");
    setSector("");
    loadRecommendations({ minScore: "", sector: "" });
  }

  useEffect(() => {
    loadRecommendations();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const holdingsBySymbol = {};
  if (analyzeResult) {
    for (const h of analyzeResult.holdings) holdingsBySymbol[h.symbol] = h;
  }

  return (
    <div>
      {analyzeResult && (
        <div className="card" style={{ marginBottom: 24 }}>
          <div className="section-title">Your portfolio analysis</div>
          <div className="metrics-grid">
            <div className="metric-tile">
              <div className="metric-label">Capital</div>
              <div className="metric-value">₹{analyzeResult.capital.toLocaleString("en-IN")}</div>
            </div>
            <div className="metric-tile">
              <div className="metric-label">Deployed</div>
              <div className="metric-value">₹{analyzeResult.deployed_capital.toLocaleString("en-IN")}</div>
            </div>
            <div className="metric-tile">
              <div className="metric-label">Cash</div>
              <div className="metric-value">
                ₹{analyzeResult.cash.toLocaleString("en-IN")} ({(analyzeResult.cash_pct * 100).toFixed(1)}%)
              </div>
            </div>
            <div className="metric-tile">
              <div className="metric-label">Weighted risk score</div>
              <div className="metric-value">{analyzeResult.weighted_risk_score ?? "—"}</div>
            </div>
          </div>
          <p className="muted">{analyzeResult.disclaimer}</p>

          {analyzeRequest && (
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 12, flexWrap: "wrap" }}>
              {saved ? (
                <span className="muted">
                  Saved. View it on the <Link to="/portfolio">Portfolio</Link> page.
                </span>
              ) : (
                <>
                  <input
                    aria-label="Portfolio name"
                    value={saveName}
                    onChange={(e) => setSaveName(e.target.value)}
                    style={{ maxWidth: 220 }}
                  />
                  <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
                    {saving ? "Saving..." : "Save this portfolio"}
                  </button>
                  <ErrorMessage message={saveError} />
                </>
              )}
            </div>
          )}
        </div>
      )}

      <PageHeader
        title="Recommendations"
        subtitle="Composite scores across the scored universe. Entry, stop and target levels are analysis output — not investment advice."
        actions={
          recommendations.length > 0 ? (
            <span className="muted mono">
              {recommendations.length} {recommendations.length === 1 ? "stock" : "stocks"}
            </span>
          ) : null
        }
      />

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="filter-bar" style={{ marginBottom: 14 }}>
          <div className="field">
            <label htmlFor="minScore">Min score</label>
            <input
              id="minScore"
              type="number"
              min={0}
              max={100}
              value={minScore}
              onChange={(e) => setMinScore(e.target.value)}
              placeholder="0"
              style={{ width: 120 }}
            />
          </div>
          <button className="btn btn-primary" onClick={() => loadRecommendations()} disabled={loading}>
            {loading ? "Loading..." : "Apply filters"}
          </button>
          {(minScore || sector) && (
            <button className="btn btn-ghost" onClick={clearFilters}>
              Clear
            </button>
          )}
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label>Sector</label>
          <div className="chip-group">
            {SECTORS.map((s) => (
              // Single-select: the API takes one sector, so picking another replaces it.
              <button
                type="button"
                key={s}
                className={`chip${sector === s ? " checked" : ""}`}
                aria-pressed={sector === s}
                onClick={() => selectSector(s)}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      </div>

      <ErrorMessage message={error} />
      {loading ? (
        <SkeletonCards count={6} />
      ) : recommendations.length === 0 ? (
        <EmptyState
          icon={
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 2 2 7l10 5 10-5-10-5Z" />
              <path d="m2 17 10 5 10-5" />
              <path d="m2 12 10 5 10-5" />
            </svg>
          }
          title="No recommendations match"
          description={
            minScore || sector
              ? "No scored stock passes these filters. Try lowering the minimum score or clearing the sector."
              : "The universe has not been scored yet. Run scoring on the backend to populate recommendations."
          }
          action={
            (minScore || sector) && (
              <button
                className="btn"
                onClick={() => {
                  setMinScore("");
                  setSector("");
                }}
              >
                Clear filters
              </button>
            )
          }
        />
      ) : (
        <div className="rec-grid">
          {recommendations.map((rec) => (
            <RecCard key={rec.stock_id} rec={rec} holding={holdingsBySymbol[rec.symbol]} />
          ))}
        </div>
      )}
    </div>
  );
}
