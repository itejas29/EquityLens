import { useEffect, useState } from "react";
import { apiClient } from "../api/client";

/**
 * Deeper analysis for one pick: how far the stock typically travels, how the
 * same setup resolved in its own past, and what a given investment would be
 * worth at the target and at the stop.
 *
 * The expected-move band is dispersion, not a forecast — it says a move of
 * that size would be ordinary for this stock, nothing about direction. The hit
 * rate is history on overlapping windows. Both are labelled as such in the UI
 * rather than only in this comment.
 */

const PRESETS = [10000, 25000, 50000, 100000];

function rupees(v, digits = 2) {
  if (v == null || Number.isNaN(v)) return "—";
  return `₹${v.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function PositionCalculator({ entry, target, stop }) {
  const [amount, setAmount] = useState(25000);

  const shares = amount > 0 && entry > 0 ? Math.floor(amount / entry) : 0;
  const cost = shares * entry;
  const atTarget = shares * target;
  const atStop = shares * stop;
  const profit = atTarget - cost;
  const loss = atStop - cost;
  const idle = amount - cost; // whole shares only, so some capital stays uninvested

  return (
    <div className="calc">
      <div className="calc-head">
        <span className="calc-title">If you invest</span>
        <div className="calc-input-wrap">
          <span className="calc-currency">₹</span>
          <input
            type="number"
            min="0"
            step="1000"
            value={amount}
            onChange={(e) => setAmount(Math.max(0, Number(e.target.value) || 0))}
            aria-label="Amount to invest"
            className="calc-input"
          />
        </div>
      </div>

      <div className="calc-presets">
        {PRESETS.map((p) => (
          <button
            key={p}
            className={`calc-preset ${amount === p ? "active" : ""}`}
            onClick={() => setAmount(p)}
          >
            {p >= 100000 ? `${p / 100000}L` : `${p / 1000}k`}
          </button>
        ))}
      </div>

      {shares === 0 ? (
        <p className="muted" style={{ marginBottom: 0 }}>
          Not enough for a single share at {rupees(entry)}.
        </p>
      ) : (
        <>
          <div className="calc-buy">
            Buys <strong>{shares}</strong> share{shares === 1 ? "" : "s"} at {rupees(entry)} ={" "}
            <strong>{rupees(cost)}</strong>
            {idle >= entry * 0.01 && <span className="muted"> · {rupees(idle)} left over</span>}
          </div>

          <div className="calc-outcomes">
            <div className="calc-outcome calc-outcome--up">
              <div className="calc-outcome-label">If it reaches target</div>
              <div className="calc-outcome-value">{rupees(atTarget)}</div>
              <div className="calc-outcome-delta text-bullish">
                +{rupees(profit)} ({((profit / cost) * 100).toFixed(2)}%)
              </div>
            </div>
            <div className="calc-outcome calc-outcome--down">
              <div className="calc-outcome-label">If it hits the stop</div>
              <div className="calc-outcome-value">{rupees(atStop)}</div>
              <div className="calc-outcome-delta text-danger">
                {rupees(loss)} ({((loss / cost) * 100).toFixed(2)}%)
              </div>
            </div>
          </div>

          <p className="calc-note">
            Assumes the whole position is entered at {rupees(entry)} and exited at one of the two levels.
            Excludes brokerage, STT and slippage.
          </p>
        </>
      )}
    </div>
  );
}

export default function PickAnalysis({ symbol, entry, target, stop, horizonDays = 30 }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    apiClient
      .get(`/analysis/${symbol}/outlook`, { params: { horizon_days: horizonDays, target, stop } })
      .then((res) => !cancelled && setData(res.data))
      .catch(() => !cancelled && setError("Could not load the deeper analysis for this stock."));
    return () => {
      cancelled = true;
    };
  }, [symbol, target, stop, horizonDays]);

  const move = data?.expected_move;
  const hist = data?.historical;
  const edge = hist?.expectancy_pct != null ? hist.expectancy_pct : null;

  return (
    <div className="pick-analysis">
      <div className="pick-analysis-grid">
        <div>
          <h5 className="pick-analysis-h">Expected move · {horizonDays} days</h5>
          {!move ? (
            <p className="muted">{error || "Not enough data to estimate a range."}</p>
          ) : (
            <>
              <div className="pa-range mono">
                {rupees(move.sigma_1_low, 0)} – {rupees(move.sigma_1_high, 0)}
              </div>
              <div className="pa-sub">
                ±{move.sigma_1_pct}% · about two thirds of past {horizonDays}-day moves landed in here
              </div>
              <dl className="pa-facts">
                <div><dt>Annual volatility</dt><dd className="mono">{move.annualised_vol_pct}%</dd></div>
                <div><dt>Typical daily range</dt><dd className="mono">{rupees(move.atr)} ({move.atr_pct}%)</dd></div>
                {move.atr_days_to_target != null && (
                  <div><dt>Target is</dt><dd className="mono">{move.atr_days_to_target} normal days away</dd></div>
                )}
                {move.atr_days_to_stop != null && (
                  <div><dt>Stop is</dt><dd className="mono">{move.atr_days_to_stop} normal days away</dd></div>
                )}
                <div><dt>Wider band (2σ)</dt><dd className="mono">{rupees(move.sigma_2_low, 0)} – {rupees(move.sigma_2_high, 0)}</dd></div>
              </dl>
              <p className="pa-caveat">
                A range, not a direction — it says a move this size would be ordinary for this stock.
              </p>
            </>
          )}
        </div>

        <div>
          <h5 className="pick-analysis-h">This setup in its own past</h5>
          {!hist ? (
            <p className="muted">Not enough history to test this setup.</p>
          ) : (
            <>
              <div className="pa-range mono">
                {hist.hit_rate_pct}% <span className="pa-range-sub">hit target first</span>
              </div>
              <div className="pa-sub">
                {hist.hit_target_first} reached target · {hist.hit_stop_first} hit stop ·{" "}
                {hist.resolved_neither} expired flat, from {hist.samples} entry points
              </div>
              <dl className="pa-facts">
                <div><dt>Breakeven needs</dt><dd className="mono">{hist.breakeven_hit_rate_pct}%</dd></div>
                <div>
                  <dt>Edge per trade</dt>
                  <dd className={`mono ${edge > 0 ? "text-bullish" : edge < 0 ? "text-danger" : ""}`}>
                    {edge > 0 ? "+" : ""}{edge}%
                  </dd>
                </div>
                {hist.avg_days_to_target != null && (
                  <div><dt>Average time to target</dt><dd className="mono">{hist.avg_days_to_target} days</dd></div>
                )}
              </dl>
              {!hist.reliable && (
                <p className="pa-warn">
                  Only {hist.samples} entry points — too few to lean on.
                </p>
              )}
              <p className="pa-caveat">
                Windows overlap, so these samples are not independent. Past resolutions do not
                determine this one.
              </p>
            </>
          )}
        </div>
      </div>

      <PositionCalculator entry={entry} target={target} stop={stop} />
    </div>
  );
}
