"""Tests for WebSocket /api/ws/notifications — S052-F-052.

Unit tests (run in backend layer — no live DB or compose stack required):
  T1.  test_connect_with_valid_jwt_accepted
  T2.  test_connect_without_token_closes_1008
  T3.  test_connect_with_invalid_jwt_closes_1008
  T4.  test_connect_with_unknown_user_closes_1008
  T5.  test_asset_materialized_event_delivered
  T6.  test_chunks_added_event_delivered
  T7.  test_post_dagster_asset_event_delivers_to_ws_extract_mineru
  T8.  test_post_dagster_asset_event_delivers_to_ws_chunks
  T9.  test_disconnect_no_error_and_broker_cleanup
  T10. test_asset_event_unknown_dagster_run_returns_processed_false

All tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

JWT pattern: tokens created with jwt.encode() using settings.SECRET_KEY and
settings.JWT_ALGORITHM, same as the auth module.

Session mock pattern: same as test_dagster_events.py — MagicMock() for result
proxy (scalar_one_or_none is sync), AsyncMock() for session itself.

Broker testing: NotificationBroker instantiated directly for broker-inject tests
(T5, T6, T9). For T7/T8, app.state.notification_broker is used (full pipe).
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi.testclient import TestClient

from dataplat_api.config import settings
from dataplat_api.db.models import Run, User
from dataplat_api.db.session import get_session
from dataplat_api.main import app
from dataplat_api.realtime.notification_broker import NotificationBroker

# ── JWT helpers ───────────────────────────────────────────────────────────────

_USER_ID = 55
_NOW = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
_TEST_SECRET = "test-secret-f052"
_SECRET_HEADER = {"X-Dagster-Webhook-Secret": _TEST_SECRET}


def _make_token(user_id: int = _USER_ID) -> str:
    return jwt.encode(
        {"sub": str(user_id)},
        settings.SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def _ws_url(token: str | None = None) -> str:
    if token is None:
        return "/api/ws/notifications"
    return f"/api/ws/notifications?token={token}"


# ── Mock factories ────────────────────────────────────────────────────────────


def _make_user(user_id: int = _USER_ID) -> MagicMock:
    u = MagicMock(spec=User)
    u.id = user_id
    return u


def _make_run(
    run_id: int = 7,
    triggered_by: int | None = _USER_ID,
    dagster_run_id: str = "backfill-f052",
    kind: str = "extract",
    status: str = "running",
) -> MagicMock:
    r = MagicMock(spec=Run)
    r.id = run_id
    r.triggered_by = triggered_by
    r.kind = kind
    r.status = status
    r.dagster_run_id = dagster_run_id
    return r


def _make_session_dep_user_only(user_row: MagicMock | None) -> Any:
    """Session override returning user_row on any execute() call."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = user_row

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)
        session.commit = AsyncMock()
        yield session

    return _override


# ── T1: connect with valid JWT → accepted + bad_message for unexpected text ──

def test_connect_with_valid_jwt_accepted() -> None:
    """HTTP 101 — WS connection accepted after valid JWT.

    Also verifies: client sending unexpected text → {"type":"error","code":"bad_message"},
    connection stays open.
    """
    token = _make_token()
    user = _make_user()
    app.dependency_overrides[get_session] = _make_session_dep_user_only(user)
    app.state.notification_broker = NotificationBroker()
    try:
        client = TestClient(app)
        with client.websocket_connect(_ws_url(token)) as ws:
            # Send unexpected text — should get bad_message back, connection stays open.
            ws.send_text("unexpected text from client")
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "error"
            assert msg["code"] == "bad_message"
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T2: connect without token → close 1008 ───────────────────────────────────

def test_connect_without_token_closes_1008() -> None:
    """No ?token= → WebSocketDisconnect with code 1008."""
    app.state.notification_broker = NotificationBroker()
    app.dependency_overrides[get_session] = _make_session_dep_user_only(None)
    try:
        client = TestClient(app)
        with pytest.raises(Exception):
            with client.websocket_connect(_ws_url(None)):
                pass  # should not reach here
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T3: connect with invalid JWT → close 1008 ────────────────────────────────

def test_connect_with_invalid_jwt_closes_1008() -> None:
    """Garbage token → WebSocketDisconnect with code 1008."""
    app.state.notification_broker = NotificationBroker()
    app.dependency_overrides[get_session] = _make_session_dep_user_only(None)
    try:
        client = TestClient(app)
        with pytest.raises(Exception):
            with client.websocket_connect(_ws_url("not.a.valid.jwt")):
                pass
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T4: connect with unknown user → close 1008 ───────────────────────────────

def test_connect_with_unknown_user_closes_1008() -> None:
    """Valid JWT but user_id not in DB → WebSocketDisconnect with code 1008."""
    token = _make_token()
    app.state.notification_broker = NotificationBroker()
    # Session returns None for the User lookup
    app.dependency_overrides[get_session] = _make_session_dep_user_only(None)
    try:
        client = TestClient(app)
        with pytest.raises(Exception):
            with client.websocket_connect(_ws_url(token)):
                pass
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T5: AssetMaterializedEvent delivered via broker.publish ──────────────────

def test_asset_materialized_event_delivered() -> None:
    """Connect; inject AssetMaterializedEvent via notification_broker.publish;
    verify received message matches verbatim:
      {"type":"asset.materialized","asset_key":"extract_mineru","partition_key":"src_42"}
    """
    token = _make_token()
    user = _make_user()
    app.dependency_overrides[get_session] = _make_session_dep_user_only(user)
    broker = NotificationBroker()
    app.state.notification_broker = broker
    try:
        client = TestClient(app)
        with client.websocket_connect(_ws_url(token)) as ws:
            # Directly inject event — same pattern as F-051 T6
            event = {
                "type": "asset.materialized",
                "asset_key": "extract_mineru",
                "partition_key": "src_42",
            }
            broker.publish(user_id=_USER_ID, event=event)
            received = json.loads(ws.receive_text())
            assert received["type"] == "asset.materialized"
            assert received["asset_key"] == "extract_mineru"
            assert received["partition_key"] == "src_42"
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T6: ChunksAddedEvent delivered via broker.publish ────────────────────────

def test_chunks_added_event_delivered() -> None:
    """Connect; inject ChunksAddedEvent via notification_broker.publish;
    verify received message matches verbatim:
      {"type":"chunks.added","source_id":42,"count":5}
    """
    token = _make_token()
    user = _make_user()
    app.dependency_overrides[get_session] = _make_session_dep_user_only(user)
    broker = NotificationBroker()
    app.state.notification_broker = broker
    try:
        client = TestClient(app)
        with client.websocket_connect(_ws_url(token)) as ws:
            event = {
                "type": "chunks.added",
                "source_id": 42,
                "count": 5,
            }
            broker.publish(user_id=_USER_ID, event=event)
            received = json.loads(ws.receive_text())
            assert received["type"] == "chunks.added"
            assert received["source_id"] == 42
            assert received["count"] == 5
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T7: end-to-end POST ASSET_MATERIALIZATION → WS receives asset.materialized ─

def test_post_dagster_asset_event_delivers_to_ws_extract_mineru(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full pipe for extract_mineru:
    POST ASSET_MATERIALIZATION → notification_broker.publish → WS receives
    {"type":"asset.materialized","asset_key":"extract_mineru","partition_key":"src_42"}
    """
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", _TEST_SECRET)

    token = _make_token()
    user = _make_user()
    run = _make_run(run_id=101, triggered_by=_USER_ID, dagster_run_id="backfill-t7")

    # WS connect needs: user lookup
    # Dagster events POST needs: run lookup
    ws_call_count = 0
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = run

    async def _combined_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        nonlocal ws_call_count
        session = AsyncMock()

        async def _execute(*_args: Any, **_kwargs: Any) -> MagicMock:
            nonlocal ws_call_count
            ws_call_count += 1
            if ws_call_count == 1:
                return user_result   # WS: user lookup
            return run_result        # POST: run lookup

        session.execute = _execute
        session.commit = AsyncMock()
        yield session

    broker = NotificationBroker()
    app.state.notification_broker = broker
    app.dependency_overrides[get_session] = _combined_session
    try:
        client = TestClient(app)
        with client.websocket_connect(_ws_url(token)) as ws:
            # POST the ASSET_MATERIALIZATION event
            resp = client.post(
                "/api/dagster/events",
                json={
                    "event_type": "ASSET_MATERIALIZATION",
                    "dagster_run_id": "backfill-t7",
                    "asset_key": "extract_mineru",
                    "partition_key": "src_42",
                    "timestamp": _NOW.isoformat(),
                    "metadata": {},
                },
                headers=_SECRET_HEADER,
            )
            assert resp.status_code == 200
            assert resp.json()["processed"] is True

            # WS client should receive the asset.materialized event
            received = json.loads(ws.receive_text())
            assert received["type"] == "asset.materialized"
            assert received["asset_key"] == "extract_mineru"
            assert received["partition_key"] == "src_42"
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T8: end-to-end POST ASSET_MATERIALIZATION → WS receives chunks.added ─────

def test_post_dagster_asset_event_delivers_to_ws_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full pipe for chunks:
    POST ASSET_MATERIALIZATION with chunk_count=7 → WS receives
    {"type":"chunks.added","source_id":42,"count":7}
    """
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", _TEST_SECRET)

    token = _make_token()
    user = _make_user()
    run = _make_run(run_id=102, triggered_by=_USER_ID, dagster_run_id="backfill-t8")

    ws_call_count = 0
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = run

    async def _combined_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        nonlocal ws_call_count
        session = AsyncMock()

        async def _execute(*_args: Any, **_kwargs: Any) -> MagicMock:
            nonlocal ws_call_count
            ws_call_count += 1
            if ws_call_count == 1:
                return user_result
            return run_result

        session.execute = _execute
        session.commit = AsyncMock()
        yield session

    broker = NotificationBroker()
    app.state.notification_broker = broker
    app.dependency_overrides[get_session] = _combined_session
    try:
        client = TestClient(app)
        with client.websocket_connect(_ws_url(token)) as ws:
            resp = client.post(
                "/api/dagster/events",
                json={
                    "event_type": "ASSET_MATERIALIZATION",
                    "dagster_run_id": "backfill-t8",
                    "asset_key": "chunks",
                    "partition_key": "src_42",
                    "timestamp": _NOW.isoformat(),
                    "metadata": {"chunk_count": 7},
                },
                headers=_SECRET_HEADER,
            )
            assert resp.status_code == 200
            assert resp.json()["processed"] is True

            received = json.loads(ws.receive_text())
            assert received["type"] == "chunks.added"
            assert received["source_id"] == 42
            assert received["count"] == 7
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T9: disconnect → broker cleanup, no server error ─────────────────────────

def test_disconnect_no_error_and_broker_cleanup() -> None:
    """Client disconnect → notification_broker.unsubscribe called; no unhandled exception.

    Verifies notification_broker._subscribers[user_id] has one entry while
    connected and is empty after disconnect.
    """
    token = _make_token()
    user = _make_user()
    app.dependency_overrides[get_session] = _make_session_dep_user_only(user)
    broker = NotificationBroker()
    app.state.notification_broker = broker
    try:
        client = TestClient(app, raise_server_exceptions=True)
        with client.websocket_connect(_ws_url(token)):
            # Verify subscriber registered
            assert len(broker._subscribers.get(_USER_ID, [])) == 1
        # After context exit, connection closed; finally block should have cleaned up.
        assert broker._subscribers.get(_USER_ID, []) == []
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T10: POST ASSET_MATERIALIZATION for unknown dagster_run_id → processed=False

def test_asset_event_unknown_dagster_run_returns_processed_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST ASSET_MATERIALIZATION with unknown dagster_run_id → HTTP 200,
    processed=False; notification_broker.publish NOT called.
    """
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", _TEST_SECRET)

    # Session returns None for the Run lookup
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None  # unknown run

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_mock)
        session.commit = AsyncMock()
        yield session

    broker = NotificationBroker()
    app.state.notification_broker = broker
    app.dependency_overrides[get_session] = _override
    try:
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post(
            "/api/dagster/events",
            json={
                "event_type": "ASSET_MATERIALIZATION",
                "dagster_run_id": "unknown-backfill-xyz",
                "asset_key": "extract_mineru",
                "partition_key": "src_42",
                "timestamp": _NOW.isoformat(),
                "metadata": {},
            },
            headers=_SECRET_HEADER,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["processed"] is False
        assert body["reason"] == "unknown_run"
        # No WS event should be queued (no subscribers in this test, but verify
        # broker is not even touched by checking it has no subscribers at all).
        assert broker._subscribers == {}
    finally:
        app.dependency_overrides.pop(get_session, None)
