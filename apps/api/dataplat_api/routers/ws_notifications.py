"""WebSocket endpoint GET /api/ws/notifications — S052-F-052.

Authenticated clients connect with a valid JWT via ?token=<jwt> query param
and passively receive all notification events fired for the authenticated user.

No client subscribe/unsubscribe protocol — the client simply connects and
receives all events routed to their user_id. This is simpler than ws_runs.py
(no subscription dict, no subscribe/unsubscribe handler, no ServerAck on
connect). If the client sends any unexpected text, the server responds with
{"type":"error","code":"bad_message"} and keeps the connection open.

Auth: JWT decoded manually from query param at connect time (browsers cannot
send custom headers on WS upgrade; OAuth2PasswordBearer is HTTP-only).

Single-worker constraint: NotificationBroker is in-process asyncio. Running
uvicorn with --workers N > 1 causes silent event loss. See notification_broker.py.

Long-lived DB session note: Depends(get_session) holds a DB pool connection for
the lifetime of each WS connection (not per-query). At default pool_size=20 this
caps concurrent WS connections to ~20. Harmless for MVP. See ws_runs.py §12.

Subscribe+publish race: events fired between the webhook arriving and this client
connecting are lost. Frontend MUST poll REST endpoints on (re)connect to get
current state. Inherited constraint from F-051 — no change in MVP.
"""

from __future__ import annotations

import asyncio
import json
import logging

import jwt
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplat_api.config import settings
from dataplat_api.db.models import User
from dataplat_api.db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


async def _event_sender_task(
    websocket: WebSocket,
    queue: asyncio.Queue[dict],
) -> None:
    """Drain the per-connection outbound queue and forward as JSON text frames.

    Runs as a background asyncio.Task per WebSocket connection.
    Cancelled (via task.cancel()) in the finally block of the message loop.
    """
    try:
        while True:
            event = await queue.get()
            await websocket.send_text(json.dumps(event))
    except asyncio.CancelledError:
        pass
    except WebSocketDisconnect:
        pass


@router.websocket("/api/ws/notifications")
async def websocket_notifications_endpoint(
    websocket: WebSocket,
    session: AsyncSession = Depends(get_session),
) -> None:
    """WebSocket user-scoped notification stream endpoint.

    Connect: GET ws://host/api/ws/notifications?token=<jwt>
    After accepted, the server pushes all notification events for the
    authenticated user:
      - {"type":"asset.materialized","asset_key":"extract_mineru","partition_key":"src_42"}
      - {"type":"chunks.added","source_id":42,"count":17}

    No client subscribe/unsubscribe messages required. If client sends any
    text, server responds with {"type":"error","code":"bad_message"} and
    keeps the connection open.
    """
    # ── Auth: decode JWT from query param BEFORE accept ───────────────────────
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008)
        return
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        user_id = int(payload["sub"])
    except Exception:
        await websocket.close(code=1008)
        return

    result = await session.execute(select(User).where(User.id == user_id))
    user: User | None = result.scalar_one_or_none()
    if user is None:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    broker = websocket.app.state.notification_broker

    # One outbound queue per connection (maxsize=100).
    outbound: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)

    # Register with broker — all events for this user_id are routed here.
    broker.subscribe(user_id=user_id, queue=outbound)

    # One sender task per connection — reads from outbound and sends to WS.
    sender_task: asyncio.Task[None] | None = asyncio.create_task(
        _event_sender_task(websocket, outbound)
    )

    try:
        while True:
            try:
                text = await websocket.receive_text()
            except WebSocketDisconnect:
                break

            # No client protocol in F-052 — any message is unexpected.
            # Respond with bad_message but keep connection open (§4.3).
            _ = text  # content ignored
            await websocket.send_text(
                json.dumps({"type": "error", "code": "bad_message"})
            )

    except WebSocketDisconnect:
        pass
    finally:
        # Clean up broker subscription (try/finally guarantees cleanup on any
        # disconnect or exception — same pattern as ws_runs.py).
        broker.unsubscribe(user_id=user_id, queue=outbound)
        # Cancel sender task (None-guard per ws_runs.py NIT-2-1 precedent).
        if sender_task is not None:
            sender_task.cancel()
