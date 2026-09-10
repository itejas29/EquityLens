import { useEffect, useState } from "react";
import { apiClient, apiErrorMessage } from "../api/client";
import { ErrorState, LoadingState } from "../components/ui/Primitives";

/* ============================================================================
   Methodology — the due-diligence surface.

   This page is the product. Not the strategy it happens to be pointed at.

   It is written to be read by someone whose job is to find the flaw: every
   figure is either computed live from the production database or transcribed
   from a committed experiment with its directory named. Where the strategy
   under test failed, the failure leads. A page that only showed favourable
   numbers would be evidence of nothing, because that is what every such page
   shows.
   ========================================================================= */

const num = (v, d = 2) => (v == null ? "—" : Number(v).toFixed(d));
const signed = (v, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}`);
const tone = (v, invert = false) => {
  if (v == null) return "";
  const good = invert ? v < 0 : v > 0;
  return good ? "up" : v === 0 ? "" : "down";
};

/* ---------------------------------------------------------------- atoms -- */

function Rule({ label }) {
  return (
    // nowrap on the label was forcing the whole row wider than a phone screen —
    // "the testing engine — 6 programmes run, 4 refuted their own hypothesis" is
    // longer than 390px at any legible size. It wraps now, and the rule that
    // follows it is allowed to collapse to nothing rather than push the document.
    <div style={{
      display: "flex", alignItems: "center", gap: 12, margin: "38px 0 18px", flexWrap: "wrap",
    }}>
      <span style={{
        fontSize: 10.5, fontWeight: 700, letterSpacing: "0.14em",
        textTransform: "uppercase", color: "var(--text-3)", lineHeight: 1.5,
      }}>{label}</span>
      <span style={{ flex: "1 1 24px", minWidth: 0, height: 1, background: "var(--line)" }} />
    </div>
  );
}

function Metric({ label, value, sub, tone: t, mono = true }) {
  return (
    <div style={{
      border: "1px solid var(--line)", borderRadius: "var(--r)",
      background: "var(--surface)", padding: "13px 15px",
      display: "flex", flexDirection: "column", gap: 5, minWidth: 0,
    }}>
      <span style={{
        fontSize: 10, fontWeight: 700, letterSpacing: "0.09em",
        textTransform: "uppercase", color: "var(--text-3)",
      }}>{label}</span>
      <span className={t || ""} style={{
        fontSize: 21, fontWeight: 700, lineHeight: 1.1, color: t ? undefined : "var(--text-1)",
        fontVariantNumeric: mono ? "tabular-nums" : undefined,
      }}>{value}</span>
      {sub && <span style={{ fontSize: 11, color: "var(--text-3)", lineHeight: 1.45 }}>{sub}</span>}
    </div>
  );
}

function Control({ title, body, evidence }) {
  return (
    <div style={{
      border: "1px solid var(--line)", borderLeft: "2px solid var(--accent)",
      borderRadius: "var(--r)", background: "var(--surface)", padding: "14px 16px",
    }}>
      <div style={{ fontSize: 13.5, fontWeight: 650, color: "var(--text-1)", marginBottom: 6 }}>
        {title}
      </div>
      <div style={{ fontSize: 12.5, color: "var(--text-2)", lineHeight: 1.62 }}>{body}</div>
      {evidence && (
        <div style={{
          marginTop: 9, paddingTop: 9, borderTop: "1px dashed var(--line)",
          fontSize: 11.5, color: "var(--text-3)", fontVariantNumeric: "tabular-nums",
        }}>{evidence}</div>
      )}
    </div>
  );
}

function Verdict({ v }) {
  const refuted = v === "REFUTED";
  return (
    <span style={{
      display: "inline-block", padding: "2px 7px", borderRadius: "var(--r-sm)",
      fontSize: 10, fontWeight: 800, letterSpacing: "0.07em",
      background: refuted ? "var(--down-soft)" : "var(--up-soft)",
      color: refuted ? "var(--down)" : "var(--up)",
      border: `1px solid ${refuted ? "var(--down)" : "var(--up)"}33`,
      whiteSpace: "nowrap",
    }}>{v}</span>
  );
}

/* ------------------------------------------------------------------ page -- */

export default function MethodologyPage() {
  const [d, setD] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiClient.get("/methodology")
      .then((r) => { setD(r.data); setError(""); })
      .catch((e) => setError(apiErrorMessage(e)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="page"><LoadingState rows={7} /></div>;
  if (error) return <div className="page"><ErrorState message={error} /></div>;
  if (!d) return null;

  const { strategy, protocol, universe, integrity, ml_gate, live_arm, phases, track_record } = d;
  const horizons = (track_record?.horizons || []).filter((h) => h.sample > 0);
  const refuted = phases.filter((p) => p.verdict === "REFUTED").length;

  return (
    <div className="page" style={{ maxWidth: 1120 }}>
      {/* ---- masthead ---- */}
      <div style={{ borderBottom: "1px solid var(--line-strong)", paddingBottom: 20, marginBottom: 4 }}>
        <div style={{
          fontSize: 10.5, fontWeight: 700, letterSpacing: "0.16em",
          textTransform: "uppercase", color: "var(--accent)", marginBottom: 9,
        }}>
          Methodology &amp; Evidence
        </div>
        <h1 style={{ fontSize: 27, fontWeight: 700, color: "var(--text-1)", margin: "0 0 10px", lineHeight: 1.22 }}>
          A measurement apparatus, evaluated on its own strategy
        </h1>
        <p style={{ fontSize: 13.5, color: "var(--text-2)", margin: 0, maxWidth: 760, lineHeight: 1.68 }}>
          Every figure below is computed live from the production database or transcribed
          from a committed walk-forward experiment with its directory named. Nothing is a
          projection. The strategy currently under test <strong style={{ color: "var(--text-1)" }}>does
          not work</strong>, and this page leads with that — a page showing only favourable
          numbers would be evidence of nothing, because that is what every such page shows.
        </p>
      </div>

      {/* ---- the verdict ---- */}
      <Rule label="Verdict on the strategy under test" />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(178px, 100%), 1fr))", gap: 11 }}>
        <Metric label="Walk-forward return" value={`${num(live_arm.return_pct)}%`}
                sub={`${protocol.folds} out-of-sample folds`} />
        <Metric label="NIFTY 50, same folds" value={`${num(live_arm.benchmark_return_pct)}%`}
                sub="buy and hold" />
        <Metric label="Edge vs benchmark" value={`${signed(live_arm.vs_benchmark_pp)} pp`}
                tone={tone(live_arm.vs_benchmark_pp)} sub="the number that decides it" />
        <Metric label="Folds beating NIFTY" value={`${live_arm.folds_beating_benchmark} / ${live_arm.folds_total}`}
                sub="below half is not an edge" />
      </div>
      <p style={{ fontSize: 12.5, color: "var(--text-2)", lineHeight: 1.68, margin: "14px 0 0", maxWidth: 800 }}>
        The live configuration is the <em>best</em> of seven holding periods tested and still
        trails a buy-and-hold. The conclusion is not that the current setup works — it is
        that every alternative measured is worse.
      </p>

      {/* ---- live forward evidence ---- */}
      <Rule label="Forward evidence — published signals, measured after the fact" />
      <div className="panel">
        <div className="tbl-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>Horizon</th>
                <th className="r">Signals</th>
                <th className="r">From reference</th>
                <th className="r">From entry</th>
                <th className="r">NIFTY</th>
                <th className="r">Edge</th>
                <th className="r">Win rate</th>
              </tr>
            </thead>
            <tbody>
              {horizons.map((h) => (
                <tr key={h.horizon_days}>
                  <td style={{ fontWeight: 650, color: "var(--text-1)" }}>{h.horizon_days}-day</td>
                  <td className="r num">{h.sample}</td>
                  <td className="r num" style={{ color: "var(--text-3)" }}>{signed(h.avg_return_pct)}%</td>
                  <td className={`r num ${tone(h.avg_return_from_entry_pct)}`}>{signed(h.avg_return_from_entry_pct)}%</td>
                  <td className="r num" style={{ color: "var(--text-3)" }}>{signed(h.avg_nifty_return_pct)}%</td>
                  <td className={`r num ${tone(h.edge_from_entry_pct)}`} style={{ fontWeight: 700 }}>
                    {signed(h.edge_from_entry_pct)} pp
                  </td>
                  <td className="r num">{num(h.win_rate_pct, 1)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div style={{ display: "flex", gap: 22, flexWrap: "wrap", marginTop: 12 }}>
        <span style={{ fontSize: 12, color: "var(--text-2)" }}>
          <strong style={{ color: "var(--text-1)" }}>{track_record.signals_published}</strong> signals published
        </span>
        <span style={{ fontSize: 12, color: "var(--text-2)" }}>
          <strong className="up">{track_record.target_hit}</strong> targets hit
        </span>
        <span style={{ fontSize: 12, color: "var(--text-2)" }}>
          <strong className="down">{track_record.stop_hit}</strong> stops hit
        </span>
      </div>
      <p style={{ fontSize: 12.5, color: "var(--text-2)", lineHeight: 1.68, margin: "12px 0 0", maxWidth: 800 }}>
        <strong style={{ color: "var(--text-1)" }}>From entry</strong> is the column that counts.
        The reference column measures from the close each signal was built on — a price no
        buyer can obtain. From entry re-bases the identical signals onto the top of the
        published entry zone, the worst fill inside the range the call actually named. It is
        always the lower number, and it is the only one of the two anyone could have traded.
      </p>

      {/* ---- discipline ---- */}
      <Rule label="Discipline the platform enforces" />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(440px, 100%), 1fr))", gap: 11 }}>
        <Control
          title="The strategy is frozen and versioned"
          body="Parameters live in one module that production signals and research backtests both read. Before that existed, production silently ran a different ranking, stop and regime rule than the one the research had validated — and ranked backwards for it."
          evidence={`${strategy.version} · ${strategy.params.ranking} ranking · ${strategy.params.atr_stop_multiplier}×ATR stop · ${strategy.params.rebalance_frequency} rebalance`}
        />
        <Control
          title="Levels are set before entry and never moved"
          body="Entry zone, stop and target are written once for the date and frozen. A past day's call can be reviewed exactly as it was published, which is what makes the forward record above auditable rather than a claim."
          evidence={`${strategy.params.trend_confirm_days}-day trend gate on entries · max ${strategy.max_concurrent_positions} concurrent positions`}
        />
        <Control
          title="Cadence is enforced, not discretionary"
          body="Rebalance is monthly and gated on a completed run. An earlier version ran the regime trim and entries daily on a monthly-validated strategy; it churned the same names for nine sessions and closed a position at a loss against a stop that never triggered."
          evidence={`${strategy.params.rebalance_frequency} · regime exposure ${strategy.params.bull_exposure} bull / ${strategy.params.bear_exposure} bear on a ${strategy.params.regime_ma_days}-day MA`}
        />
        <Control
          title="Risk checks that cannot run say so"
          body="A held position with no available price, or one priced off the previous close rather than a live quote, is reported by name. An unevaluated stop previously looked identical to one that passed."
          evidence="Every cycle reports unpriced and stale-marked positions"
        />
      </div>

      {/* ---- bias controls ---- */}
      <Rule label="Bias controls, including the ones not yet solved" />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(440px, 100%), 1fr))", gap: 11 }}>
        <Control
          title="Point-in-time data, enforced at the query"
          body="Every rebalance scores against rows dated on or before that day. Indicators are backward-looking rolling computations, so reading them from storage is equivalent to recomputing from a truncated slice."
          evidence={`${protocol.folds} folds · ${protocol.train_months}m train / ${protocol.test_months}m test / ${protocol.roll_months}m roll · ${protocol.window}`}
        />
        <Control
          title="Corporate actions are detected and quarantined"
          body="An unadjusted split is a step in the price level, not a return. A stock carrying one inside the momentum lookback is dropped from the ranking rather than scored — the discriminator is the ratio, because real stocks fall 30% in a day but they do not fall by a factor of exactly 2."
          evidence={`${integrity.large_moves_scanned} large moves scanned · ${integrity.corporate_action_shaped} corporate-action shaped${integrity.detail?.length ? ` · most recent ${integrity.detail[0].symbol} ${integrity.detail[0].date} ×${integrity.detail[0].ratio}` : ""}`}
        />
        <Control
          title="Survivorship: measured, disclosed, not eliminated"
          body="The default backtest universe is the stocks active today, projected backwards — which inflates returns. Delisted names are retained in storage precisely so the exposure can be quantified, and a switch runs the survivorship-free comparison. It is disclosed here because a platform that hid it would be worth less, not more."
          evidence={`${universe.active} active · ${universe.inactive_retained} inactive retained · ${Number(universe.bars).toLocaleString()} bars · ${universe.first_bar} → ${universe.last_bar}`}
        />
        <Control
          title="Costs are charged to the strategy, not to the benchmark"
          body="Every fill pays commission and slippage on both legs. The benchmark does not, which understates the strategy's edge by roughly 0.22pp — stated because the asymmetry runs in the strategy's disfavour and is therefore the kind of thing an honest platform still reports."
          evidence={`${protocol.transaction_cost_pct}% round-trip · ${protocol.slippage_pct}% slippage per fill · ₹${Number(protocol.capital).toLocaleString()} capital`}
        />
      </div>

      {/* ---- refusal to flatter ---- */}
      <Rule label="Where the platform refuses to flatter itself" />
      <div style={{
        border: "1px solid var(--line)", borderLeft: `2px solid var(--warn)`,
        borderRadius: "var(--r)", background: "var(--surface)", padding: "16px 18px",
      }}>
        <div style={{ fontSize: 13.5, fontWeight: 650, color: "var(--text-1)", marginBottom: 7 }}>
          The ML model is trained, measured, and then not served
        </div>
        <p style={{ fontSize: 12.5, color: "var(--text-2)", lineHeight: 1.68, margin: "0 0 12px", maxWidth: 820 }}>
          A secondary model predicts whether a stock will outperform over 20 trading days.
          It is refused at serving time unless its own recorded test ROC-AUC clears the bar.
          It does not clear the bar. Rendering a near-random probability beside a buy call
          would make it look like corroborating evidence when it carries no measured
          information, so the field reads null and the gate is self-enforcing — a future
          retrain that genuinely clears it starts serving with no code change.
        </p>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(160px, 100%), 1fr))", gap: 11 }}>
          <Metric label="Measured test ROC-AUC" value={num(ml_gate.measured_test_roc_auc, 4)}
                  tone="down" sub={ml_gate.selected_model} />
          <Metric label="Serving threshold" value={num(ml_gate.serving_threshold_roc_auc, 2)}
                  sub="below this, nothing is served" />
          <Metric label="Currently serving" value={ml_gate.is_serving ? "YES" : "NO"}
                  tone={ml_gate.is_serving ? "up" : "down"} sub="enforced at request time" />
          <Metric label="Coin flip" value="0.5000" sub="for reference" mono />
        </div>
      </div>

      {/* ---- the testing engine ---- */}
      <Rule label={`The testing engine — ${phases.length} programmes run, ${refuted} refuted their own hypothesis`} />
      <div className="panel">
        <div className="tbl-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th style={{ width: 86 }}>Phase</th>
                <th>Question put to it</th>
                <th style={{ width: 84 }}>Verdict</th>
                <th>What it measured</th>
              </tr>
            </thead>
            <tbody>
              {phases.map((p) => (
                <tr key={p.phase}>
                  <td style={{ fontWeight: 650, color: "var(--text-1)", whiteSpace: "nowrap" }}>{p.phase}</td>
                  <td style={{ color: "var(--text-2)", fontFamily: "var(--font)" }}>{p.question}</td>
                  <td><Verdict v={p.verdict} /></td>
                  <td style={{ color: "var(--text-2)", fontSize: 12.5, lineHeight: 1.6, fontFamily: "var(--font)" }}>
                    {p.detail}
                    <div style={{ fontSize: 10.5, color: "var(--text-3)", marginTop: 5, fontFamily: "var(--font-mono)" }}>
                      {p.source}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <p style={{ fontSize: 12.5, color: "var(--text-2)", lineHeight: 1.68, margin: "14px 0 0", maxWidth: 820 }}>
        Four of six programmes refuted the hypothesis they were built to test, including two
        that removed a feature already shipped. That ratio is the product. An engine that
        only ever confirmed would not be measuring anything.
      </p>

      <div style={{
        marginTop: 30, paddingTop: 16, borderTop: "1px solid var(--line)",
        fontSize: 11.5, color: "var(--text-3)", lineHeight: 1.6,
      }}>
        Computed {d.generated_at} from the production database. Research figures transcribed
        from committed experiment output; each row names its directory. This is a private
        research tool — no real-money execution, and nothing here is investment advice or a
        claim that any return is achievable.
      </div>
    </div>
  );
}
