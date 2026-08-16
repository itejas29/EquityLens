import { Link } from "react-router-dom";
import { StockCell, inr } from "./ui/Primitives";

export default function MomentumTable({ rows }) {
  if (!rows?.length) return null;
  return (
    <div className="panel">
      <div className="tbl-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th className="r" style={{ width: 40, color: "var(--text-3)" }}>#</th>
              <th>Stock</th>
              <th className="r">Price</th>
              <th className="r">Momentum Score</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.symbol}>
                <td className="r num" style={{ color: "var(--text-3)" }}>{m.rank}</td>
                <td><StockCell symbol={m.symbol} company={m.company_name} sector={m.sector} /></td>
                <td className="r price num">{m.price ? inr(m.price) : "—"}</td>
                <td className="r num">{m.momentum_percentile.toFixed(1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="cards">
        {rows.map((m) => (
          <Link to={`/stocks/${m.symbol}`} key={m.symbol} className="card-row">
            <div style={{ minWidth: 0, display: "flex", gap: "12px", alignItems: "center" }}>
              <span className="num" style={{ color: "var(--text-3)" }}>{m.rank}</span>
              <div style={{ minWidth: 0 }}>
                <span className="sym">{m.symbol}</span>
                <div className="co">{m.company_name || m.sector || "—"}</div>
              </div>
            </div>
            <div className="card-r">
              <span className="price num">{m.price ? inr(m.price) : "—"}</span>
              <span className="num">{m.momentum_percentile.toFixed(1)}</span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
