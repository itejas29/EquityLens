/**
 * useLivePrices — WebSocket hook for live price updates.
 *
 * Connects to ws://localhost:8000/api/v1/ws/prices (or the env-configured
 * WS_BASE_URL). Two tiers arrive on the same socket:
 *
 *   type "prices" — Tier 1, all 500 universe stocks, ~60s cadence.
 *   type "quotes" — Tier 2, the ~10-20 watched symbols, ~10s cadence.
 *
 * They are kept in separate maps and merged on read, with Tier 2 winning where
 * it has a symbol. Merging rather than overwriting matters: a "quotes" message
 * carries only the watched set, so folding it into one map would blank the
 * other ~485 symbols on every fast tick.
 *
 * Reconnects with exponential back-off (1s → 2s → 4s … max 30s) so a transient
 * backend restart doesn't break the UI.
 *
 * @param watchSymbol optional — a Stock Detail symbol to register with the
 *        server so the fast tier includes it. Re-sent on every reconnect.
 *
 * Returns:
 *   prices     — merged map, Tier 2 overlaid on Tier 1.
 *   quotes     — Tier 2 only, for deciding what may be labelled "live".
 *   quotesAt   — ISO timestamp of the last fast-tier fetch, or null.
 *   connected  — the socket is up (NOT "the market is open").
 *   status     — "live" | "closed" | "stale": what Tier 1's prices represent.
 *   sessionDate / capturedAt — when a "closed" snapshot was taken.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const WS_URL =
  (import.meta.env.VITE_WS_BASE_URL || "ws://localhost:8000/api/v1") +
  "/ws/prices";

const MIN_RETRY_MS = 1_000;
const MAX_RETRY_MS = 30_000;

export function useLivePrices(watchSymbol = null) {
  const [tape, setTape] = useState({});
  const [quotes, setQuotes] = useState({});
  const [quotesAt, setQuotesAt] = useState(null);
  const [connected, setConnected] = useState(false);
  const [meta, setMeta] = useState({ status: "stale", sessionDate: null, capturedAt: null });
  const wsRef = useRef(null);
  const retryMs = useRef(MIN_RETRY_MS);
  const retryTimer = useRef(null);
  const unmounted = useRef(false);
  // Held in a ref so `connect` doesn't need it as a dependency — rebuilding
  // connect() on every symbol change would tear down and reopen the socket.
  const watchRef = useRef(watchSymbol);

  const sendWatch = useCallback(() => {
    const ws = wsRef.current;
    if (ws?.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(JSON.stringify({ type: "watch", symbol: watchRef.current }));
    } catch {
      // socket closed between the check and the send — reconnect will re-send
    }
  }, []);

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
        sendWatch(); // server forgets the viewed symbol when the socket drops
      };

      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          if (msg.type === "prices" && msg.data) {
            setTape((prev) => ({ ...prev, ...msg.data }));
            setMeta({
              status: msg.status || "live",
              sessionDate: msg.session_date || null,
              capturedAt: msg.captured_at || null,
            });
          } else if (msg.type === "quotes" && msg.data) {
            setQuotes((prev) => ({ ...prev, ...msg.data }));
            setQuotesAt(msg.fetched_at || null);
          }
        } catch {
          // ignore malformed messages
        }
      };

      ws.onclose = () => {
        if (unmounted.current) return;
        setConnected(false);
        wsRef.current = null;
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
  }, [sendWatch]);

  useEffect(() => {
    unmounted.current = false;
    connect();
    return () => {
      unmounted.current = true;
      clearTimeout(retryTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  useEffect(() => {
    watchRef.current = watchSymbol;
    sendWatch();
  }, [watchSymbol, sendWatch]);

  const prices = useMemo(() => ({ ...tape, ...quotes }), [tape, quotes]);

  return { prices, quotes, quotesAt, connected, ...meta };
}
