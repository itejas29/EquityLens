"""WebSocket endpoint — streams live price updates to frontend clients.

On connect the client immediately receives the latest cached prices so
it doesn't have to wait up to 60 s for the first scheduler tick.
Subsequent updates arrive via broadcasts from the scheduler job.

The endpoint does NOT require auth — the ticker tape is visible on the
dashboard which is already behind ProtectedRoute on the frontend. Adding
WS auth would require a custom handshake (browsers can't send headers on
WS upgrade) and is overkill for a research tool.
"""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.ws_manager import ws_manager
from app.services.market import get_price_feed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["websocket"])


@router.websocket("/prices")
async def prices_ws(ws: WebSocket) -> None:
    """Stream live price updates.

    Message format (JSON):
        { "type": "prices", "data": { "SYMBOL": { price, change, change_pct, volume, timestamp } } }
    """
    await ws_manager.connect(ws)
    try:
        # Send whatever the feed currently stands behind, immediately on
        # connect. Outside market hours that is the frozen last-session
        # snapshot rather than nothing, so the tape opens holding the prices
        # the market closed at instead of blank. `status` tells the client
        # which it is; it must not animate "closed" prices as live.
        feed = get_price_feed()
        if feed.prices:
            await ws.send_text(
                json.dumps(
                    {
                        "type": "prices",
                        "status": feed.status,
                        "captured_at": feed.captured_at,
                        "session_date": feed.session_date,
                        "data": feed.prices,
                    }
                )
            )

        # Keep the connection alive — actual updates come via broadcast().
        # We read from the socket only to detect disconnects (recv will raise
        # WebSocketDisconnect when the client closes the tab/navigates away).
        while True:
            await ws.receive_text()  # blocks; client never sends, so this just waits

    except WebSocketDisconnect:
        logger.debug("WS client disconnected cleanly")
    except Exception as exc:
        logger.debug("WS error: %s", exc)
    finally:
        ws_manager.disconnect(ws)
