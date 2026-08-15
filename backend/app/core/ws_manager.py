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

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._active.add(ws)
        logger.debug("WS connected — %d total", len(self._active))

    def disconnect(self, ws: WebSocket) -> None:
        self._active.discard(ws)
        logger.debug("WS disconnected — %d remaining", len(self._active))

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
