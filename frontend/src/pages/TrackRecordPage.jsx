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

const pctOr = (v) => (v == null ? "—" : pct(v));

const FLAG_TEXT = {
  same_day_data: "built from that day's own close",
  published_after_close: "published after the 15:30 close",
  closed_day: "dated on a day NSE was closed",
};

/* The verdict is computed from the interval, never written by hand: whatever
   the numbers say on the day is what the sentence says. */
function selectionVerdict(u) {
  if (!u || !u.positions) return null;
  const range = `${pct(u.ci95_low)} to ${pct(u.ci95_high)}`;
  const base = `${u.positions} positions opened over ${u.weeks} weeks; 95% interval ${range}.`;
  if (u.excludes_zero && u.mean_excess_pct > 0) {
    return { tone: "up", head: "So far the picks beat a random pick from the same stocks.", body: `${base} Still one market regime — not yet a durable edge.` };
  }
  if (u.excludes_zero && u.mean_excess_pct < 0) {
    return { tone: "down", head: "So far the picks did worse than a random pick from the same stocks.", body: base };
  }
  return { tone: "", head: "No evidence yet that the picks beat a random pick from the same stocks.", body: base };
}

function FollowingTheCalls({ rec }) {
  const clean = rec.clean;
  const h20 = clean.horizons.find((h) => h.horizon_sessions === rec.max_hold_sessions) || {};
  const u = clean.uncertainty[rec.selection_control];
  const verdict = selectionVerdict(u);
  const exits = clean.managed.exits || {};
  const names = { universe_equal_weight: "Universe", nifty200_momentum30: "Momentum 30", nifty50: "NIFTY 50" };
  const order = ["universe_equal_weight", "nifty200_momentum30", "nifty50"];

  return (
    <section style={{ marginBottom: 34 }}>
      <SectionHeader
        title="Following the calls"
        sub={`${clean.positions.opened} positions in ${clean.positions.unique_stocks} stocks · ${rec.clean_days} of ${rec.published_days} days · ${fmtDate(rec.first_call_date)} — ${fmtDate(rec.last_call_date)}`}
      />

      {verdict && (
        <div style={{
          background: "var(--surface)", border: "1px solid var(--line)",
          borderLeft: `3px solid var(--${verdict.tone === "up" ? "up" : verdict.tone === "down" ? "down" : "warn"})`,
          borderRadius: "var(--r)", padding: "12px 16px", marginBottom: 16,
          fontSize: 13, color: "var(--text-2)", lineHeight: 1.6, maxWidth: 760,
        }}>
          <strong style={{ color: "var(--text-1)" }}>{verdict.head}</strong> {verdict.body}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginBottom: 16 }}>
        <Stat label={`${rec.max_hold_sessions}-session return`} value={pctOr(h20.mean_net_return_pct)} tone={tone(h20.mean_net_return_pct)} sub={`after ${rec.round_trip_cost_pct}% costs · n=${h20.sample || 0}`} />
        <Stat label="vs random pick" value={pctOr(u?.mean_excess_pct)} tone={tone(u?.mean_excess_pct)} sub="equal-weight universe, same windows" />
        <Stat label="vs Momentum 30" value={pctOr(h20.vs?.nifty200_momentum30?.mean_excess_pct)} tone={tone(h20.vs?.nifty200_momentum30?.mean_excess_pct)} sub="NIFTY200 Momentum 30 ETF" />
        <Stat label="Exits" value={`${exits.stop || 0} / ${exits.target || 0} / ${exits.time || 0}`} sub={`stop / target / ${rec.max_hold_sessions} sessions · ${clean.managed.still_open} open`} />
      </div>

      <div className="panel">
        <div className="tbl-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>Held</th>
                <th className="r">Positions</th>
                <th className="r">Return</th>
                <th className="r">Win rate</th>
                <th className="r">Profit factor</th>
                {order.map((k) => <th key={k} className="r">vs {names[k]}</th>)}
              </tr>
            </thead>
            <tbody>
              {clean.horizons.map((h) => (
                <tr key={h.horizon_sessions}>
                  <td style={{ fontWeight: 600, color: "var(--text-1)" }}>{h.horizon_sessions} sessions</td>
                  <td className="r num">{h.sample || "—"}</td>
                  <td className={`r num ${tone(h.mean_net_return_pct)}`}>{pctOr(h.mean_net_return_pct)}</td>
                  <td className="r num">{h.win_rate_pct == null ? "—" : `${h.win_rate_pct}%`}</td>
                  <td className="r num">{h.profit_factor == null ? "—" : h.profit_factor.toFixed(2)}</td>
                  {order.map((k) => (
                    <td key={k} className={`r num ${tone(h.vs?.[k]?.mean_excess_pct)}`} style={k === order[0] ? { fontWeight: 700 } : undefined}>
                      {pctOr(h.vs?.[k]?.mean_excess_pct)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <p style={{ fontSize: 12, color: "var(--text-3)", marginTop: 12, maxWidth: 760, lineHeight: 1.7 }}>
        Each call is treated as an order to buy at up to the top of its entry zone when the list
        is published: filled at the open if the stock opens at or below it, at the top of the zone
        if it trades down to it, and counted as missed if it never does. A stock published again
        while already held is the same position, not a new one. Positions exit at the stop or
        target, whichever comes first, or after {rec.max_hold_sessions} sessions; {rec.round_trip_cost_pct}%
        round-trip costs come off every trade. <strong>vs random pick</strong> is the column that
        tests the picking: the same windows, filled the same way, across every stock in the
        universe at equal weight. Beating an index while not beating that basket means the gain came
        from the kind of stocks picked, not from which ones.
      </p>

      {rec.excluded_days.length > 0 && (
        <p style={{ fontSize: 12, color: "var(--text-3)", marginTop: 8, maxWidth: 760, lineHeight: 1.7 }}>
          <strong>Not in these numbers</strong> — {rec.excluded_days.length} days whose list could not
          have been followed as published:{" "}
          {rec.excluded_days.map((d, i) => (
            <span key={d.date}>
              {i > 0 && "; "}
              <span className="num">{fmtDate(d.date)}</span> ({d.flags.map((f) => FLAG_TEXT[f] || f).join(", ")})
            </span>
          ))}
          . They stay in the record and in the table below.
        </p>
      )}
    </section>
  );
}

/* ------------------------------------------------------------- page root -- */

export default function TrackRecordPage() {
  const [data, setData] = useState(null);
  const [realistic, setRealistic] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // The realistic record is fetched alongside, not instead: if it fails, the
    // signal outcomes below still render on their own.
    apiClient.get("/daily-signals/track-record/realistic")
      .then((r) => setRealistic(r.data))
      .catch(() => setRealistic(null));
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
          What actually happened to every signal this app has published. Forward and
          out-of-sample — not a backtest, and not adjustable after the fact. Reported whether or
          not it flatters the strategy.
        </p>
      </div>

      {realistic && realistic.published_calls > 0 && <FollowingTheCalls rec={realistic} />}

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
              title="Signal outcomes"
              sub={`Every published signal, one row each · ${fmtDate(data.first_signal_date)} — ${fmtDate(data.last_signal_date)}`}
            />
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12 }}>
              <Stat label="Signals published" value={data.signals_published} />
              <Stat label="Outcomes measured" value={data.signals_evaluated} />
              <Stat label="Target ever touched" value={data.target_hit} tone="up" sub="per signal, any time" />
              <Stat label="Stop ever touched" value={data.stop_hit} tone="down" sub="per signal, any time" />
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
                      <th className="r">From entry</th>
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
                        <td className={`r num ${tone(h.avg_return_from_entry_pct)}`}>
                          {pct(h.avg_return_from_entry_pct)}
                        </td>
                        <td className="r num" style={{ color: "var(--text-3)" }}>{pct(h.avg_nifty_return_pct)}</td>
                        <td className={`r num ${tone(h.edge_from_entry_pct ?? h.edge_vs_nifty_pct)}`} style={{ fontWeight: 700 }}>
                          {pct(h.edge_from_entry_pct ?? h.edge_vs_nifty_pct)}
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
              <strong>Avg return</strong> is measured from the reference close each signal
              was built on. <strong>From entry</strong> re-bases the identical signals and
              windows onto the top of the published entry zone — the worst fill inside the
              range the call actually told you to buy in, and the only one of the two you
              could have obtained. It is always the lower number.
              {" "}
              <strong>Edge</strong> is the column that matters: return in excess of simply
              holding the index for the same days, measured from entry. It is the average
              of each signal's own difference against NIFTY, not the gap between two
              separately averaged columns — those coincide only while every signal has a
              NIFTY window, and an <span className="num">n=</span> marker appears when one
              does not. A positive average return during a rising market is not
              evidence of skill on its own, and a negative one during a falling market is not
              proof of its absence — which is why the benchmark is measured over each signal's own
              window rather than a fixed period.
              {" "}
              Two limits of this table, which <strong>Following the calls</strong> above does not
              have: a stock published on many days counts once per day, so the sample looks
              larger and more independent than it is; and each stock is measured from the close
              before its signal date while NIFTY is measured from the signal date's close, so the
              stock's window is one session longer.
            </p>
          </section>
        </>
      )}
    </div>
  );
}
