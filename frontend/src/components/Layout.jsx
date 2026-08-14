import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export default function Layout() {
  const { user, logout, isAuthenticated } = useAuth();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate("/login");
  }

  return (
    <div className="app-shell">
      <div className="disclaimer-banner">
        Analysis only — not investment advice. Past performance does not indicate future returns.
      </div>
      <nav className="top-nav">
        <span className="brand">EquityLens</span>
        {isAuthenticated && (
          <>
            <NavLink to="/analyze" className={({ isActive }) => (isActive ? "active" : "")}>
              Analyze
            </NavLink>
            <NavLink to="/recommendations" className={({ isActive }) => (isActive ? "active" : "")}>
              Recommendations
            </NavLink>
            <NavLink to="/backtest" className={({ isActive }) => (isActive ? "active" : "")}>
              Backtest
            </NavLink>
            <NavLink to="/portfolio" className={({ isActive }) => (isActive ? "active" : "")}>
              Portfolio
            </NavLink>
            <NavLink to="/watchlist" className={({ isActive }) => (isActive ? "active" : "")}>
              Watchlist
            </NavLink>
          </>
        )}
        <div className="spacer" />
        {isAuthenticated ? (
          <>
            <span className="user-info">{user?.name}</span>
            <button className="btn" onClick={handleLogout}>
              Log out
            </button>
          </>
        ) : (
          <>
            <NavLink to="/login" className={({ isActive }) => (isActive ? "active" : "")}>
              Log in
            </NavLink>
            <NavLink to="/register" className={({ isActive }) => (isActive ? "active" : "")}>
              Register
            </NavLink>
          </>
        )}
      </nav>
      <div className="page">
        <Outlet />
      </div>
    </div>
  );
}
