import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiClient, apiErrorMessage } from "../api/client";
import Loading from "../components/Loading";
import ErrorMessage from "../components/ErrorMessage";
import SignalBadge from "../components/SignalBadge";
import ScoreBar from "../components/ScoreBar";

export default function StockDetailPage() {
  const { symbol } = useParams();
  const [detail, setDetail] = useState(null);
  const [chartData, setChartData] = useState([]);
  const [recommendation, setRecommendation] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError("");
      try {
        const [detailRes, pricesRes, indicatorsRes, recsRes] = await Promise.all([
          apiClient.get(`/stocks/${symbol}`),
          apiClient.get(`/stocks/${symbol}/prices`),
          apiClient.get(`/stocks/${symbol}/indicators`),
          apiClient.get("/recommendations", { params: { limit: 200 } }),
        ]);
        if (cancelled) return;

        setDetail(detailRes.data);

        const indicatorsByDate = {};
        for (const row of indicatorsRes.data) indicatorsByDate[row.date] = row;
        const merged = pricesRes.data.map((p) => ({
          date: p.date,
          close: p.close,
          dma_50: indicatorsByDate[p.date]?.dma_50 ?? null,
          dma_200: indicatorsByDate[p.date]?.dma_200 ?? null,
          rsi_14: indicatorsByDate[p.date]?.rsi_14 ?? null,
        }));
        setChartData(merged);

        const match = recsRes.data.find((r) => r.symbol === symbol.toUpperCase());
        setRecommendation(match || null);
      } catch (err) {
        if (!cancelled) setError(apiErrorMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  if (loading) return <Loading label={`Loading ${symbol}...`} />;
  if (error) return <ErrorMessage message={error} />;
  if (!detail) return null;

  const lp = detail.latest_price;
  const lf = detail.latest_fundamentals;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
        <div>
          <h2 style={{ margin: 0 }}>
            {detail.symbol} <span className="muted">({detail.company_name})</span>
          </h2>
          <p className="muted" style={{ margin: "4px 0" }}>
            {detail.sector} · {detail.industry}
          </p>
        </div>
        {recommendation && <SignalBadge signal={recommendation.signal} />}
      </div>

      <div className="metrics-grid">
        <div className="metric-tile">
          <div className="metric-label">Last close</div>
          <div className="metric-value">{lp ? lp.close?.toFixed(2) : "—"}</div>
        </div>
        <div className="metric-tile">
          <div className="metric-label">Market cap</div>
          <div className="metric-value">
            {detail.market_cap ? `₹${(detail.market_cap / 1e7).toFixed(0)} Cr` : "—"}
          </div>
        </div>
        <div className="metric-tile">
          <div className="metric-label">P/E</div>
          <div className="metric-value">{lf?.pe_ratio?.toFixed(2) ?? "—"}</div>
        </div>
        <div className="metric-tile">
          <div className="metric-label">P/B</div>
          <div className="metric-value">{lf?.pb_ratio?.toFixed(2) ?? "—"}</div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="section-title">Price with 50/200 DMA</div>
        <ResponsiveContainer width="100%" height={320}>
          <LineChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e5ea" />
            <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={40} />
            <YAxis domain={["auto", "auto"]} tick={{ fontSize: 11 }} />
            <Tooltip />
            <Legend />
            <Line type="monotone" dataKey="close" stroke="#2563eb" dot={false} name="Close" strokeWidth={2} />
            <Line type="monotone" dataKey="dma_50" stroke="#15803d" dot={false} name="50-DMA" strokeWidth={1.5} />
            <Line type="monotone" dataKey="dma_200" stroke="#b91c1c" dot={false} name="200-DMA" strokeWidth={1.5} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="section-title">RSI (14)</div>
        <ResponsiveContainer width="100%" height={160}>
          <LineChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e5ea" />
            <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={40} />
            <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} />
            <Tooltip />
            <ReferenceLine y={70} stroke="#b91c1c" strokeDasharray="4 4" />
            <ReferenceLine y={30} stroke="#15803d" strokeDasharray="4 4" />
            <Line type="monotone" dataKey="rsi_14" stroke="#7c3aed" dot={false} name="RSI 14" strokeWidth={1.5} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        <div className="card">
          <div className="section-title">Fundamentals</div>
          {lf ? (
            <>
              <div className="stat-row"><span className="stat-label">P/E</span><span>{lf.pe_ratio?.toFixed(2) ?? "—"}</span></div>
              <div className="stat-row"><span className="stat-label">P/B</span><span>{lf.pb_ratio?.toFixed(2) ?? "—"}</span></div>
              <div className="stat-row"><span className="stat-label">ROE</span><span>{lf.roe != null ? `${(lf.roe * 100).toFixed(1)}%` : "—"}</span></div>
              <div className="stat-row"><span className="stat-label">ROCE</span><span>{lf.roce != null ? `${(lf.roce * 100).toFixed(1)}%` : "—"}</span></div>
              <div className="stat-row"><span className="stat-label">Debt/Equity</span><span>{lf.debt_to_equity?.toFixed(2) ?? "—"}</span></div>
              <div className="stat-row"><span className="stat-label">Revenue growth</span><span>{lf.revenue_growth != null ? `${(lf.revenue_growth * 100).toFixed(1)}%` : "—"}</span></div>
              <div className="stat-row"><span className="stat-label">EPS growth</span><span>{lf.eps_growth != null ? `${(lf.eps_growth * 100).toFixed(1)}%` : "—"}</span></div>
              <div className="stat-row"><span className="stat-label">Operating margin</span><span>{lf.operating_margin != null ? `${(lf.operating_margin * 100).toFixed(1)}%` : "—"}</span></div>
            </>
          ) : (
            <p className="muted">No fundamentals data.</p>
          )}
        </div>

        <div className="card">
          <div className="section-title">Score breakdown</div>
          {recommendation ? (
            <>
              <ScoreBar label="Overall" value={recommendation.overall_score} />
              <ScoreBar label="Technical" value={recommendation.technical_score} />
              <ScoreBar label="Fundamental" value={recommendation.fundamental_score} />
              <ScoreBar label="Valuation" value={recommendation.valuation_score} />
              <ScoreBar label="Risk" value={recommendation.risk_score} />
              {recommendation.ml_probability != null && (
                <div className="stat-row" style={{ marginTop: 8 }}>
                  <span className="stat-label">ML probability</span>
                  <span>{(recommendation.ml_probability * 100).toFixed(1)}%</span>
                </div>
              )}
            </>
          ) : (
            <p className="muted">Not yet scored — run scoring on the backend.</p>
          )}
        </div>
      </div>
    </div>
  );
}
