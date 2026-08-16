import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext";
import Layout from "./components/Layout";
import ProtectedRoute from "./components/ProtectedRoute";
import LoginPage from "./pages/LoginPage";
import RegisterPage from "./pages/RegisterPage";
import DashboardPage from "./pages/DashboardPage";
import PaperTradingPage from "./pages/PaperTradingPage";
import StocksPage from "./pages/StocksPage";
import SectorsPage from "./pages/SectorsPage";
import IntradayPage from "./pages/IntradayPage";
import NewsPage from "./pages/NewsPage";
import DiscoverPage from "./pages/DiscoverPage";
import HomePage from "./pages/HomePage";
import TodayPage from "./pages/TodayPage";
import AnalyzePage from "./pages/AnalyzePage";
import RecommendationsPage from "./pages/RecommendationsPage";
import StockDetailPage from "./pages/StockDetailPage";
import BacktestPage from "./pages/BacktestPage";
import PortfolioPage from "./pages/PortfolioPage";
import WatchlistPage from "./pages/WatchlistPage";

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          {/* Standalone full-viewport auth pages — no Layout wrapper */}
          <Route path="/" element={<Navigate to="/login" replace />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />

          {/* App shell — sidebar + protected routes */}
          <Route element={<Layout />}>
            <Route path="/home" element={<ProtectedRoute><HomePage /></ProtectedRoute>} />
            <Route path="/discover" element={<ProtectedRoute><DiscoverPage /></ProtectedRoute>} />
            <Route path="/news" element={<ProtectedRoute><NewsPage /></ProtectedRoute>} />
            <Route path="/intraday" element={<ProtectedRoute><IntradayPage /></ProtectedRoute>} />
            <Route path="/stocks" element={<ProtectedRoute><StocksPage /></ProtectedRoute>} />
            <Route path="/sectors" element={<ProtectedRoute><SectorsPage /></ProtectedRoute>} />
            <Route path="/paper" element={<ProtectedRoute><PaperTradingPage /></ProtectedRoute>} />
            <Route
              path="/today"
              element={
                <ProtectedRoute>
                  <TodayPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/dashboard"
              element={
                <ProtectedRoute>
                  <DashboardPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/analyze"
              element={
                <ProtectedRoute>
                  <AnalyzePage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/recommendations"
              element={
                <ProtectedRoute>
                  <RecommendationsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/stocks/:symbol"
              element={
                <ProtectedRoute>
                  <StockDetailPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/backtest"
              element={
                <ProtectedRoute>
                  <BacktestPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/portfolio"
              element={
                <ProtectedRoute>
                  <PortfolioPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/watchlist"
              element={
                <ProtectedRoute>
                  <WatchlistPage />
                </ProtectedRoute>
              }
            />
            <Route path="*" element={<Navigate to="/login" replace />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
