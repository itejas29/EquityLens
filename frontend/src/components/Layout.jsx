import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import StockSearch from "./StockSearch";
import TickerTape from "./TickerTape";
import PipelineStatus from "./PipelineStatus";

function BrandMark({ size = 30 }) {
  return (
    <div className="top-nav-mark" style={{ width: size, height: size, background: 'var(--accent)', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <svg width={size * 0.55} height={size * 0.55} viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.4">
        <path d="M3 17 9 11l4 4 8-8" />
        <path d="M15 7h6v6" />
      </svg>
    </div>
  );
}

export default function Layout() {
  const { user, logout, isAuthenticated } = useAuth();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate("/login");
  }

  const initial = user?.name ? user.name.trim().charAt(0).toUpperCase() : "?";

  return (
    <div className="app-shell groww-layout">
      {/* Disclaimer Banner */}
      <div className="disclaimer-banner">
        Private tool — picks are generated from historical data and are not guarantees. Past performance does not
        indicate future returns.
      </div>

      {/* Top Navigation */}
      <header className="top-nav">
        <div className="top-nav-main">
          <div className="top-nav-left">
            <span className="top-nav-brand">
              <BrandMark size={28} />
              <span className="top-nav-wordmark">EquityLens</span>
            </span>
            {isAuthenticated && (
              <div className="top-nav-links primary-links">
                {/* Only destinations that exist. The previous "Mutual Funds" and
                    "F&O" entries were inert spans advertising features the app
                    does not have. */}
                <NavLink to="/today" className={({ isActive }) => (isActive ? "active" : "")}>Today</NavLink>
                <NavLink to="/dashboard" className={({ isActive }) => (isActive ? "active" : "")}>Stocks</NavLink>
              </div>
            )}
          </div>

          <div className="top-nav-center">
            {isAuthenticated && <StockSearch />}
          </div>

          <div className="top-nav-right">
            {isAuthenticated ? (
              <>
                <button className="icon-btn" title="Notifications">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"></path>
                    <path d="M13.73 21a2 2 0 0 1-3.46 0"></path>
                  </svg>
                </button>
                <div className="user-dropdown-wrap">
                  <button className="user-avatar-btn">
                    {initial}
                  </button>
                  <div className="user-dropdown">
                    <div className="user-dropdown-header">
                      <strong>{user?.name}</strong>
                      <span className="muted">{user?.email}</span>
                    </div>
                    <button className="dropdown-item text-danger" onClick={handleLogout}>Log out</button>
                  </div>
                </div>
              </>
            ) : (
              <>
                <NavLink to="/login" className="btn btn-ghost">Log in</NavLink>
                <NavLink to="/register" className="btn btn-primary">Register</NavLink>
              </>
            )}
          </div>
        </div>

        {isAuthenticated && (
          <div className="top-nav-sub">
            <div className="top-nav-links secondary-links">
              <NavLink to="/today" className={({ isActive }) => (isActive ? "active" : "")}>Today's picks</NavLink>
              <NavLink to="/dashboard" className={({ isActive }) => (isActive ? "active" : "")}>Explore</NavLink>
              <NavLink to="/portfolio" className={({ isActive }) => (isActive ? "active" : "")}>Holdings</NavLink>
              <NavLink to="/recommendations" className={({ isActive }) => (isActive ? "active" : "")}>Recommendations</NavLink>
              <NavLink to="/watchlist" className={({ isActive }) => (isActive ? "active" : "")}>Watchlist</NavLink>
              <NavLink to="/analyze" className={({ isActive }) => (isActive ? "active" : "")}>Analyze</NavLink>
            </div>
            <div className="top-nav-sub-actions">
              <NavLink to="/backtest" className="btn btn-outline btn-sm">Terminal</NavLink>
            </div>
          </div>
        )}
      </header>

      {/* Ticker Tape */}
      {isAuthenticated && <TickerTape />}

      {/* Pipeline Status Indicator */}
      {isAuthenticated && <PipelineStatus />}

      {/* Main Content */}
      <main className="main-content">
        <div className="page-container">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
