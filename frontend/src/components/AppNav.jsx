/**
 * Single navigation bar, five entries.
 *
 * Thirteen top-level links needed ~1,190px and overflowed even a 1440px
 * screen: "AI Trading" was cut in half and Watchlist, Portfolio and News sat
 * off the edge behind a hidden scrollbar. The three most-used pages stay one
 * click away; the other ten sit in two groups that open on click.
 *
 * Each group is the disclosure pattern — a button with aria-expanded followed
 * by ordinary links — not an ARIA menu, which would oblige arrow-key handling
 * that site navigation does not need. Any open panel closes on Escape (focus
 * returns to its button), on a click outside it, when focus leaves it, and
 * when the route changes.
 *
 * Desktop: one row — logo, five entries, search, account menu.
 * Mobile (<=860px): the entries move to a fixed bottom bar of four tabs plus
 * "More", which lists every other page.
 */

import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import StockSearch from "./StockSearch";

const PRIMARY = [
  { to: "/home", label: "Home" },
  { to: "/today", label: "Today's Picks" },
  { to: "/track-record", label: "Track Record" },
];

const GROUPS = [
  {
    id: "research",
    label: "Research",
    links: [
      { to: "/discover", label: "Discover" },
      { to: "/recommendations", label: "Recommendations" },
      { to: "/stocks", label: "Stocks" },
      { to: "/sectors", label: "Sectors" },
      { to: "/news", label: "News" },
      { to: "/methodology", label: "Methodology" },
    ],
  },
  {
    id: "trading",
    label: "Trading",
    links: [
      { to: "/portfolio", label: "Portfolio" },
      { to: "/watchlist", label: "Watchlist" },
      { to: "/paper", label: "Paper Trading" },
      { to: "/ai-trading", label: "AI Trading" },
    ],
  },
];

// Bottom bar: four tabs plus "More". Five is the practical ceiling for
// thumb-reachable tabs; "More" is what keeps the other pages reachable.
const MOBILE = [
  { to: "/home", label: "Home", d: "M3 11l9-8 9 8v9a2 2 0 0 1-2 2h-4v-6H9v6H5a2 2 0 0 1-2-2z" },
  { to: "/discover", label: "Discover", d: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM15 9l-2 4-4 2 2-4z" },
  { to: "/today", label: "Picks", d: "M3 4h18v16H3zM3 10h18M8 2v4M16 2v4" },
  { to: "/stocks", label: "Stocks", d: "M4 19h16M7 16V9M12 16V5M17 16v-4" },
];

// Same rule NavLink uses for its own active state: the page or anything under it.
const onPath = (pathname, to) => pathname === to || pathname.startsWith(`${to}/`);

function Caret() {
  return (
    <svg className="nav-caret" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.6" aria-hidden="true">
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

export default function AppNav() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(null); // "research" | "trading" | "account" | "more" | null
  const triggers = useRef({});
  const initial = user?.name ? user.name.trim().charAt(0).toUpperCase() : "?";

  // Following a link closes whatever was open.
  useEffect(() => { setOpen(null); }, [pathname]);

  useEffect(() => {
    if (!open) return undefined;
    const onPointer = (e) => { if (!e.target.closest?.("[data-nav-popup]")) setOpen(null); };
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      setOpen(null);
      triggers.current[open]?.focus();
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = (id) => setOpen((o) => (o === id ? null : id));
  // Tabbing out of a group closes it, so a keyboard user never leaves a panel
  // hanging open over the page.
  const closeOnLeave = (id) => (e) => {
    if (!e.currentTarget.contains(e.relatedTarget)) setOpen((o) => (o === id ? null : o));
  };
  const moreActive = !MOBILE.some((m) => onPath(pathname, m.to));

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
            {PRIMARY.map((l) => (
              <NavLink key={l.to} to={l.to} className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}>
                {l.label}
              </NavLink>
            ))}
            {GROUPS.map((g) => {
              const active = g.links.some((l) => onPath(pathname, l.to));
              return (
                <div key={g.id} className="nav-group" data-nav-popup onBlur={closeOnLeave(g.id)}>
                  <button
                    type="button"
                    ref={(el) => { triggers.current[g.id] = el; }}
                    className={`nav-link nav-trigger ${active ? "active" : ""}`}
                    aria-expanded={open === g.id}
                    aria-controls={`nav-${g.id}`}
                    onClick={() => toggle(g.id)}
                  >
                    {g.label}
                    <Caret />
                  </button>
                  <div id={`nav-${g.id}`} className="nav-menu" hidden={open !== g.id}>
                    {g.links.map((l) => (
                      <NavLink key={l.to} to={l.to}
                        className={({ isActive }) => `nav-menu-link ${isActive ? "active" : ""}`}>
                        {l.label}
                      </NavLink>
                    ))}
                  </div>
                </div>
              );
            })}
          </nav>

          <div className="nav-right">
            <StockSearch />
            <div className="nav-group" data-nav-popup onBlur={closeOnLeave("account")}>
              <button
                type="button"
                ref={(el) => { triggers.current.account = el; }}
                className="avatar"
                title={user?.email || "Account"}
                aria-label="Account"
                aria-expanded={open === "account"}
                aria-controls="nav-account"
                onClick={() => toggle("account")}
              >
                {initial}
              </button>
              <div id="nav-account" className="nav-menu nav-menu-end" hidden={open !== "account"}>
                <div className="nav-menu-head">
                  {user?.name && <div className="nav-menu-name">{user.name}</div>}
                  {user?.email && <div className="nav-menu-email">{user.email}</div>}
                </div>
                <button type="button" className="nav-menu-link" onClick={() => { logout(); navigate("/login"); }}>
                  Log out
                </button>
              </div>
            </div>
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
        <button
          type="button"
          ref={(el) => { triggers.current.more = el; }}
          className={`botnav-item ${open === "more" || moreActive ? "active" : ""}`}
          aria-expanded={open === "more"}
          aria-controls="nav-more"
          data-nav-popup
          onClick={() => toggle("more")}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
            <path d="M5 12h.01M12 12h.01M19 12h.01" strokeWidth="3" strokeLinecap="round" />
          </svg>
          More
        </button>
      </nav>

      <div id="nav-more" className="nav-sheet" data-nav-popup hidden={open !== "more"}>
        <NavLink to="/track-record" className={({ isActive }) => `nav-menu-link ${isActive ? "active" : ""}`}>
          Track Record
        </NavLink>
        {GROUPS.map((g) => (
          <section key={g.id} aria-label={g.label}>
            <div className="nav-sheet-h">{g.label}</div>
            {g.links.filter((l) => !MOBILE.some((m) => m.to === l.to)).map((l) => (
              <NavLink key={l.to} to={l.to} className={({ isActive }) => `nav-menu-link ${isActive ? "active" : ""}`}>
                {l.label}
              </NavLink>
            ))}
          </section>
        ))}
      </div>
    </>
  );
}
