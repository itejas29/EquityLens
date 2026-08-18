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

from app.core.cache import get_fast_quotes
from app.core.market_hours import is_market_hours
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

        # Fast-tier quotes for the small watched set, if the loop has any. Sent
        # separately from the tape so the client can label them with their own
        # freshness rather than inheriting the 60s tape's.
        fast = get_fast_quotes()
        if fast and fast.get("quotes"):
            await ws.send_text(
                json.dumps(
                    {
                        "type": "quotes",
                        "status": "live" if is_market_hours() else "closed",
                        "fetched_at": fast.get("fetched_at"),
                        "data": fast["quotes"],
                    }
                )
            )

        # Updates arrive via broadcast(); reading here also detects disconnects
        # (recv raises WebSocketDisconnect when the client closes the tab).
        while True:
            raw = await ws.receive_text()
            # Clients announce which Stock Detail symbol they have open so the
            # fast tier can include it. Anything unparseable is ignored on
            # purpose — this socket is a price feed, not a command channel, and
            # a malformed frame must not drop the connection.
            try:
                msg = json.loads(raw)
                if isinstance(msg, dict) and msg.get("type") == "watch":
                    ws_manager.set_viewing(ws, msg.get("symbol"))
            except (ValueError, TypeError):
                pass

    except WebSocketDisconnect:
        logger.debug("WS client disconnected cleanly")
    except Exception as exc:
        # Warning, not debug. A server-side bug in this handler (a NameError,
        # a bad payload) looks exactly like a client vanishing: the socket just
        # closes. Logging it at debug hid one for an entire debugging session —
        # the endpoint silently served the tape and dropped every fast quote.
        logger.warning("WS handler error: %s", exc, exc_info=True)
    finally:
        ws_manager.disconnect(ws)
