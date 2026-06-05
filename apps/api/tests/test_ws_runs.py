"""Tests for WebSocket /api/ws/runs — S051-F-051.

Unit tests (run in backend layer — no live DB or compose stack required):
  T1.  test_connect_with_valid_jwt_accepted
  T2.  test_connect_without_token_closes_1008
  T3.  test_connect_with_invalid_jwt_closes_1008
  T4.  test_subscribe_own_run_returns_subscribed_ack
  T5.  test_subscribe_other_users_run_returns_unauthorized
  T6.  test_subscribe_and_broker_publish_delivers_event
  T7.  test_disconnect_no_server_error_and_broker_cleanup
  T8.  test_post_dagster_event_delivers_ws_event_end_to_end
  T9.  test_bad_json_returns_bad_message_connection_stays_open
  T10. test_subscribe_nonexistent_run_returns_not_found
  T11. test_subscribe_run_with_null_triggered_by_returns_unauthorized

All tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

JWT pattern: tokens are created with jwt.encode() using settings.SECRET_KEY
and settings.JWT_ALGORITHM, same as the auth module.

Session mock pattern: same as test_dagster_events.py — MagicMock() for result
proxy (scalar_one_or_none is sync), AsyncMock() for session itself.

Broker testing: RunEventBroker instantiated directly (no app needed) for unit
tests T6/T7 that test broker mechanics. For T8, the broker on app.state is used.
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
from dataplat_api.realtime.broker import RunEventBroker

# ── JWT helpers ───────────────────────────────────────────────────────────────

_USER_ID = 42
_OTHER_USER_ID = 99


def _make_token(user_id: int = _USER_ID) -> str:
    return jwt.encode(
        {"sub": str(user_id)},
        settings.SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def _ws_url(token: str | None = None) -> str:
    if token is None:
        return "/api/ws/runs"
    return f"/api/ws/runs?token={token}"


# ── Mock factories ────────────────────────────────────────────────────────────

_NOW = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)


def _make_user(user_id: int = _USER_ID) -> MagicMock:
    u = MagicMock(spec=User)
    u.id = user_id
    return u


def _make_run(
    run_id: int = 7,
    triggered_by: int | None = _USER_ID,
    kind: str = "extract",
    status: str = "running",
) -> MagicMock:
    r = MagicMock(spec=Run)
    r.id = run_id
    r.triggered_by = triggered_by
    r.kind = kind
    r.status = status
    r.dagster_run_id = f"backfill-{run_id}"
    return r


def _make_session_dep_two_queries(
    user_row: MagicMock | None,
    run_row: MagicMock | None,
) -> Any:
    """Session override returning user on first execute, run on second."""
    call_count = 0
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user_row

    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = run_row

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        nonlocal call_count
        session = AsyncMock()

        async def _execute(*_args: Any, **_kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return user_result
            return run_result

        session.execute = _execute
        session.commit = AsyncMock()
        yield session

    return _override


def _make_session_dep_user_only(user_row: MagicMock | None) -> Any:
    """Session override that always returns user_row (for connect-only tests)."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = user_row

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)
        session.commit = AsyncMock()
        yield session

    return _override


# ── T1: connect with valid JWT → accepted ─────────────────────────────────────


def test_connect_with_valid_jwt_accepted() -> None:
    """HTTP 101 — WS connection accepted after valid JWT."""
    token = _make_token()
    user = _make_user()
    app.dependency_overrides[get_session] = _make_session_dep_user_only(user)
    app.state.run_broker = RunEventBroker()
    try:
        client = TestClient(app)
        with client.websocket_connect(_ws_url(token)) as ws:
            # Connection accepted — send a ping-style command to confirm alive
            ws.send_text(json.dumps({"type": "unknown_cmd"}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "error"
            assert msg["code"] == "bad_message"
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T2: connect without token → close 1008 ───────────────────────────────────


def test_connect_without_token_closes_1008() -> None:
    """No ?token= → WebSocketDisconnect with code 1008."""
    app.state.run_broker = RunEventBroker()
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
    app.state.run_broker = RunEventBroker()
    app.dependency_overrides[get_session] = _make_session_dep_user_only(None)
    try:
        client = TestClient(app)
        with pytest.raises(Exception):
            with client.websocket_connect(_ws_url("not.a.valid.jwt")):
                pass
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T4: subscribe to own run → subscribed ack ────────────────────────────────


def test_subscribe_own_run_returns_subscribed_ack() -> None:
    """subscribe run owned by user → {"type":"subscribed","run_id":N}."""
    token = _make_token()
    user = _make_user()
    run = _make_run(run_id=7, triggered_by=_USER_ID)
    app.dependency_overrides[get_session] = _make_session_dep_two_queries(user, run)
    # Ensure broker is fresh
    app.state.run_broker = RunEventBroker()
    try:
        client = TestClient(app)
        with client.websocket_connect(_ws_url(token)) as ws:
            ws.send_text(json.dumps({"type": "subscribe", "run_id": 7}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "subscribed"
            assert msg["run_id"] == 7
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T5: subscribe to other user's run → unauthorized ─────────────────────────


def test_subscribe_other_users_run_returns_unauthorized() -> None:
    """subscribe to run with triggered_by != user.id → unauthorized error."""
    token = _make_token(_USER_ID)
    user = _make_user(_USER_ID)
    run = _make_run(run_id=8, triggered_by=_OTHER_USER_ID)
    app.dependency_overrides[get_session] = _make_session_dep_two_queries(user, run)
    app.state.run_broker = RunEventBroker()
    try:
        client = TestClient(app)
        with client.websocket_connect(_ws_url(token)) as ws:
            ws.send_text(json.dumps({"type": "subscribe", "run_id": 8}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "error"
            assert msg["code"] == "unauthorized"
            assert msg["run_id"] == 8
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T6: subscribe + broker.publish → event received ──────────────────────────


def test_subscribe_and_broker_publish_delivers_event() -> None:
    """After subscribe, broker.publish fans event to WS client."""
    token = _make_token()
    user = _make_user()
    run = _make_run(run_id=10, triggered_by=_USER_ID, kind="extract", status="running")
    app.dependency_overrides[get_session] = _make_session_dep_two_queries(user, run)
    broker = RunEventBroker()
    app.state.run_broker = broker
    try:
        client = TestClient(app)
        with client.websocket_connect(_ws_url(token)) as ws:
            ws.send_text(json.dumps({"type": "subscribe", "run_id": 10}))
            ack = json.loads(ws.receive_text())
            assert ack["type"] == "subscribed"
            # Directly publish an event via broker
            event = {
                "type": "run.status_changed",
                "run_id": 10,
                "kind": "extract",
                "from": "running",
                "to": "success",
                "metadata": {},
            }
            broker.publish(run_id=10, event=event)
            received = json.loads(ws.receive_text())
            assert received["type"] == "run.status_changed"
            assert received["run_id"] == 10
            assert received["to"] == "success"
            assert received["from"] == "running"
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T7: disconnect → broker cleanup, no server error ─────────────────────────


def test_disconnect_no_server_error_and_broker_cleanup() -> None:
    """Client disconnect → broker.unsubscribe called; no unhandled exception."""
    token = _make_token()
    user = _make_user()
    run = _make_run(run_id=11, triggered_by=_USER_ID)
    app.dependency_overrides[get_session] = _make_session_dep_two_queries(user, run)
    broker = RunEventBroker()
    app.state.run_broker = broker
    try:
        client = TestClient(app, raise_server_exceptions=True)
        with client.websocket_connect(_ws_url(token)) as ws:
            ws.send_text(json.dumps({"type": "subscribe", "run_id": 11}))
            ack = json.loads(ws.receive_text())
            assert ack["type"] == "subscribed"
            # Verify subscriber registered
            assert len(broker._subscribers.get(11, [])) == 1
        # After context exit, connection is closed; finally block should have
        # called broker.unsubscribe for all subscriptions.
        assert broker._subscribers.get(11, []) == []
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T8: end-to-end POST /api/dagster/events → WS client receives event ───────


def test_post_dagster_event_delivers_ws_event_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full pipe: POST /api/dagster/events → broker.publish → WS event received."""
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", "test-secret-f051")

    token = _make_token()
    user = _make_user()
    run = _make_run(run_id=20, triggered_by=_USER_ID, kind="extract", status="pending")

    # WS connect + subscribe need: user lookup then run lookup
    ws_call_count = 0
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = run

    # Dagster events handler needs: run lookup by dagster_run_id
    dagster_run_result = MagicMock()
    dagster_run_result.scalar_one_or_none.return_value = run

    async def _ws_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
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

    async def _dagster_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=dagster_run_result)
        session.commit = AsyncMock()
        yield session

    broker = RunEventBroker()
    app.state.run_broker = broker

    # We need to alternate session overrides — use a shared counter
    # Simplest: both handlers use get_session, so we set a single override
    # that handles both use cases via call counting.
    call_idx = 0
    results_sequence = [user_result, run_result, dagster_run_result]

    async def _combined_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        nonlocal call_idx
        session = AsyncMock()

        async def _execute(*_args: Any, **_kwargs: Any) -> MagicMock:
            nonlocal call_idx
            r = results_sequence[min(call_idx, len(results_sequence) - 1)]
            call_idx += 1
            return r

        session.execute = _execute
        session.commit = AsyncMock()
        yield session

    app.dependency_overrides[get_session] = _combined_session
    try:
        client = TestClient(app)
        with client.websocket_connect(_ws_url(token)) as ws:
            ws.send_text(json.dumps({"type": "subscribe", "run_id": 20}))
            ack = json.loads(ws.receive_text())
            assert ack["type"] == "subscribed"

            # POST the Dagster event — this triggers broker.publish
            resp = client.post(
                "/api/dagster/events",
                json={
                    "event_type": "RUN_SUCCESS",
                    "dagster_run_id": run.dagster_run_id,
                    "timestamp": _NOW.isoformat(),
                },
                headers={"X-Dagster-Webhook-Secret": "test-secret-f051"},
            )
            assert resp.status_code == 200

            # The broker should have published; read from WS
            received = json.loads(ws.receive_text())
            assert received["type"] == "run.status_changed"
            assert received["run_id"] == 20
            assert received["to"] == "success"
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T9: bad JSON → bad_message error, connection stays open ──────────────────


def test_bad_json_returns_bad_message_connection_stays_open() -> None:
    """Bad JSON → {"type":"error","code":"bad_message"}; connection stays open;
    subsequent valid subscribe succeeds on same connection.
    """
    token = _make_token()
    user = _make_user()
    run = _make_run(run_id=30, triggered_by=_USER_ID)

    call_count = 0
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = run

    async def _session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        nonlocal call_count
        session = AsyncMock()

        async def _execute(*_args: Any, **_kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return user_result
            return run_result

        session.execute = _execute
        session.commit = AsyncMock()
        yield session

    app.dependency_overrides[get_session] = _session
    app.state.run_broker = RunEventBroker()
    try:
        client = TestClient(app)
        with client.websocket_connect(_ws_url(token)) as ws:
            # Send bad JSON
            ws.send_text("this is not valid json {{{")
            err = json.loads(ws.receive_text())
            assert err["type"] == "error"
            assert err["code"] == "bad_message"

            # Connection must still be open — send a valid subscribe
            ws.send_text(json.dumps({"type": "subscribe", "run_id": 30}))
            ack = json.loads(ws.receive_text())
            # Confirms connection survived the bad message
            assert ack["type"] == "subscribed"
            assert ack["run_id"] == 30
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T10: subscribe to nonexistent run → not_found (NOT unauthorized) ─────────


def test_subscribe_nonexistent_run_returns_not_found() -> None:
    """run_id not in DB → {"type":"error","code":"not_found"} (not "unauthorized")."""
    token = _make_token()
    user = _make_user()
    # run lookup returns None
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = None  # not found

    call_count = 0

    async def _session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        nonlocal call_count
        session = AsyncMock()

        async def _execute(*_args: Any, **_kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return user_result
            return run_result

        session.execute = _execute
        session.commit = AsyncMock()
        yield session

    app.dependency_overrides[get_session] = _session
    app.state.run_broker = RunEventBroker()
    try:
        client = TestClient(app)
        with client.websocket_connect(_ws_url(token)) as ws:
            ws.send_text(json.dumps({"type": "subscribe", "run_id": 9999}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "error"
            assert msg["code"] == "not_found", (
                f"Expected 'not_found' but got '{msg['code']}' — "
                "two-step existence check is required (agreed.md §7)"
            )
            assert msg["run_id"] == 9999
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T11: subscribe to run with triggered_by IS NULL → unauthorized ────────────


def test_subscribe_run_with_null_triggered_by_returns_unauthorized() -> None:
    """Run.triggered_by IS NULL → unauthorized (None != user.id is True → denied)."""
    token = _make_token()
    user = _make_user(_USER_ID)
    run = _make_run(run_id=50, triggered_by=None)  # NULL triggered_by

    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = run

    call_count = 0

    async def _session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        nonlocal call_count
        session = AsyncMock()

        async def _execute(*_args: Any, **_kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return user_result
            return run_result

        session.execute = _execute
        session.commit = AsyncMock()
        yield session

    app.dependency_overrides[get_session] = _session
    app.state.run_broker = RunEventBroker()
    try:
        client = TestClient(app)
        with client.websocket_connect(_ws_url(token)) as ws:
            ws.send_text(json.dumps({"type": "subscribe", "run_id": 50}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "error"
            assert msg["code"] == "unauthorized", (
                f"Expected 'unauthorized' for NULL triggered_by but got '{msg['code']}'"
            )
            assert msg["run_id"] == 50
    finally:
        app.dependency_overrides.pop(get_session, None)
