/**
 * AI Trading — the dashboard for the automated PAPER account.
 *
 * Top to bottom: status (switch, regime, last/next run), four summary cards,
 * equity against NIFTY, open positions, the decision log, closed trades.
 * Everything shown comes from the /ai-trading endpoints; a figure the API does
 * not have renders as an em dash or an empty state, never as a stand-in.
 *
 * The switch pauses the WHOLE daily cycle — new entries and the stop/target
 * checks alike — which is why turning it off asks for confirmation too.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { apiClient, apiErrorMessage } from "../api/client";
import EquityChart from "../components/EquityChart";
import {
  EmptyState, ErrorState, LoadingState, SectionHeader, inr, pct, fmtDate,
} from "../components/ui/Primitives";

const LOG_ICON = { buy: "🟢", sell: "🔴", skip: "⚪", hold: "⚪", regime: "🛡️", toggle: "⏻" };
const REASONS = ["stop", "target", "horizon", "regime"];
const PAGE = 20;

const tone = (v) => (v == null || v === 0 ? "" : v > 0 ? "up" : "down");
const signedInr = (v) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${inr(Math.abs(v))}`);
const dateTime = (iso) =>
  new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/* ------------------------------------------------------------ status bar -- */

function StatusBar({ status, onToggle, busy, error }) {
  const dialog = useRef(null);
  const next = !status.enabled;
  const r = status.regime;

  return (
    <div className="ai-status">
      <span className="badge badge-wait" title="No broker is connected. Every trade here is simulated.">
        PAPER TRADING — NOT REAL MONEY
      </span>

      <button
        type="button"
        role="switch"
        aria-checked={status.enabled}
        className={`ai-switch ${status.enabled ? "on" : ""}`}
        disabled={busy}
        onClick={() => dialog.current?.showModal()}
      >
        <span className="ai-switch-track" aria-hidden="true"><span className="ai-switch-knob" /></span>
        Auto-trading {status.enabled ? "ON" : "OFF"}
      </button>

      {r.label ? (
        <span className={`badge ${r.label === "CAUTION" ? "badge-wait" : "badge-buy"}`}>
          {r.label}
          {r.distance_pct != null &&
            ` · NIFTY ${Math.abs(r.distance_pct).toFixed(2)}% ${r.distance_pct >= 0 ? "above" : "below"} 200-day avg`}
        </span>
      ) : (
        <span className="badge badge-mute">Regime unavailable</span>
      )}

      <span className="ai-status-meta">
        Last run:{" "}
        {status.last_run
          ? `${fmtDate(status.last_run.run_date)} · ${status.last_run.status} · ${status.last_run.bought_count} bought, ${status.last_run.sold_count} sold`
          : "never"}
        {" · "}
        {status.enabled ? `Next: ${dateTime(status.next_run_at)} (if the market is open)` : "Next: paused"}
      </span>
      {error && <span className="down" style={{ fontSize: 12.5 }}>{error}</span>}

      <dialog ref={dialog} className="ai-dialog">
        <h3>{next ? "Enable automated paper trading?" : "Pause automated paper trading?"}</h3>
        <p>
          {next
            ? "This will execute trades automatically based on daily signals. NO REAL MONEY is involved."
            : "The daily cycle stops completely: no new entries, and no stop-loss or target checks on the positions already open, until you switch it back on. NO REAL MONEY is involved."}
        </p>
        <div className="ai-dialog-actions">
          <button type="button" className="btn" onClick={() => dialog.current?.close()}>Cancel</button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => { dialog.current?.close(); onToggle(next); }}
          >
            {next ? "Enable" : "Pause"}
          </button>
        </div>
      </dialog>
    </div>
  );
}

/* ----------------------------------------------------------------- cards -- */

function MiniCurve({ points }) {
  if (!points || points.length < 2) return null;
  const w = 120, h = 30;
  const min = Math.min(...points), max = Math.max(...points);
  const span = max - min || 1;
  const path = points
    .map((v, i) => `${i ? "L" : "M"}${((i / (points.length - 1)) * w).toFixed(1)},${(h - ((v - min) / span) * h).toFixed(1)}`)
    .join(" ");
  const up = points[points.length - 1] >= points[0];
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img"
      aria-label={`Equity trend: ${up ? "up" : "down"} over ${points.length} sessions`}>
      <path d={path} fill="none" stroke={up ? "var(--up)" : "var(--down)"} strokeWidth="1.6" />
    </svg>
  );
}

function Card({ label, value, sub, valueTone, children }) {
  return (
    <div className="ai-card">
      <div className="mkt-label">{label}</div>
      <div className={`ai-card-value num ${valueTone || ""}`}>{value}</div>
      {sub && <div className="ai-card-sub">{sub}</div>}
      {children}
    </div>
  );
}

function SummaryCards({ acct, curve, status }) {
  const last = curve?.length ? curve[curve.length - 1] : null;
  const prev = curve?.length > 1 ? curve[curve.length - 2] : null;
  // The last recorded session's change. Snapshots are end-of-day, so before
  // tonight's 20:00 update this is the previous session's — labelled by date.
  const dayPnl = last && prev ? last.total_equity - prev.total_equity : null;
  const alpha = last?.cumulative_return != null && last?.nifty_return != null
    ? last.cumulative_return - last.nifty_return : null;

  return (
    <div className="ai-cards">
      <Card label="Total equity" value={inr(acct.equity)} sub={`Started with ${inr(acct.virtual_capital, 0)}`}>
        <MiniCurve points={curve?.map((c) => c.total_equity)} />
      </Card>
      <Card
        label="Last session's P&L"
        value={signedInr(dayPnl)}
        valueTone={tone(dayPnl)}
        sub={last && dayPnl != null ? `${pct(last.daily_return)} · ${fmtDate(last.date)}` : "Needs two recorded sessions"}
      />
      <Card
        label="vs NIFTY since inception"
        value={alpha == null ? "—" : `${pct(alpha)}`}
        valueTone={tone(alpha)}
        sub={alpha == null
          ? "No recorded session yet"
          : `Portfolio ${pct(last.cumulative_return)} · NIFTY ${pct(last.nifty_return)} since ${fmtDate(curve[0].date)}`}
      />
      <Card
        label="Positions"
        value={`${status.open_positions} / ${status.max_positions}`}
        sub={`${inr(acct.cash, 0)} cash`}
      />
    </div>
  );
}

/* ------------------------------------------------------------- positions -- */

function Positions({ rows }) {
  const [open, setOpen] = useState(null);
  const sorted = useMemo(
    () => [...rows].sort((a, b) => (b.unrealized_pnl_pct ?? -Infinity) - (a.unrealized_pnl_pct ?? -Infinity)),
    [rows],
  );
  if (!rows.length) {
    return (
      <EmptyState
        title="No open positions"
        body="The account enters on the month's rebalance day, and only stocks from that day's shortlist trading inside their entry zone on a live quote."
      />
    );
  }
  const rowTone = (p) => (p.unrealized_pnl > 0 ? "ai-row-up" : p.unrealized_pnl < 0 ? "ai-row-down" : "");
  const dist = (v) => (v == null ? "—" : `${v.toFixed(1)}%`);

  return (
    <div className="panel">
      <div className="tbl-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>Symbol</th><th>Entered</th><th className="r">Entry</th><th className="r">Current</th>
              <th className="r">P&amp;L (₹)</th><th className="r">P&amp;L (%)</th><th className="r">Days held</th>
              <th className="r">Above stop</th><th className="r">To target</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((p) => (
              <tr key={p.symbol} className={rowTone(p)}>
                <td><Link to={`/stocks/${p.symbol}`} className="sym">{p.symbol}</Link></td>
                <td>{fmtDate(p.entry_date)}</td>
                <td className="r">{inr(p.entry_price)}</td>
                <td className="r">{inr(p.current_price)}</td>
                <td className={`r ${tone(p.unrealized_pnl)}`}>{signedInr(p.unrealized_pnl)}</td>
                <td className={`r ${tone(p.unrealized_pnl_pct)}`}>{pct(p.unrealized_pnl_pct)}</td>
                <td className="r">{p.days_held}</td>
                <td className="r">{dist(p.stop_distance_pct)}</td>
                <td className="r">{dist(p.target_distance_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Phones: three fields per row; the rest opens on tap. */}
      <div className="cards">
        {sorted.map((p) => (
          <div key={p.symbol} className={`ai-pos ${rowTone(p)}`}>
            <button
              type="button" className="ai-pos-head" aria-expanded={open === p.symbol}
              onClick={() => setOpen(open === p.symbol ? null : p.symbol)}
            >
              <span className="sym">{p.symbol}</span>
              <span className={`num ${tone(p.unrealized_pnl_pct)}`}>{pct(p.unrealized_pnl_pct)}</span>
              <span className="num ai-pos-days">{p.days_held}d</span>
            </button>
            {open === p.symbol && (
              <dl className="ai-pos-more">
                <dt>Entered</dt><dd>{fmtDate(p.entry_date)} at {inr(p.entry_price)}</dd>
                <dt>Current</dt><dd>{inr(p.current_price)}</dd>
                <dt>P&amp;L</dt><dd className={tone(p.unrealized_pnl)}>{signedInr(p.unrealized_pnl)}</dd>
                <dt>Above stop</dt><dd>{dist(p.stop_distance_pct)}</dd>
                <dt>To target</dt><dd>{dist(p.target_distance_pct)}</dd>
                <dt>Stock</dt><dd><Link to={`/stocks/${p.symbol}`} className="section-link">Open {p.symbol} →</Link></dd>
              </dl>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ----------------------------------------------------------- pagination -- */

function Pager({ offset, total, onChange }) {
  if (total <= PAGE) return null;
  return (
    <div className="ai-pager">
      <button type="button" className="btn btn-sm" disabled={offset === 0} onClick={() => onChange(offset - PAGE)}>
        Newer
      </button>
      <span className="num">{offset + 1}–{Math.min(offset + PAGE, total)} of {total}</span>
      <button type="button" className="btn btn-sm" disabled={offset + PAGE >= total} onClick={() => onChange(offset + PAGE)}>
        Older
      </button>
    </div>
  );
}

/* ---------------------------------------------------------- activity feed -- */

function Activity({ refreshKey }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [all, setAll] = useState(false);
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    apiClient.get("/ai-trading/log", { params: { limit: PAGE, offset: all ? offset : 0 } })
      .then((r) => { setData(r.data); setError(""); })
      .catch((e) => setError(apiErrorMessage(e)));
  }, [all, offset, refreshKey]);

  if (error) return <ErrorState message={error} />;
  if (!data) return <LoadingState rows={4} />;
  if (!data.total) {
    return (
      <EmptyState
        title="No decisions logged yet"
        body="Each daily cycle records what it bought, sold, held and skipped, with the reason. Entries appear after the next cycle runs."
      />
    );
  }

  return (
    <div className="panel">
      <ul className="ai-feed">
        {data.entries.map((e) => (
          <li key={e.id}>
            <span className="ai-feed-icon" aria-hidden="true">{LOG_ICON[e.action] || "•"}</span>
            <div>
              <div className="ai-feed-line">
                <strong>{e.action.toUpperCase()}</strong>
                {e.symbol && <Link to={`/stocks/${e.symbol}`} className="sym">{e.symbol}</Link>}
                {e.quantity != null && e.price != null && e.action !== "hold" && (
                  <span className="num">× {e.quantity} @ {inr(e.price)}</span>
                )}
              </div>
              <div className="ai-feed-reason">{e.reason}</div>
            </div>
            <time className="ai-feed-time" dateTime={e.created_at}>{dateTime(e.created_at)}</time>
          </li>
        ))}
      </ul>
      {all ? (
        <Pager offset={offset} total={data.total} onChange={setOffset} />
      ) : data.total > PAGE && (
        <div className="ai-pager">
          <button type="button" className="btn btn-sm" onClick={() => setAll(true)}>View all {data.total} →</button>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ trade history -- */

function History({ refreshKey }) {
  const [reason, setReason] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiClient.get("/ai-trading/history", { params: { limit: PAGE, offset, ...(reason ? { exit_reason: reason } : {}) } })
      .then((r) => { setData(r.data); setError(""); })
      .catch((e) => setError(apiErrorMessage(e)));
  }, [reason, offset, refreshKey]);

  if (error) return <ErrorState message={error} />;
  if (!data) return <LoadingState rows={4} />;
  const s = data.stats;
  const stat = (label, value) => (
    <div><div className="mkt-label">{label}</div><div className="num ai-stat">{value}</div></div>
  );

  return (
    <>
      <div className="ai-filter" role="group" aria-label="Filter by exit reason">
        {["", ...REASONS].map((r) => (
          <button
            key={r || "all"} type="button" className={`chip ${reason === r ? "on" : ""}`}
            aria-pressed={reason === r} onClick={() => { setReason(r); setOffset(0); }}
          >
            {r ? r[0].toUpperCase() + r.slice(1) : "All"}
          </button>
        ))}
      </div>

      {!data.total ? (
        <EmptyState
          title={reason ? `No trades closed by ${reason}` : "No closed trades yet"}
          body="A trade appears here when the account sells it: at its stop, its target, the 90-day horizon, or a regime trim."
        />
      ) : (
        <div className="panel">
          <div className="ai-stats">
            {stat("Trades", s.total_trades)}
            {stat("Win rate", s.win_rate_pct == null ? "—" : `${s.win_rate_pct}%`)}
            {stat("Avg win", pct(s.avg_win_pct))}
            {stat("Avg loss", pct(s.avg_loss_pct))}
            {stat("Profit factor", s.profit_factor == null ? "—" : s.profit_factor.toFixed(2))}
            {stat("Avg holding", s.avg_holding_days == null ? "—" : `${s.avg_holding_days} days`)}
          </div>
          <div className="ai-scroll">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Symbol</th><th>Entered</th><th>Exited</th><th className="r">Entry</th><th className="r">Exit</th>
                  <th className="r">P&amp;L (₹)</th><th className="r">P&amp;L (%)</th><th className="r">Days</th><th>Exit reason</th>
                </tr>
              </thead>
              <tbody>
                {data.trades.map((t, i) => (
                  <tr key={`${t.symbol}-${t.exit_date}-${i}`}>
                    <td><Link to={`/stocks/${t.symbol}`} className="sym">{t.symbol}</Link></td>
                    <td>{fmtDate(t.entry_date)}</td>
                    <td>{fmtDate(t.exit_date)}</td>
                    <td className="r">{inr(t.entry_price)}</td>
                    <td className="r">{inr(t.exit_price)}</td>
                    <td className={`r ${tone(t.pnl)}`}>{signedInr(t.pnl)}</td>
                    <td className={`r ${tone(t.pnl_pct)}`}>{pct(t.pnl_pct)}</td>
                    <td className="r">{t.holding_days}</td>
                    <td>{t.exit_reason || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager offset={offset} total={data.total} onChange={setOffset} />
        </div>
      )}
    </>
  );
}

/* ------------------------------------------------------------- page root -- */

export default function AITradingPage() {
  const [status, setStatus] = useState(null);
  const [acct, setAcct] = useState(null);
  const [positions, setPositions] = useState(null);
  const [curve, setCurve] = useState(null);
  const [error, setError] = useState("");
  const [toggleError, setToggleError] = useState("");
  const [busy, setBusy] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  function load() {
    Promise.allSettled([
      apiClient.get("/ai-trading/status"),
      apiClient.get("/ai-trading/account"),
      apiClient.get("/ai-trading/positions"),
      apiClient.get("/ai-trading/equity-curve"),
    ]).then(([s, a, p, c]) => {
      if (s.status === "fulfilled" && a.status === "fulfilled") {
        setStatus(s.value.data);
        setAcct(a.value.data);
        setError("");
      } else {
        setError(apiErrorMessage((s.status === "rejected" ? s : a).reason));
      }
      if (p.status === "fulfilled") setPositions(p.value.data);
      if (c.status === "fulfilled") setCurve(c.value.data);
      setRefreshKey((k) => k + 1);
    });
  }

  useEffect(() => {
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  function toggle(enabled) {
    setBusy(true);
    apiClient.post("/ai-trading/toggle", { enabled })
      .then((r) => { setStatus(r.data); setToggleError(""); setRefreshKey((k) => k + 1); })
      .catch((e) => setToggleError(apiErrorMessage(e)))
      .finally(() => setBusy(false));
  }

  if (error && !status) return <div className="page"><ErrorState message={error} onRetry={load} /></div>;
  if (!status || !acct) return <div className="page"><LoadingState rows={8} /></div>;

  return (
    <div className="page">
      <div style={{ marginBottom: 14 }}>
        <h1>AI Trading</h1>
        <p className="section-sub" style={{ maxWidth: 680 }}>
          A virtual account run by the V1 momentum rules with no manual clicks. On the month&apos;s rebalance day it
          enters shortlisted stocks trading inside their entry zone, and every day it checks each stop and target.
          V1 has not beaten NIFTY 50 in testing. Research tool only — not investment advice.
        </p>
      </div>

      <StatusBar status={status} onToggle={toggle} busy={busy} error={toggleError} />

      <section className="section">
        <SummaryCards acct={acct} curve={curve} status={status} />
      </section>

      <section className="section">
        <SectionHeader title="Equity vs NIFTY 50" sub="End-of-day snapshots since the account opened" />
        {curve == null ? <LoadingState rows={4} /> : <EquityChart data={curve} virtualCapital={acct.virtual_capital} />}
      </section>

      <section className="section">
        <SectionHeader title="Open positions" sub="Sorted by unrealised P&L %" />
        {positions == null ? <LoadingState rows={3} /> : <Positions rows={positions} />}
      </section>

      <section className="section">
        <SectionHeader title="Activity" sub="Every decision the daily cycle took, newest first" />
        <Activity refreshKey={refreshKey} />
      </section>

      <section className="section">
        <SectionHeader title="Closed trades" />
        <History refreshKey={refreshKey} />
      </section>
    </div>
  );
}
