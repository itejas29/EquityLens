export default function ScoreBar({ label, value }) {
  const pct = value == null ? 0 : Math.max(0, Math.min(100, value));
  return (
    <div className="score-bar-row">
      <span className="score-bar-label">{label}</span>
      <span className="score-bar-track">
        <span className="score-bar-fill" style={{ width: `${pct}%`, opacity: value == null ? 0.15 : 1 }} />
      </span>
      <span className="score-bar-value">{value == null ? "—" : value.toFixed(0)}</span>
    </div>
  );
}
