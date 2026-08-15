// Placeholder geometry only — never renders numbers, so a loading screen can't be
// mistaken for real data.

export function SkeletonLine({ width = "100%", height = 11 }) {
  return <div className="skeleton skeleton-line" style={{ width, height }} />;
}

export function SkeletonCards({ count = 6 }) {
  return (
    <div className="rec-grid" aria-busy="true" aria-label="Loading results">
      {Array.from({ length: count }, (_, i) => (
        <div className="card skeleton-card" key={i}>
          <div className="skeleton-card-head">
            <div>
              <SkeletonLine width={96} height={15} />
              <div style={{ marginTop: 8 }}>
                <SkeletonLine width={64} height={10} />
              </div>
            </div>
            <SkeletonLine width={104} height={22} />
          </div>
          {Array.from({ length: 5 }, (_, r) => (
            <div className="skeleton-card-row" key={r}>
              <SkeletonLine width={78} height={9} />
              <SkeletonLine height={8} />
              <SkeletonLine width={26} height={9} />
            </div>
          ))}
          <SkeletonLine width="70%" height={9} />
        </div>
      ))}
    </div>
  );
}

export function SkeletonTable({ rows = 6, columns = 6 }) {
  return (
    <div className="card" aria-busy="true" aria-label="Loading table">
      {Array.from({ length: rows }, (_, r) => (
        <div className="skeleton-table-row" key={r} style={{ "--skeleton-cols": columns }}>
          {Array.from({ length: columns }, (_, c) => (
            // First column is the symbol — rendered wider so the shape matches the real table.
            <SkeletonLine key={c} width={c === 0 ? "70%" : "45%"} height={10} />
          ))}
        </div>
      ))}
    </div>
  );
}
