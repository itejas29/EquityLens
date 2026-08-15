import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiClient, apiErrorMessage } from "../api/client";
import ErrorMessage from "../components/ErrorMessage";
import PageHeader from "../components/PageHeader";
import { SECTORS } from "../lib/constants";

export default function AnalyzePage() {
  const navigate = useNavigate();
  const [capital, setCapital] = useState(500000);
  const [riskAppetite, setRiskAppetite] = useState("moderate");
  const [horizon, setHorizon] = useState("6m");
  const [sectors, setSectors] = useState([]);
  const [maxStocks, setMaxStocks] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  function toggleSector(sector) {
    setSectors((prev) => (prev.includes(sector) ? prev.filter((s) => s !== sector) : [...prev, sector]));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const payload = {
        capital: Number(capital),
        risk_appetite: riskAppetite,
        horizon: horizon || null,
        sectors: sectors.length ? sectors : null,
        max_stocks: maxStocks ? Number(maxStocks) : null,
      };
      const res = await apiClient.post("/portfolio/analyze", payload);
      navigate("/recommendations", { state: { analyzeResult: res.data, analyzeRequest: payload } });
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Analyze"
        subtitle="Size a paper portfolio from the current scored universe using your capital, risk appetite and sector preferences."
      />
      <ErrorMessage message={error} />
      <form onSubmit={handleSubmit} className="card" style={{ maxWidth: 620 }}>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="capital">Capital (INR)</label>
            <input
              id="capital"
              type="number"
              min={1}
              value={capital}
              onChange={(e) => setCapital(e.target.value)}
              required
            />
          </div>
          <div className="field">
            <label htmlFor="risk">Risk appetite</label>
            <select id="risk" value={riskAppetite} onChange={(e) => setRiskAppetite(e.target.value)}>
              <option value="low">Low</option>
              <option value="moderate">Moderate</option>
              <option value="high">High</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="horizon">Horizon</label>
            <input id="horizon" placeholder="e.g. 6m, 1y" value={horizon} onChange={(e) => setHorizon(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="maxStocks">Max stocks (5-10)</label>
            <input
              id="maxStocks"
              type="number"
              min={5}
              max={10}
              placeholder="default 10"
              value={maxStocks}
              onChange={(e) => setMaxStocks(e.target.value)}
            />
          </div>
        </div>
        <div className="field">
          <label>Sector filter (optional)</label>
          <div className="chip-group">
            {SECTORS.map((sector) => {
              const checked = sectors.includes(sector);
              return (
                <label key={sector} className={`chip${checked ? " checked" : ""}`}>
                  <input type="checkbox" checked={checked} onChange={() => toggleSector(sector)} />
                  {sector}
                </label>
              );
            })}
          </div>
        </div>
        <button className="btn btn-primary" type="submit" disabled={submitting} style={{ marginTop: 4 }}>
          {submitting ? "Analyzing..." : "Analyze portfolio"}
        </button>
      </form>
    </div>
  );
}
