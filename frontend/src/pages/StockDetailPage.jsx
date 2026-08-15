import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { apiClient, apiErrorMessage } from "../api/client";
import ErrorMessage from "../components/ErrorMessage";
import { SkeletonTable } from "../components/Skeleton";
import Sparkline from "../components/Sparkline";

/**
 * Stock detail — technicals for one symbol, computed from its own data.
 *
 * The version this replaced was entirely hardcoded: a fixed ₹7,056 price, fixed
 * pivot levels, and RSI/MACD/Beta values that were identical for every stock in
 * the universe. Everything below now reads from /stocks/{symbol}/indicators and
 * the symbol's own OHLC bar.
 *
 * Where a value genuinely is not available it renders "—". In particular only
 * the 50- and 200-day moving averages exist in the indicators table; the 10/20/
 * 100-day SMA and EMA rows the old page displayed were invented, so they are
 * gone rather than approximated.
 */

function num(v, digits = 2) {
  return v == null ? "—" : v.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function inr(v, digits = 2) {
  return v == null ? "—" : `₹${num(v, digits)}`;
}

/**
 * Classic floor pivots from the latest completed bar. A documented formula
 * applied to real OHLC — not the fixed ladder the previous page displayed.
 */
function floorPivots(bar) {
  if (!bar || bar.high == null || bar.low == null || bar.close == null) return null;
  const { high: h, low: l, close: c } = bar;
  const p = (h + l + c) / 3;
  const range = h - l;
  return {
    r3: h + 2 * (p - l),
    r2: p + range,
    r1: 2 * p - l,
    pivot: p,
    s1: 2 * p - h,
    s2: p - range,
    s3: l - 2 * (h - p),
  };
}

/** Verdicts derived from the same bands the scoring engine uses. */
function readIndicators(ind, close) {
  if (!ind) return [];
  const out = [];

  if (ind.rsi_14 != null) {
    const v = ind.rsi_14;
    out.push({
      name: "RSI (14)",
      value: num(v, 2),
      verdict: v >= 70 ? "Overbought" : v <= 30 ? "Oversold" : v >= 50 ? "Bullish" : "Neutral",
      tone: v >= 70 ? "bear" : v <= 30 ? "bear" : v >= 50 ? "bull" : "neutral",
    });
  }
  if (ind.macd_hist != null) {
    out.push({
      name: "MACD histogram",
      value: num(ind.macd_hist, 2),
      verdict: ind.macd_hist > 0 ? "Bullish" : "Bearish",
      tone: ind.macd_hist > 0 ? "bull" : "bear",
    });
  }
  if (ind.beta != null) {
    out.push({
      name: "Beta (1Y)",
      value: num(ind.beta, 2),
      verdict: ind.beta >= 1.1 ? "More volatile than index" : ind.beta <= 0.9 ? "Less volatile than index" : "Tracks index",
      tone: ind.beta >= 1.1 ? "bear" : ind.beta <= 0.9 ? "bull" : "neutral",
    });
  }
  if (ind.volatility != null) {
    out.push({ name: "Volatility (30D)", value: `${num(ind.volatility * 100, 2)}%`, verdict: "—", tone: "neutral" });
  }
  if (ind.max_drawdown != null) {
    out.push({
      name: "Max drawdown (1Y)",
      value: `${num(Math.abs(ind.max_drawdown) * 100, 2)}%`,
      verdict: "—",
      tone: "neutral",
    });
  }
  if (ind.volume_ratio != null) {
    out.push({
      name: "Volume vs 20D avg",
      value: `${num(ind.volume_ratio, 2)}x`,
      verdict: ind.volume_ratio >= 1.2 ? "Above average" : "Normal",
      tone: ind.volume_ratio >= 1.2 ? "bull" : "neutral",
    });
  }
  if (ind.dma_50 != null && close != null) {
    out.push({
      name: "Price vs 50 DMA",
      value: `${close >= ind.dma_50 ? "+" : ""}${num(((close - ind.dma_50) / ind.dma_50) * 100, 2)}%`,
      verdict: close >= ind.dma_50 ? "Above" : "Below",
      tone: close >= ind.dma_50 ? "bull" : "bear",
    });
  }
  if (ind.dma_50 != null && ind.dma_200 != null) {
    out.push({
      name: "50 DMA vs 200 DMA",
      value: ind.dma_50 >= ind.dma_200 ? "Golden cross" : "Death cross",
      verdict: ind.dma_50 >= ind.dma_200 ? "Bullish" : "Bearish",
      tone: ind.dma_50 >= ind.dma_200 ? "bull" : "bear",
    });
  }
  return out;
}

const TONE_CLASS = { bull: "text-bullish", bear: "text-danger", neutral: "text-neutral" };

export default function StockDetailPage() {
  const { symbol } = useParams();
  const [stock, setStock] = useState(null);
  const [indicator, setIndicator] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");

    Promise.allSettled([
      apiClient.get(`/stocks/${symbol}`),
      apiClient.get(`/stocks/${symbol}/indicators`),
    ]).then(([detail, indicators]) => {
      if (cancelled) return;
      if (detail.status === "fulfilled") setStock(detail.value.data);
      else setError(apiErrorMessage(detail.reason));

      if (indicators.status === "fulfilled") {
        const rows = indicators.value.data;
        setIndicator(Array.isArray(rows) && rows.length ? rows[rows.length - 1] : null);
      }
      setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [symbol]);

  if (loading) return <SkeletonTable rows={6} columns={3} />;

  if (error || !stock) {
    return (
      <div>
        <ErrorMessage message={error || `No data for ${symbol}.`} />
        <Link to="/dashboard">Back to Explore</Link>
      </div>
    );
  }

  const bar = stock.latest_price;
  const close = bar?.close ?? null;
  const pivots = floorPivots(bar);
  const rows = readIndicators(indicator, close);
  const bulls = rows.filter((r) => r.tone === "bull").length;
  const bears = rows.filter((r) => r.tone === "bear").length;
  const neutrals = rows.filter((r) => r.tone === "neutral").length;

  const stance =
    rows.length === 0
      ? { label: "Not enough data", cls: "muted" }
      : bulls > bears * 2
        ? { label: "Strongly bullish", cls: "text-bullish" }
        : bulls > bears
          ? { label: "Mildly bullish", cls: "text-bullish" }
          : bears > bulls * 2
            ? { label: "Strongly bearish", cls: "text-danger" }
            : bears > bulls
              ? { label: "Mildly bearish", cls: "text-danger" }
              : { label: "Neutral", cls: "text-neutral" };

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, marginBottom: 8 }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 600, margin: 0, color: "var(--text)" }}>{stock.symbol}</h1>
          <div style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 4 }}>
            {stock.company_name || "—"}
            {stock.sector ? ` · ${stock.sector}` : ""}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: 22, fontWeight: 600 }}>{inr(close)}</div>
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Close, {bar?.date || "—"}</div>
        </div>
      </div>

      <div style={{ margin: "16px 0 32px" }}>
        <Sparkline symbol={stock.symbol} width={320} height={56} days={90} />
        <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>Last 90 sessions</div>
      </div>

      <div style={{ marginBottom: 40 }}>
        <h2 style={{ fontSize: 18, fontWeight: 500, color: "var(--text)", margin: "0 0 4px" }}>Summary</h2>
        <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 16 }}>
          From {rows.length} computed indicator{rows.length === 1 ? "" : "s"} on daily data
        </div>
        <div className="groww-table-wrap" style={{ padding: "20px 24px" }}>
          <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 6 }}>
            On its technicals, this stock reads
          </div>
          <div className={stance.cls} style={{ fontSize: 18, fontWeight: 600 }}>{stance.label}</div>
          <div className="groww-legend" style={{ marginTop: 14, flexDirection: "row", gap: 24 }}>
            <div className="groww-legend-item">
              <span className="groww-legend-dot" style={{ background: "var(--emerald)" }} /> Bullish
              <strong style={{ marginLeft: 8, color: "var(--text)" }}>{bulls}</strong>
            </div>
            <div className="groww-legend-item">
              <span className="groww-legend-dot" style={{ background: "#8e959f" }} /> Neutral
              <strong style={{ marginLeft: 8, color: "var(--text)" }}>{neutrals}</strong>
            </div>
            <div className="groww-legend-item">
              <span className="groww-legend-dot" style={{ background: "var(--rose)" }} /> Bearish
              <strong style={{ marginLeft: 8, color: "var(--text)" }}>{bears}</strong>
            </div>
          </div>
        </div>
      </div>

      <div className="split-grid">
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 500, color: "var(--text)", margin: "0 0 4px" }}>
            Support and resistance
          </h2>
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 16 }}>
            Floor pivots from the {bar?.date || "latest"} bar
          </div>
          <div className="groww-table-wrap">
            {!pivots ? (
              <p className="muted" style={{ padding: 20 }}>Not enough OHLC data to compute pivots.</p>
            ) : (
              <table className="groww-table">
                <tbody>
                  {[
                    ["R3", pivots.r3], ["R2", pivots.r2], ["R1", pivots.r1],
                    ["Pivot", pivots.pivot],
                    ["S1", pivots.s1], ["S2", pivots.s2], ["S3", pivots.s3],
                  ].map(([label, value]) => (
                    <tr key={label}>
                      <td style={{ color: label === "Pivot" ? "var(--text)" : "var(--text-muted)", fontWeight: label === "Pivot" ? 600 : 400 }}>
                        {label}
                      </td>
                      <td className="text-right" style={{ fontWeight: 500 }}>{num(value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        <div>
          <h2 style={{ fontSize: 18, fontWeight: 500, color: "var(--text)", margin: "0 0 4px" }}>Indicators</h2>
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 16 }}>
            {indicator?.date ? `As of ${indicator.date}` : "Latest available"}
          </div>
          <div className="groww-table-wrap">
            {rows.length === 0 ? (
              <p className="muted" style={{ padding: 20 }}>No indicators computed for this stock yet.</p>
            ) : (
              <table className="groww-table">
                <thead>
                  <tr>
                    <th>Indicator</th>
                    <th className="text-right">Value</th>
                    <th className="text-right">Verdict</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.name}>
                      <td style={{ color: "var(--text-muted)" }}>{r.name}</td>
                      <td className="text-right" style={{ fontWeight: 500 }}>{r.value}</td>
                      <td className={`text-right ${TONE_CLASS[r.tone]}`} style={{ fontWeight: 500 }}>{r.verdict}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>

      <div style={{ marginTop: 40 }}>
        <h2 style={{ fontSize: 18, fontWeight: 500, color: "var(--text)", margin: "0 0 4px" }}>Moving averages</h2>
        <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 16 }}>
          Only the 50- and 200-day averages are computed by this app
        </div>
        <div className="groww-table-wrap">
          <table className="groww-table">
            <thead>
              <tr>
                <th>Period</th>
                <th className="text-right">SMA</th>
                <th className="text-right">Price vs SMA</th>
              </tr>
            </thead>
            <tbody>
              {[["50D", indicator?.dma_50], ["200D", indicator?.dma_200]].map(([label, value]) => {
                const above = value != null && close != null && close >= value;
                return (
                  <tr key={label}>
                    <td style={{ color: "var(--text-muted)" }}>{label}</td>
                    <td className="text-right" style={{ fontWeight: 500 }}>{num(value)}</td>
                    <td className={`text-right ${value == null || close == null ? "" : above ? "text-bullish" : "text-danger"}`} style={{ fontWeight: 500 }}>
                      {value == null || close == null ? "—" : `${above ? "+" : ""}${num(((close - value) / value) * 100)}%`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
