"""WebSocket endpoint GET /api/ws/runs — S051-F-051.

Authenticated clients connect with a valid JWT via ?token=<jwt> query param,
then send {"type":"subscribe","run_id":N} messages to receive
run.status_changed events when Run rows transition status in Postgres.

Auth: JWT decoded manually from query param at connect time (browsers cannot
send custom headers on WS upgrade; OAuth2PasswordBearer is HTTP-only).

Owner-scope: only the user that triggered the run (Run.triggered_by == user.id)
may subscribe. Distinction between not_found and unauthorized is intentional —
mirrors the HTTP layer semantics (agreed.md §7 trade-off accepted).

Long-lived DB session note: Depends(get_session) holds a DB pool connection for
the lifetime of each WS connection (not per-query). At default pool_size=20 this
caps concurrent WS connections to ~20. Harmless for MVP. See agreed.md §12.

Single-worker constraint: RunEventBroker is in-process asyncio. Running uvicorn
with --workers N > 1 causes silent event loss. See broker.py docstring.
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
from dataplat_api.db.models import Run, User
from dataplat_api.db.session import get_session
from dataplat_api.schemas.realtime import (
    ClientSubscribe,
    ClientUnsubscribe,
    ServerAck,
    ServerError,
)

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


@router.websocket("/api/ws/runs")
async def websocket_runs_endpoint(
    websocket: WebSocket,
    session: AsyncSession = Depends(get_session),
) -> None:
    """WebSocket run-status subscription endpoint.

    Connect: GET ws://host/api/ws/runs?token=<jwt>
    After accepted, send {"type":"subscribe","run_id":N} to subscribe.

    Server pushes {"type":"run.status_changed","run_id":N,"kind":...,"from":...,
    "to":...,"metadata":{}} whenever the Dagster event webhook transitions
    that run's status.
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

    broker = websocket.app.state.run_broker

    # One shared outbound queue per connection (maxsize=100).
    # All broker subscriptions for this connection share this queue —
    # broker.publish() writes events here regardless of which run_id fired.
    outbound: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)

    # Per-connection subscription tracking (agreed.md §8 L2).
    # Maps run_id → outbound queue (same object for all entries; stored so
    # cleanup can call broker.unsubscribe(run_id, q) for each.
    subscriptions: dict[int, asyncio.Queue[dict]] = {}

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

            # ── Parse client command ──────────────────────────────────────────
            try:
                data = json.loads(text)
                cmd_type = data.get("type") if isinstance(data, dict) else None
            except (json.JSONDecodeError, AttributeError):
                await websocket.send_text(
                    ServerError(code="bad_message", run_id=None).model_dump_json()
                )
                continue

            if cmd_type == "subscribe":
                try:
                    cmd = ClientSubscribe(**data)
                except Exception:
                    await websocket.send_text(
                        ServerError(code="bad_message", run_id=None).model_dump_json()
                    )
                    continue

                # Step 1: existence check (two-step per agreed.md §7)
                try:
                    run_result = await session.execute(
                        select(Run).where(Run.id == cmd.run_id)
                    )
                    run: Run | None = run_result.scalar_one_or_none()
                except Exception as exc:
                    logger.warning(
                        "WS subscribe DB error for run_id=%s: %s", cmd.run_id, exc
                    )
                    await websocket.close(code=1011)
                    break

                if run is None:
                    await websocket.send_text(
                        ServerError(code="not_found", run_id=cmd.run_id).model_dump_json()
                    )
                    continue

                # Step 2: ownership check
                if run.triggered_by != user.id:
                    await websocket.send_text(
                        ServerError(
                            code="unauthorized", run_id=cmd.run_id
                        ).model_dump_json()
                    )
                    continue

                # Register subscription — shared outbound queue
                subscriptions[cmd.run_id] = outbound
                broker.subscribe(run_id=cmd.run_id, queue=outbound)
                await websocket.send_text(
                    ServerAck(type="subscribed", run_id=cmd.run_id).model_dump_json()
                )

            elif cmd_type == "unsubscribe":
                try:
                    cmd_u = ClientUnsubscribe(**data)
                except Exception:
                    await websocket.send_text(
                        ServerError(code="bad_message", run_id=None).model_dump_json()
                    )
                    continue

                q = subscriptions.pop(cmd_u.run_id, None)
                if q is not None:
                    broker.unsubscribe(run_id=cmd_u.run_id, queue=q)

                await websocket.send_text(
                    ServerAck(
                        type="unsubscribed", run_id=cmd_u.run_id
                    ).model_dump_json()
                )

            else:
                await websocket.send_text(
                    ServerError(code="bad_message", run_id=None).model_dump_json()
                )

    except WebSocketDisconnect:
        pass
    finally:
        # Clean up all subscriptions (agreed.md §8 L2 try/finally contract).
        for run_id, q in subscriptions.items():
            broker.unsubscribe(run_id, q)
        # Cancel sender task (NIT-2-1: guarded by None check).
        if sender_task is not None:
            sender_task.cancel()
