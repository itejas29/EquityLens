import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { apiClient, getToken, registerUnauthorizedHandler, setToken } from "../api/client";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadUser = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const res = await apiClient.get("/auth/me");
      setUser(res.data);
    } catch {
      setToken(null);
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    registerUnauthorizedHandler(() => setUser(null));
    loadUser();
  }, [loadUser]);

  async function login(email, password) {
    const res = await apiClient.post("/auth/login", { email, password });
    setToken(res.data.access_token);
    await loadUser();
  }

  async function register(name, email, password, riskProfile) {
    const res = await apiClient.post("/auth/register", {
      name,
      email,
      password,
      risk_profile: riskProfile || null,
    });
    setToken(res.data.access_token);
    await loadUser();
  }

  function logout() {
    setToken(null);
    setUser(null);
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, isAuthenticated: !!user }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
