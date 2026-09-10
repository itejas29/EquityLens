import { useEffect, useState } from "react";
import { apiClient, apiErrorMessage } from "../api/client";
import { EmptyState, ErrorState, LoadingState, SectionHeader, fmtDate } from "../components/ui/Primitives";

/* ---------------------------------------------------------------- helpers -- */

function Stat({ label, value, tone, sub }) {
  return (
    <div style={{
      background: "var(--surface)", border: "1px solid var(--line)",
      borderRadius: "var(--r-lg)", padding: "14px 18px",
      display: "flex", flexDirection: "column", gap: 4,
    }}>
      <span style={{
        fontSize: 11, fontWeight: 600, letterSpacing: "0.05em",
        textTransform: "uppercase", color: "var(--text-3)",
      }}>{label}</span>
      <span className={`num ${tone || ""}`} style={{ fontSize: 20, fontWeight: 700, lineHeight: 1.2 }}>
        {value}
      </span>
      {sub && <span style={{ fontSize: 11.5, color: "var(--text-3)" }}>{sub}</span>}
    </div>
  );
}

const pct = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
const tone = (v) => (v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "");

/* ------------------------------------------------------------- page root -- */

export default function TrackRecordPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiClient.get("/daily-signals/track-record")
      .then((r) => { setData(r.data); setError(""); })
      .catch((e) => setError(apiErrorMessage(e)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="page"><LoadingState rows={5} /></div>;
  if (error) return <div className="page"><ErrorState message={error} /></div>;
  if (!data) return null;

  const horizons = data.horizons || [];
  const anySufficient = horizons.some((h) => h.sufficient_sample);

  return (
    <div className="page">
      <div style={{ marginBottom: 22 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: "var(--text-1)", marginBottom: 4 }}>
          Track Record
        </h1>
        <p style={{ fontSize: 13, color: "var(--text-3)", margin: 0, maxWidth: 680, lineHeight: 1.6 }}>
          What actually happened to every signal this app has published, measured against NIFTY
          over the same window for each one. Forward and out-of-sample — not a backtest, and not
          adjustable after the fact. Reported whether or not it flatters the strategy.
        </p>
      </div>

      {data.signals_evaluated === 0 ? (
        <EmptyState
          title="No measured outcomes yet"
          body="Outcomes are recorded nightly for every published signal. Numbers appear here once the first ones mature."
        />
      ) : (
        <>
          {!anySufficient && (
            <div style={{
              background: "var(--warn-soft)", border: "1px solid var(--line)",
              borderLeft: "3px solid var(--warn)", borderRadius: "var(--r)",
              padding: "12px 16px", marginBottom: 18, fontSize: 13,
              color: "var(--text-2)", lineHeight: 1.6, maxWidth: 760,
            }}>
              <strong style={{ color: "var(--warn)" }}>Too early to conclude anything.</strong>{" "}
              Every horizon below is under {data.min_sample_for_horizon} measured signals. At this
              size the averages are a handful of stocks wearing a percentage sign — direction here
              is not yet evidence either way.
            </div>
          )}

          <section style={{ marginBottom: 26 }}>
            <SectionHeader
              title="Sample"
              sub={`${fmtDate(data.first_signal_date)} — ${fmtDate(data.last_signal_date)}`}
            />
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12 }}>
              <Stat label="Signals published" value={data.signals_published} />
              <Stat label="Outcomes measured" value={data.signals_evaluated} />
              <Stat label="Target hit" value={data.target_hit} tone="up" />
              <Stat label="Stop hit" value={data.stop_hit} tone="down" />
            </div>
          </section>

          <section>
            <SectionHeader
              title="Performance by horizon"
              sub="Average across all signals, against NIFTY over the identical window"
            />
            <div className="panel">
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Horizon</th>
                      <th className="r">Signals</th>
                      <th className="r">Avg return</th>
                      <th className="r">NIFTY</th>
                      <th className="r">Edge</th>
                      <th className="r">Win rate</th>
                      <th className="r">Beat NIFTY</th>
                    </tr>
                  </thead>
                  <tbody>
                    {horizons.map((h) => (
                      <tr key={h.horizon_days}>
                        <td style={{ fontWeight: 600, color: "var(--text-1)" }}>
                          {h.horizon_days}-day
                          {!h.sufficient_sample && h.sample > 0 && (
                            <span style={{ fontSize: 10.5, color: "var(--warn)", marginLeft: 8 }}>
                              low sample
                            </span>
                          )}
                        </td>
                        <td className="r num">{h.sample || "—"}</td>
                        <td className={`r num ${tone(h.avg_return_pct)}`}>{pct(h.avg_return_pct)}</td>
                        <td className="r num" style={{ color: "var(--text-3)" }}>{pct(h.avg_nifty_return_pct)}</td>
                        <td className={`r num ${tone(h.edge_vs_nifty_pct)}`} style={{ fontWeight: 700 }}>
                          {pct(h.edge_vs_nifty_pct)}
                          {/* The edge is a mean of per-signal differences, so it can
                              only be computed for signals that also have a NIFTY
                              window. Normally that is all of them; when it is not,
                              say so rather than let the count in the Signals column
                              stand for a smaller sample. */}
                          {h.edge_sample != null && h.edge_sample !== h.sample && (
                            <span style={{ fontSize: 10.5, color: "var(--warn)", marginLeft: 6, fontWeight: 500 }}>
                              n={h.edge_sample}
                            </span>
                          )}
                        </td>
                        <td className="r num">{h.win_rate_pct == null ? "—" : `${h.win_rate_pct}%`}</td>
                        <td className="r num">{h.beat_nifty_rate_pct == null ? "—" : `${h.beat_nifty_rate_pct}%`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <p style={{
              fontSize: 12, color: "var(--text-3)", marginTop: 12,
              maxWidth: 760, lineHeight: 1.7,
            }}>
              <strong>Edge</strong> is the column that matters: return in excess of simply holding
              the index for the same days. It is the average of each signal's own
              difference against NIFTY, not the gap between two separately averaged
              columns — those coincide only while every signal has a NIFTY window,
              and an <span className="num">n=</span> marker appears when one does not. A positive average return during a rising market is not
              evidence of skill on its own, and a negative one during a falling market is not
              proof of its absence — which is why the benchmark is measured over each signal's own
              window rather than a fixed period.
            </p>
          </section>
        </>
      )}
    </div>
  );
}
