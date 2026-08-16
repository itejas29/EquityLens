/**
 * 1-Day opportunities. Kept deliberately separate from the frozen v1
 * medium-term strategy so the two are never confused.
 *
 * The backend publishes no intraday signals. Every tab below therefore shows
 * an honest empty state instead of approximating breakouts or volume surges
 * from daily bars.
 */
import { useState } from "react";
import { EmptyState, SectionHeader } from "../components/ui/Primitives";

const TABS = ["Breakouts", "Volume surge", "Intraday momentum", "Watch"];

export default function IntradayPage() {
  const [tab, setTab] = useState(TABS[0]);
  return (
    <div className="page">
      <SectionHeader
        title="1-day opportunities"
        sub="Short-term setups — separate from the medium-term EquityLens v1 strategy"
      />
      <div className="chips" role="tablist" aria-label="Opportunity type" style={{ marginBottom: 12 }}>
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t}
            className={`chip ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      <EmptyState
        title="1-day engine coming soon"
        body={`EquityLens v1 is a monthly-rebalanced momentum strategy validated over 16 out-of-sample folds. No intraday engine exists yet, so "${tab}" has no real signals to show — and approximating them from daily bars would be inventing predictions.`}
      />
    </div>
  );
}
