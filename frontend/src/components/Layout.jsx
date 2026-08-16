/**
 * App shell. One nav, one content column, one disclaimer.
 * The previous shell stacked a nav, a sub-nav, a ticker tape and a pipeline
 * banner before any content appeared.
 */
import { Outlet } from "react-router-dom";
import AppNav from "./AppNav";
import { useAuth } from "../context/AuthContext";

export default function Layout() {
  const { isAuthenticated } = useAuth();
  return (
    <div className="el">
      {isAuthenticated && <AppNav />}
      <main>
        <Outlet />
      </main>
    </div>
  );
}
