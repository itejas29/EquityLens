/**
 * The 200-day rule as a call, with the evidence it rests on and the case
 * against it.
 *
 * Live inputs come from /daily-signals: the regime close and NIFTY's 200-day
 * average that set today's exposure (services/daily_signals.py `_regime`) —
 * the numbers the strategy acted on, never the live tick, matching MarketStrip.
 *
 * The evidence is Phase 26's measured result
 * (docs/experiments/phase26_crash_brake/results.json): an 18-year backtest of
 * this fixed rule on NIFTY itself, 2008-07 → 2026-10, 0.10% per trade, cash
 * earning nothing. "Mostly from sitting out recoveries" rests on its monthly
 * variant, which switched twice a year and still trailed by 3.1 points, so
 * whipsaws account for under one point of the four.
 */

import { fmtDate } from "./ui/Primitives";

const FINDINGS_URL =
  "https://github.com/itejas29/EquityLens/blob/main/docs/experiments/phase26_crash_brake/FINDINGS.md";

const level = (v) => v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default function RegimeCard({ signals }) {
  if (!signals) return null;
  const close = signals.nifty_close;
  const ma = signals.nifty_200dma;
  const known = close != null && ma != null && signals.regime !== "unknown";
  const below = known && close < ma;
  const distance = known ? ((close - ma) / ma) * 100 : null;
  const exposure = signals.regime_exposure != null ? Math.round(signals.regime_exposure * 100) : null;

  return (
    <div className="regime">
      <div>
        <div className="regime-head">
          <span className="mkt-label">200-day rule</span>
          {known && (
            <span className={`badge ${below ? "badge-wait" : "badge-buy"}`}>{below ? "CAUTION" : "NORMAL"}</span>
          )}
        </div>
        {known ? (
          <>
            <div className="regime-value">
              NIFTY is {Math.abs(distance).toFixed(2)}% {below ? "below" : "above"} its 200-day average
            </div>
            <div className="regime-meta num">
              Close {level(close)} · 200-day average {level(ma)}
              {signals.market_data_through && ` · ${fmtDate(signals.market_data_through)}`}
            </div>
            <p className="regime-call">
              {below
                ? `Picks are sized at ${exposure ?? 25}% while NIFTY closes below its 200-day average (${level(ma)} today). Full size returns after a close above it.`
                : `Picks are at full size. A close below the 200-day average (${level(ma)} today) cuts them to 25%.`}
            </p>
          </>
        ) : (
          <div className="regime-meta">Not enough NIFTY history to compute the 200-day average.</div>
        )}
      </div>

      <div>
        <div className="mkt-label">What this rule has done — NIFTY, 2008–2026</div>
        <ul className="regime-ev">
          <li>Worst fall <strong>−26%</strong> instead of −45%. In the COVID crash, −6% instead of −38%.</li>
          <li>
            It cost about <strong>4 points a year</strong>: 6.0% a year against 10.0% for buy-and-hold, mostly from
            sitting out recoveries. It barely softened the slow declines of 2015–16 and 2024–25.
          </li>
        </ul>
        <p className="regime-foot">
          Insurance against crashes, not a way to beat NIFTY. An 18-year backtest of this fixed rule, at 0.10% per
          trade with cash earning nothing.{" "}
          <a className="section-link" href={FINDINGS_URL} target="_blank" rel="noopener noreferrer">
            Phase 26 findings →
          </a>
        </p>
      </div>
    </div>
  );
}
