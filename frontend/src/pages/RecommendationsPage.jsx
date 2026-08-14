import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { apiClient, apiErrorMessage } from "../api/client";
import Loading from "../components/Loading";
import ErrorMessage from "../components/ErrorMessage";
import SignalBadge from "../components/SignalBadge";
import ScoreBar from "../components/ScoreBar";

function MissingFlags({ missingInputs }) {
  if (!missingInputs) return null;
  const flat = Object.entries(missingInputs).flatMap(([sub, names]) => names.map((n) => `${sub}.${n}`));
  if (!flat.length) return null;
  return (
    <div style={{ marginTop: 6 }}>
      {flat.map((f) => (
        <span key={f} className="tag missing-flag">
          missing: {f}
        </span>
      ))}
    </div>
  );
}

function RecCard({ rec, holding }) {
  return (
    <div className="card rec-card">
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
      <MissingFlags missingInputs={rec.missing_inputs} />
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

  async function loadRecommendations() {
    setLoading(true);
    setError("");
    try {
      const params = { limit: 50 };
      if (minScore) params.min_score = minScore;
      if (sector) params.sector = sector;
      const res = await apiClient.get("/recommendations", { params });
      setRecommendations(res.data);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
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
          <h2 style={{ marginTop: 0 }}>Your portfolio analysis</h2>
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
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
              {saved ? (
                <span className="muted">
                  Saved. View it on the <Link to="/portfolio">Portfolio</Link> page.
                </span>
              ) : (
                <>
                  <input value={saveName} onChange={(e) => setSaveName(e.target.value)} style={{ padding: "6px 10px", border: "1px solid var(--border)", borderRadius: 6 }} />
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

      <h2>Recommendations</h2>
      <div className="card" style={{ marginBottom: 20, display: "flex", gap: 16, alignItems: "flex-end" }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor="minScore">Min score</label>
          <input id="minScore" type="number" min={0} max={100} value={minScore} onChange={(e) => setMinScore(e.target.value)} />
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor="sector">Sector</label>
          <input id="sector" value={sector} onChange={(e) => setSector(e.target.value)} placeholder="e.g. Technology" />
        </div>
        <button className="btn" onClick={loadRecommendations}>
          Apply filters
        </button>
      </div>

      <ErrorMessage message={error} />
      {loading ? (
        <Loading label="Loading recommendations..." />
      ) : recommendations.length === 0 ? (
        <p className="muted">No recommendations found. Run scoring on the backend first.</p>
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
