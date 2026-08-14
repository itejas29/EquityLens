import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import Loading from "./Loading";

export default function ProtectedRoute({ children }) {
  const { isAuthenticated, loading } = useAuth();

  if (loading) return <Loading label="Checking session..." />;
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return children;
}
