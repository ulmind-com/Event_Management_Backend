import asyncio
import json
import logging
from typing import Optional, List
from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from app.core.realtime_bus import event_bus

logger = logging.getLogger("eventora.routes.realtime")
router = APIRouter()

@router.get("/stream")
async def realtime_sse_stream(
    request: Request,
    channels: Optional[str] = Query("admin:all,admin:events,*", description="Comma-separated channel topics to subscribe to")
):
    """
    High-performance Server-Sent Events (SSE) stream.
    Clients subscribe with native browser EventSource:
    const es = new EventSource('/api/realtime/stream?channels=admin:events,event:<eventId>');
    """
    raw_channels = [c.strip() for c in (channels or "*").split(",") if c.strip()]
    if not raw_channels:
        raw_channels = ["*"]

    queue = await event_bus.subscribe(raw_channels)

    async def event_generator():
        try:
            # 1. Handshake frame
            yield f"event: connected\ndata: {json.dumps({'status': 'connected', 'channels': raw_channels})}\n\n"

            while True:
                # Disconnection check
                if await request.is_disconnected():
                    break

                try:
                    # Wait up to 15 seconds for a real event, else send keepalive heartbeat
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    evt_type = payload.get("event", "message")
                    data_str = json.dumps(payload, default=str)
                    yield f"event: {evt_type}\ndata: {data_str}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat comment to keep connection alive through cloud proxies (Render/Cloudflare)
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Error in SSE stream: {e}")
        finally:
            await event_bus.unsubscribe(queue, raw_channels)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        }
    )


@router.websocket("/ws")
async def realtime_websocket(websocket: WebSocket):
    """
    Real-time WebSocket endpoint for bi-directional live subscriptions.
    """
    await websocket.accept()
    subscribed_channels: List[str] = ["admin:all", "admin:events", "*"]
    queue = await event_bus.subscribe(subscribed_channels)

    async def sender():
        try:
            # Initial handshake
            await websocket.send_json({
                "event": "connected",
                "status": "connected",
                "channels": subscribed_channels
            })
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=20.0)
                    await websocket.send_json(payload)
                except asyncio.TimeoutError:
                    await websocket.send_text("ping")
        except Exception:
            pass

    async def receiver():
        try:
            while True:
                msg = await websocket.receive_text()
                if msg == "pong" or msg == "ping":
                    continue
                try:
                    data = json.loads(msg)
                    if data.get("action") == "subscribe" and isinstance(data.get("channels"), list):
                        new_channels = [str(c).strip() for c in data["channels"] if str(c).strip()]
                        if new_channels:
                            await event_bus.unsubscribe(queue, subscribed_channels)
                            subscribed_channels.clear()
                            subscribed_channels.extend(new_channels)
                            await event_bus.subscribe(subscribed_channels)
                            await websocket.send_json({"event": "subscribed", "channels": subscribed_channels})
                except json.JSONDecodeError:
                    pass
        except WebSocketDisconnect:
            pass
        except Exception:
            pass

    sender_task = asyncio.create_task(sender())
    receiver_task = asyncio.create_task(receiver())

    done, pending = await asyncio.wait(
        [sender_task, receiver_task],
        return_when=asyncio.FIRST_COMPLETED
    )

    for task in pending:
        task.cancel()

    await event_bus.unsubscribe(queue, subscribed_channels)
