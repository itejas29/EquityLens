"""WebSocket connection manager.

Maintains the set of active client connections and broadcasts
price updates to all of them. Stale/disconnected sockets are
removed silently on the next send attempt.
"""

import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._active: set[WebSocket] = set()
        # Which symbol each client currently has open, so the fast-quote tier
        # can prioritise what is actually on someone's screen. Keyed by socket
        # so it cleans itself up on disconnect — there is no TTL to get wrong
        # and no way for a closed tab to keep a symbol in the fast set.
        self._viewing: dict[WebSocket, str] = {}

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._active.add(ws)
        logger.debug("WS connected — %d total", len(self._active))

    def disconnect(self, ws: WebSocket) -> None:
        self._active.discard(ws)
        self._viewing.pop(ws, None)
        logger.debug("WS disconnected — %d remaining", len(self._active))

    def set_viewing(self, ws: WebSocket, symbol: str | None) -> None:
        """Record (or clear) the symbol this client has open."""
        if symbol:
            self._viewing[ws] = symbol.upper()
        else:
            self._viewing.pop(ws, None)

    def viewed_symbols(self) -> list[str]:
        """Distinct symbols currently open across all clients, in insertion
        order. dict.fromkeys rather than set() so the result is stable between
        ticks — an unstable order would reshuffle which symbols survive the cap.
        """
        return list(dict.fromkeys(self._viewing.values()))

    async def broadcast(self, message: str) -> None:
        """Send a text message to every connected client.

        Dead connections are collected and removed after the loop so we
        never modify the set while iterating over it.
        """
        dead: list[WebSocket] = []
        for ws in list(self._active):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._active.discard(ws)
        if dead:
            logger.debug("Removed %d dead WS connection(s)", len(dead))

    @property
    def connection_count(self) -> int:
        return len(self._active)


# Module-level singleton — imported by both the scheduler and the WS route.
ws_manager = ConnectionManager()
