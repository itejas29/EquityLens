/**
 * Reusable movers table used by Home and Discover.
 * Renders only fields present on MoverResponse; no derived or filled values.
 */
import { Link } from "react-router-dom";
import Sparkline from "./Sparkline";
import { Change, StockCell, compactNum, inr } from "./ui/Primitives";

export default function MoversTable({ rows, showTurnover = false, showSpark = true }) {
  if (!rows?.length) return null;
  return (
    <div className="panel">
      <div className="tbl-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>Stock</th>
              {showSpark && <th className="col-hide-lg" style={{ width: 130 }}>
                <span className="visually-hidden">30-day trend</span>
              </th>}
              <th className="r">Price</th>
              <th className="r">Change</th>
              <th className="r col-hide-lg">{showTurnover ? "Turnover" : "Volume"}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.symbol}>
                <td><StockCell symbol={m.symbol} company={m.company_name} sector={m.sector} /></td>
                {showSpark && (
                  <td className="col-hide-lg"><Sparkline symbol={m.symbol} width={110} height={26} /></td>
                )}
                <td className="r price num">{inr(m.close)}</td>
                <td className="r"><Change value={m.change_pct} absolute={m.change} /></td>
                <td className="r num col-hide-lg" style={{ color: "var(--text-3)" }}>
                  {showTurnover
                    ? (m.traded_value != null ? `₹${(m.traded_value / 1e7).toFixed(1)} Cr` : "—")
                    : compactNum(m.volume)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="cards">
        {rows.map((m) => (
          <Link to={`/stocks/${m.symbol}`} key={m.symbol} className="card-row">
            <div style={{ minWidth: 0 }}>
              <span className="sym">{m.symbol}</span>
              <div className="co">{m.company_name || m.sector || "—"}</div>
            </div>
            <div className="card-r">
              <span className="price num">{inr(m.close)}</span>
              <Change value={m.change_pct} />
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
