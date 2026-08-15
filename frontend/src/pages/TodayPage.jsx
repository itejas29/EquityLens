import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiClient, apiErrorMessage } from "../api/client";
import EmptyState from "../components/EmptyState";
import ErrorMessage from "../components/ErrorMessage";
import MarketOverview from "../components/MarketOverview";
import PageHeader from "../components/PageHeader";
import PickAnalysis from "../components/PickAnalysis";
import { SkeletonTable } from "../components/Skeleton";

// The call is derived from where the live price sits against the frozen zone,
// so it can only ever say something the levels actually support. ABOVE_ZONE is
// deliberately a WAIT rather than a BUY: past the entry range the stop is
// further away in percentage terms, which breaks the 1:2 the target was set for.
const CALL = {
  IN_ZONE: {
    action: "BUY NOW",
    tone: "in",
    hint: (s) => `Trading inside the buy range. Enter up to ₹${s.entry_high.toLocaleString("en-IN")}.`,
  },
  BELOW_ZONE: {
    action: "BUY",
    tone: "in",
    hint: (s) =>
      `Cheaper than the buy range — a better entry, but it sits closer to the ₹${s.stop_loss.toLocaleString("en-IN")} stop, so size smaller.`,
  },
  ABOVE_ZONE: {
    action: "WAIT",
    tone: "above",
    hint: (s) =>
      `Already run past the buy range. Wait for a pullback to ₹${s.entry_high.toLocaleString("en-IN")} — chasing it here breaks the 1:2 risk:reward.`,
  },
  UNKNOWN: {
    action: "WAIT",
    tone: "unknown",
    hint: () => "No live price available, so there is nothing to check the buy range against yet.",
  },
};

function rupees(value) {
  if (value == null) return "—";
  return `₹${value.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function LevelBar({ stop, entryLow, entryHigh, target, price }) {
  // Single continuous axis from stop to target so the distances are shown to
  // scale — a 1:2 setup should visibly look like 1:2, not two equal halves.
  const span = target - stop;
  if (!(span > 0)) return null;
  const at = (v) => `${Math.min(100, Math.max(0, ((v - stop) / span) * 100))}%`;

  return (
    <div className="level-bar">
      <div className="level-bar-track">
        <span className="level-bar-risk" style={{ left: at(stop), width: `calc(${at(entryLow)} - ${at(stop)})` }} />
        <span className="level-bar-zone" style={{ left: at(entryLow), width: `calc(${at(entryHigh)} - ${at(entryLow)})` }} />
        <span className="level-bar-reward" style={{ left: at(entryHigh), width: `calc(${at(target)} - ${at(entryHigh)})` }} />
        {price != null && <span className="level-bar-marker" style={{ left: at(price) }} title={`Now ${rupees(price)}`} />}
      </div>
      <div className="level-bar-ends">
        <span>Stop {rupees(stop)}</span>
        <span>Target {rupees(target)}</span>
      </div>
    </div>
  );
}

function SignalCard({ signal }) {
  const call = CALL[signal.trigger_state] || CALL.UNKNOWN;
  const supports = signal.rationale.filter((r) => r.kind === "support");
  const cautions = signal.rationale.filter((r) => r.kind === "caution");
  // Collapsed by default, and only fetched when opened — eight cards each
  // firing a historical backtest on mount would be wasteful when most are
  // never expanded.
  const [open, setOpen] = useState(false);

  return (
    <article className="signal-card">
      <header className="signal-card-head">
        <div>
          <div className="signal-card-title">
            <span className="signal-rank">{signal.rank}</span>
            <Link to={`/stocks/${signal.symbol}`} className="signal-symbol">
              {signal.symbol}
            </Link>
          </div>
          <p className="signal-company">
            {signal.company_name || signal.symbol}
            {signal.sector ? ` · ${signal.sector}` : ""}
          </p>
        </div>
        <div className="signal-card-head-right">
          <span className={`call-pill call-pill--${call.tone}`}>{call.action}</span>
          <span className="signal-score" title="Composite score out of 100">
            {signal.overall_score.toFixed(0)}<span className="signal-score-max">/100</span>
          </span>
        </div>
      </header>

      <div className="signal-levels">
        <div className="signal-level signal-level--primary">
          <span className="signal-level-label">Buy between</span>
          <span className="signal-level-value mono">
            {rupees(signal.entry_low)} – {rupees(signal.entry_high)}
          </span>
        </div>
        <div className="signal-level">
          <span className="signal-level-label">Sell if it hits</span>
          <span className="signal-level-value mono down">
            {rupees(signal.stop_loss)} <em>{signal.downside_pct.toFixed(1)}%</em>
          </span>
        </div>
        <div className="signal-level">
          <span className="signal-level-label">Target</span>
          <span className="signal-level-value mono up">
            {rupees(signal.target_price)} <em>+{signal.upside_pct.toFixed(1)}%</em>
          </span>
        </div>
        <div className="signal-level">
          <span className="signal-level-label">Now</span>
          <span className="signal-level-value mono">{rupees(signal.latest_price)}</span>
        </div>
      </div>

      <LevelBar
        stop={signal.stop_loss}
        entryLow={signal.entry_low}
        entryHigh={signal.entry_high}
        target={signal.target_price}
        price={signal.latest_price}
      />

      <p className="signal-state-hint">{call.hint(signal)}</p>

      <div className="signal-why">
        <h4>Why we are buying it</h4>
        <ul>
          {supports.map((r, i) => (
            <li key={`s${i}`} className="why-support">
              <span className="why-label">{r.label}</span>
              {r.text}
            </li>
          ))}
        </ul>
        {cautions.length > 0 && (
          <>
            <h4 className="signal-why-caution">What argues against it</h4>
            <ul>
              {cautions.map((r, i) => (
                <li key={`c${i}`} className="why-caution">
                  <span className="why-label">{r.label}</span>
                  {r.text}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      <button
        className="pick-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={`analysis-${signal.symbol}`}
      >
        {open ? "Hide" : "Expected move, historical odds & position calculator"}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
             style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform .18s" }}>
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div id={`analysis-${signal.symbol}`}>
          <PickAnalysis
            symbol={signal.symbol}
            entry={signal.entry_high}
            target={signal.target_price}
            stop={signal.stop_loss}
          />
        </div>
      )}
    </article>
  );
}

export default function TodayPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .get("/daily-signals")
      .then((res) => !cancelled && setData(res.data))
      .catch((err) => !cancelled && setError(apiErrorMessage(err)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div>
        <PageHeader title="Today" subtitle="Loading this morning's shortlist..." />
        <SkeletonTable rows={3} columns={4} />
      </div>
    );
  }

  const published = data?.date
    ? new Date(`${data.date}T00:00:00`).toLocaleDateString("en-IN", {
        weekday: "long",
        day: "numeric",
        month: "long",
      })
    : null;

  return (
    <div className="today-page">
      <PageHeader
        title="Today's picks"
        subtitle={
          published
            ? `Published ${published}, priced off the previous session's close.`
            : "Nothing published yet."
        }
      />

      <ErrorMessage message={error} />

      <div style={{ marginBottom: 20 }}>
        <MarketOverview />
      </div>

      {!data || data.count === 0 ? (
        <EmptyState
          title="No picks yet"
          description="Picks are generated automatically at 9:15 AM IST on trading days. Once the job runs, whatever passed the screen shows up here."
        />
      ) : (
        <>
          <p className="today-lede">
            <strong>{data.count}</strong> picks out of {data.universe_size} stocks screened. Buy inside the stated
            range, sell if it hits the stop, book at the target. Each pick lists the case for it and the case against
            it — read both before you act.
          </p>

          <div className="signal-list">
            {data.signals.map((signal) => (
              <SignalCard key={signal.stock_id} signal={signal} />
            ))}
          </div>

          <p className="today-footnote">
            Levels come from a 14-day ATR against the {data.signals[0]?.reference_date} close and are fixed for the
            day. Stops take whichever is tighter of the ATR stop or the 20-day low; targets sit at twice the distance
            to that stop. Only stocks with complete price and risk data are picked —{" "}
            <Link to="/recommendations">the full scored universe</Link> shows everything, including partial scores.
            These picks come from a rule-based score on historical data; in the one period backtested, the strategy
            returned less than simply holding the NIFTY 50. Size positions accordingly.
          </p>
        </>
      )}
    </div>
  );
}
