/**
 * Single navigation bar. The previous UI had three stacked bars (primary
 * links, a secondary row, and a ticker tape) which left no room for hierarchy
 * and made the active page ambiguous.
 *
 * Desktop: one row, links + search + profile.
 * Mobile (<=860px): links collapse into a fixed bottom bar with 48px targets.
 */

import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import StockSearch from "./StockSearch";

const LINKS = [
  { to: "/home", label: "Home" },
  { to: "/discover", label: "Discover" },
  { to: "/recommendations", label: "Recommendations" },
  { to: "/today", label: "Today's Picks" },
  { to: "/stocks", label: "Stocks" },
  { to: "/sectors", label: "Sectors" },
  { to: "/paper", label: "Paper Trading" },
  { to: "/ai-trading", label: "AI Trading" },
  { to: "/watchlist", label: "Watchlist" },
  { to: "/portfolio", label: "Portfolio" },
  { to: "/news", label: "News" },
];

// Bottom bar carries the five most-used destinations; the rest stay reachable
// from Home. Five is the practical ceiling for thumb-reachable tabs.
const MOBILE = [
  { to: "/home", label: "Home", d: "M3 11l9-8 9 8v9a2 2 0 0 1-2 2h-4v-6H9v6H5a2 2 0 0 1-2-2z" },
  { to: "/discover", label: "Discover", d: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM15 9l-2 4-4 2 2-4z" },
  { to: "/today", label: "Picks", d: "M3 4h18v16H3zM3 10h18M8 2v4M16 2v4" },
  { to: "/stocks", label: "Stocks", d: "M4 19h16M7 16V9M12 16V5M17 16v-4" },
  { to: "/paper", label: "Paper", d: "M3 7h18v13H3zM8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" },
];

export default function AppNav() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const initial = user?.name ? user.name.trim().charAt(0).toUpperCase() : "?";

  return (
    <>
      <header className="nav">
        <div className="nav-in">
          <NavLink to="/home" className="nav-logo" aria-label="EquityLens home">
            <span className="nav-logo-mark" aria-hidden="true">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6">
                <path d="M3 17 9 11l4 4 8-8" />
              </svg>
            </span>
            EquityLens
          </NavLink>

          <nav className="nav-links" aria-label="Main">
            {LINKS.map((l) => (
              <NavLink key={l.to} to={l.to} className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}>
                {l.label}
              </NavLink>
            ))}
          </nav>

          <div className="nav-right">
            <StockSearch />
            <button
              className="avatar"
              title={user?.email || "Account"}
              onClick={() => { logout(); navigate("/login"); }}
              aria-label="Log out"
            >
              {initial}
            </button>
          </div>
        </div>
      </header>

      <nav className="botnav" aria-label="Primary">
        {MOBILE.map((m) => (
          <NavLink key={m.to} to={m.to} className={({ isActive }) => `botnav-item ${isActive ? "active" : ""}`}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
              <path d={m.d} />
            </svg>
            {m.label}
          </NavLink>
        ))}
      </nav>
    </>
  );
}
