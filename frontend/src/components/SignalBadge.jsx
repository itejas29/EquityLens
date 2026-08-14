export default function SignalBadge({ signal }) {
  if (!signal) return <span className="tag">No signal</span>;
  return <span className={`signal-badge signal-${signal}`}>{signal.replace(/_/g, " ")}</span>;
}
