export default function Loading({ label = "Loading..." }) {
  return (
    <div className="loading">
      <div className="spinner" />
      <span>{label}</span>
    </div>
  );
}
