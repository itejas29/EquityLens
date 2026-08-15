/**
 * useLivePrices — WebSocket hook for real-time price updates.
 *
 * Connects to ws://localhost:8000/api/v1/ws/prices (or the env-configured
 * WS_BASE_URL). On every message from the server the `prices` map is updated.
 *
 * Reconnects automatically with exponential back-off (1s → 2s → 4s … max 30s)
 * so a transient backend restart doesn't break the UI.
 *
 * Returns:
 *   prices: { RELIANCE: { price, change, change_pct, volume, timestamp }, … }
 *   connected: boolean — the socket is up (NOT "the market is open")
 *   status: "live" | "closed" | "stale" — what the prices actually represent.
 *           Outside market hours the server sends the frozen last-session
 *           snapshot with status "closed"; the tape holds at those prices
 *           rather than emptying, and must not present them as moving.
 *   sessionDate / capturedAt: when a "closed" snapshot was taken.
 */

import { useCallback, useEffect, useRef, useState } from "react";

const WS_URL =
  (import.meta.env.VITE_WS_BASE_URL || "ws://localhost:8000/api/v1") +
  "/ws/prices";

const MIN_RETRY_MS = 1_000;
const MAX_RETRY_MS = 30_000;

export function useLivePrices() {
  const [prices, setPrices] = useState({});
  const [connected, setConnected] = useState(false);
  const [meta, setMeta] = useState({ status: "stale", sessionDate: null, capturedAt: null });
  const wsRef = useRef(null);
  const retryMs = useRef(MIN_RETRY_MS);
  const retryTimer = useRef(null);
  const unmounted = useRef(false);

  const connect = useCallback(() => {
    if (unmounted.current) return;
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    try {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        if (unmounted.current) return ws.close();
        setConnected(true);
        retryMs.current = MIN_RETRY_MS; // reset back-off on successful connect
      };

      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          if (msg.type === "prices" && msg.data) {
            setPrices((prev) => ({ ...prev, ...msg.data }));
            setMeta({
              status: msg.status || "live",
              sessionDate: msg.session_date || null,
              capturedAt: msg.captured_at || null,
            });
          }
        } catch {
          // ignore malformed messages
        }
      };

      ws.onclose = () => {
        if (unmounted.current) return;
        setConnected(false);
        wsRef.current = null;
        // Exponential back-off reconnect
        retryTimer.current = setTimeout(() => {
          retryMs.current = Math.min(retryMs.current * 2, MAX_RETRY_MS);
          connect();
        }, retryMs.current);
      };

      ws.onerror = () => {
        ws.close(); // triggers onclose → reconnect
      };
    } catch {
      // WebSocket constructor threw (e.g. invalid URL in test env) — retry later
      retryTimer.current = setTimeout(connect, MAX_RETRY_MS);
    }
  }, []);

  useEffect(() => {
    unmounted.current = false;
    connect();
    return () => {
      unmounted.current = true;
      clearTimeout(retryTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { prices, connected, ...meta };
}
