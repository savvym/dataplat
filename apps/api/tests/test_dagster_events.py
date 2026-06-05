"""Tests for POST /api/dagster/events — S050-F-050 / S052-F-052.

Unit tests (run in backend layer — no live DB or compose stack required):
  T1.  test_run_success_event_updates_status
  T2.  test_run_failure_event_updates_status
  T3.  test_run_start_event_updates_status_and_started_at
  T4.  test_run_canceled_maps_to_failure
  T5.  test_session_commit_called_on_known_run
  T6.  test_unknown_dagster_run_id_returns_processed_false
  T7.  test_missing_secret_header_returns_401
  T8.  test_wrong_secret_header_returns_401
  T9.  test_invalid_event_type_returns_422
  T10. test_unconfigured_webhook_secret_returns_500
  T11. test_asset_materialization_event_accepted_200 (S052)
  T12. test_asset_materialization_invalid_payload_returns_422 (S052)
  T13. test_asset_materialization_publishes_to_notification_broker (S052)
  T14. test_asset_materialization_unknown_run_returns_processed_false (S052)
  T15. test_asset_materialization_malformed_partition_key_skips_publish (S052)
  T16. test_notification_broker_publish_exception_still_returns_200 (S052)

All tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Auth pattern: DAGSTER_WEBHOOK_SECRET is monkeypatched on settings for every test
that needs a configured secret. The default in config.py is "" (CI-safe); tests
that need auth to pass set it to "test-secret" via monkeypatch.setattr.

Session mock pattern (single execute() call + optional commit()):
  result_mock = MagicMock()
  result_mock.scalar_one_or_none.return_value = run_row  # or None
  session = AsyncMock()
  session.execute = AsyncMock(return_value=result_mock)
  session.commit = AsyncMock()

  scalar_one_or_none() is synchronous on the result proxy (same pattern as
  test_runs_get.py). Use MagicMock() for the result proxy, NOT AsyncMock().

T10 specifically verifies the fail-closed guard (OQ-2): even a correct header +
valid payload returns 500 when settings.DAGSTER_WEBHOOK_SECRET == "".
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from dataplat_api.config import settings
from dataplat_api.db.models import Run
from dataplat_api.db.session import get_session
from dataplat_api.main import app
from dataplat_api.realtime.notification_broker import NotificationBroker

# ── Secret constant used by all auth-passing tests ────────────────────────────

_TEST_SECRET = "test-secret-f050"
_SECRET_HEADER = {"X-Dagster-Webhook-Secret": _TEST_SECRET}

# ── Timestamp constant ────────────────────────────────────────────────────────

_NOW = datetime(2026, 6, 5, 10, 30, 0, tzinfo=timezone.utc)
_NOW_ISO = _NOW.isoformat()

# ── Minimal valid payload factory ─────────────────────────────────────────────


def _payload(
    event_type: str = "RUN_SUCCESS",
    dagster_run_id: str = "backfill-abc123",
    timestamp: str = _NOW_ISO,
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "dagster_run_id": dagster_run_id,
        "timestamp": timestamp,
    }


# ── Run row mock factory ──────────────────────────────────────────────────────


def _make_run_row(
    status: str = "pending",
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> MagicMock:
    """Build a plain MagicMock(spec=Run) that looks like a Run ORM row.

    Uses MagicMock(spec=Run) consistent with test_runs_get.py / test_runs_list.py.
    The handler sets run.status / run.started_at / run.ended_at directly on the
    mock; assertions after the call read back those attribute values.
    """
    row = MagicMock(spec=Run)
    row.status = status
    row.started_at = started_at
    row.ended_at = ended_at
    return row


# ── Session dependency override factory ───────────────────────────────────────


def _make_session_dep(run_row: MagicMock | None) -> Any:
    """Return a get_session dependency override.

    session.execute() is called once by the handler (SELECT by dagster_run_id).
    scalar_one_or_none() is synchronous on the result proxy — use MagicMock(),
    NOT AsyncMock(), for the result (same discipline as test_runs_get.py).
    """
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = run_row

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_mock)
        session.commit = AsyncMock()
        yield session

    return _override


# ── T1: RUN_SUCCESS → status='success', ended_at set ─────────────────────────


def test_run_success_event_updates_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST RUN_SUCCESS → run.status='success'; processed=True; ended_at not None."""
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", _TEST_SECRET)
    run_row = _make_run_row()
    app.dependency_overrides[get_session] = _make_session_dep(run_row)
    try:
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post(
            "/api/dagster/events",
            json=_payload("RUN_SUCCESS"),
            headers=_SECRET_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json() == {"processed": True, "reason": None}
        assert run_row.status == "success"
        assert run_row.ended_at is not None
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T2: RUN_FAILURE → status='failure', ended_at set ─────────────────────────


def test_run_failure_event_updates_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST RUN_FAILURE → run.status='failure'; ended_at not None."""
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", _TEST_SECRET)
    run_row = _make_run_row()
    app.dependency_overrides[get_session] = _make_session_dep(run_row)
    try:
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post(
            "/api/dagster/events",
            json=_payload("RUN_FAILURE"),
            headers=_SECRET_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["processed"] is True
        assert run_row.status == "failure"
        assert run_row.ended_at is not None
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T3: RUN_START → status='running', started_at set, ended_at unchanged ──────


def test_run_start_event_updates_status_and_started_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST RUN_START → run.status='running'; started_at not None; ended_at unchanged (None)."""
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", _TEST_SECRET)
    run_row = _make_run_row(status="pending", started_at=None, ended_at=None)
    app.dependency_overrides[get_session] = _make_session_dep(run_row)
    try:
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post(
            "/api/dagster/events",
            json=_payload("RUN_START"),
            headers=_SECRET_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["processed"] is True
        assert run_row.status == "running"
        assert run_row.started_at is not None
        assert run_row.ended_at is None
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T4: RUN_CANCELED maps to failure, ended_at set ───────────────────────────


def test_run_canceled_maps_to_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST RUN_CANCELED → run.status='failure'; ended_at not None (same as RUN_FAILURE)."""
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", _TEST_SECRET)
    run_row = _make_run_row()
    app.dependency_overrides[get_session] = _make_session_dep(run_row)
    try:
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post(
            "/api/dagster/events",
            json=_payload("RUN_CANCELED"),
            headers=_SECRET_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["processed"] is True
        assert run_row.status == "failure"
        assert run_row.ended_at is not None
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T5: session.commit() is called exactly once on a known run ────────────────


def test_session_commit_called_on_known_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST RUN_SUCCESS with known run → session.commit called exactly once."""
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", _TEST_SECRET)
    run_row = _make_run_row()

    # Need to capture the session instance to inspect commit calls.
    captured_session: list[AsyncMock] = []
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = run_row

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_mock)
        session.commit = AsyncMock()
        captured_session.append(session)
        yield session

    app.dependency_overrides[get_session] = _override
    try:
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post(
            "/api/dagster/events",
            json=_payload("RUN_SUCCESS"),
            headers=_SECRET_HEADER,
        )
        assert resp.status_code == 200
        assert len(captured_session) == 1
        captured_session[0].commit.assert_called_once()
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T6: unknown dagster_run_id → 200, processed=False, no commit ─────────────


def test_unknown_dagster_run_id_returns_processed_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scalar_one_or_none returns None → HTTP 200, processed=False, reason='unknown_run';
    session.commit NOT called (no DB mutation for unknown runs).
    """
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", _TEST_SECRET)

    captured_session: list[AsyncMock] = []
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None  # unknown run

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_mock)
        session.commit = AsyncMock()
        captured_session.append(session)
        yield session

    app.dependency_overrides[get_session] = _override
    try:
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post(
            "/api/dagster/events",
            json=_payload("RUN_SUCCESS", dagster_run_id="unknown-run-xyz"),
            headers=_SECRET_HEADER,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["processed"] is False
        assert body["reason"] == "unknown_run"
        # commit must NOT be called — no DB mutation for unknown runs
        assert len(captured_session) == 1
        captured_session[0].commit.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T7: missing X-Dagster-Webhook-Secret header → 401 ────────────────────────


def test_missing_secret_header_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """No X-Dagster-Webhook-Secret header → HTTP 401."""
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", _TEST_SECRET)
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post(
        "/api/dagster/events",
        json=_payload(),
        # No X-Dagster-Webhook-Secret header
    )
    assert resp.status_code == 401
    assert "Invalid webhook secret" in resp.json()["detail"]


# ── T8: wrong secret header value → 401 ──────────────────────────────────────


def test_wrong_secret_header_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """X-Dagster-Webhook-Secret with wrong value → HTTP 401."""
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", _TEST_SECRET)
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post(
        "/api/dagster/events",
        json=_payload(),
        headers={"X-Dagster-Webhook-Secret": "wrong-secret-value"},
    )
    assert resp.status_code == 401
    assert "Invalid webhook secret" in resp.json()["detail"]


# ── T9: invalid event_type → 422 ──────────────────────────────────────────────


def test_invalid_event_type_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """event_type='RUN_QUEUED' is not in the Literal → HTTP 422 (Pydantic validation)."""
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", _TEST_SECRET)
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.post(
        "/api/dagster/events",
        json=_payload("RUN_QUEUED"),  # not a valid Literal value
        headers=_SECRET_HEADER,
    )
    assert resp.status_code == 422


# ── T10: unconfigured DAGSTER_WEBHOOK_SECRET → 500 (fail-closed guard) ────────


def test_unconfigured_webhook_secret_returns_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """settings.DAGSTER_WEBHOOK_SECRET='' → HTTP 500 regardless of header (OQ-2 fail-closed).

    Verifies that a misconfigured deployment returns 500 immediately rather than
    silently accepting any caller that sends an empty X-Dagster-Webhook-Secret header.
    This is the first check in the handler — before any compare_digest call.
    """
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", "")
    client = TestClient(app, raise_server_exceptions=False)
    # Even a header that would match "" passes compare_digest("","") — but the
    # fail-closed guard fires BEFORE compare_digest, so we get 500.
    resp = client.post(
        "/api/dagster/events",
        json=_payload(),
        headers={"X-Dagster-Webhook-Secret": ""},  # would match "" if guard absent
    )
    assert resp.status_code == 500
    assert "Webhook secret not configured" in resp.json()["detail"]


# ════════════════════════════════════════════════════════════════════════════
# S052-F-052: ASSET_MATERIALIZATION event type (T11–T16)
# ════════════════════════════════════════════════════════════════════════════

# ── Shared helpers for S052 tests ──────────────────────────────────────────────

_USER_ID_F052 = 77


def _asset_payload(
    event_type: str = "ASSET_MATERIALIZATION",
    dagster_run_id: str = "backfill-f052",
    asset_key: str | None = "extract_mineru",
    partition_key: str | None = "src_42",
    metadata: dict | None = None,
) -> dict:
    payload: dict = {
        "event_type": event_type,
        "dagster_run_id": dagster_run_id,
        "timestamp": _NOW_ISO,
    }
    if asset_key is not None:
        payload["asset_key"] = asset_key
    if partition_key is not None:
        payload["partition_key"] = partition_key
    if metadata is not None:
        payload["metadata"] = metadata
    return payload


def _make_run_row_f052(
    triggered_by: int | None = _USER_ID_F052,
) -> MagicMock:
    row = MagicMock(spec=Run)
    row.id = 201
    row.triggered_by = triggered_by
    row.kind = "extract"
    row.status = "running"
    return row


# ── T11: ASSET_MATERIALIZATION event accepted → 200 processed=True ────────────


def test_asset_materialization_event_accepted_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST valid ASSET_MATERIALIZATION with auth secret → HTTP 200, processed=True.

    Also confirms optional asset_key field: if asset_key is omitted from the
    payload (defaults to None), handler still returns 200 processed=True
    (recognized event type; not a 422 — asset_key is optional per schema).
    """
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", _TEST_SECRET)
    run_row = _make_run_row_f052()
    app.dependency_overrides[get_session] = _make_session_dep(run_row)
    broker = NotificationBroker()
    app.state.notification_broker = broker
    try:
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post(
            "/api/dagster/events",
            json=_asset_payload(),
            headers=_SECRET_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["processed"] is True
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T12: invalid event_type → 422 (existing T9 still passes) ────────────────


def test_asset_materialization_invalid_payload_returns_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST with event_type='BAD_TYPE' → 422.

    OQ-4 confirmation: T9 uses 'RUN_QUEUED' (still invalid after extending
    the Literal). This test uses 'BAD_TYPE' as an additional coverage point.
    asset_key is optional — a payload with no asset_key but valid ASSET_MATERIALIZATION
    event_type is NOT a 422 (Pydantic-OK).
    """
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", _TEST_SECRET)
    client = TestClient(app, raise_server_exceptions=True)

    # Case 1: completely invalid event_type → 422
    resp = client.post(
        "/api/dagster/events",
        json={"event_type": "BAD_TYPE", "dagster_run_id": "bf-1", "timestamp": _NOW_ISO},
        headers=_SECRET_HEADER,
    )
    assert resp.status_code == 422

    # Case 2: missing asset_key is OK (optional field) → 200 (run lookup needed)
    run_row = _make_run_row_f052()
    app.dependency_overrides[get_session] = _make_session_dep(run_row)
    broker = NotificationBroker()
    app.state.notification_broker = broker
    try:
        payload_no_key = {
            "event_type": "ASSET_MATERIALIZATION",
            "dagster_run_id": "backfill-f052",
            "timestamp": _NOW_ISO,
            # asset_key omitted (None / missing) → defaults to None
        }
        resp2 = client.post(
            "/api/dagster/events",
            json=payload_no_key,
            headers=_SECRET_HEADER,
        )
        # The handler accepts it — asset_key is optional, defaults to "" in _build_notification_event
        # which falls into the AssetMaterializedEvent branch with partition_key=None → returns None
        # (drop event) → processed=True (event recognized, just not routable)
        assert resp2.status_code == 200
        assert resp2.json()["processed"] is True
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T13: ASSET_MATERIALIZATION publishes to notification_broker ───────────────


def test_asset_materialization_publishes_to_notification_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST valid ASSET_MATERIALIZATION → notification_broker.publish called
    with correct user_id and event dict.
    """
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", _TEST_SECRET)
    run_row = _make_run_row_f052(triggered_by=_USER_ID_F052)
    app.dependency_overrides[get_session] = _make_session_dep(run_row)

    broker = NotificationBroker()
    published_calls: list[tuple[int, dict]] = []

    original_publish = broker.publish

    def _spy_publish(user_id: int, event: dict) -> None:
        published_calls.append((user_id, event))
        original_publish(user_id=user_id, event=event)

    broker.publish = _spy_publish  # type: ignore[method-assign]
    app.state.notification_broker = broker

    try:
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post(
            "/api/dagster/events",
            json=_asset_payload(
                asset_key="extract_mineru",
                partition_key="src_42",
            ),
            headers=_SECRET_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["processed"] is True
        assert len(published_calls) == 1
        called_user_id, called_event = published_calls[0]
        assert called_user_id == _USER_ID_F052
        assert called_event["type"] == "asset.materialized"
        assert called_event["asset_key"] == "extract_mineru"
        assert called_event["partition_key"] == "src_42"
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T14: ASSET_MATERIALIZATION for unknown run → processed=False ──────────────


def test_asset_materialization_unknown_run_returns_processed_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scalar_one_or_none returns None (unknown run) → HTTP 200, processed=False."""
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", _TEST_SECRET)
    # Session returns None for the Run lookup
    app.dependency_overrides[get_session] = _make_session_dep(None)
    broker = NotificationBroker()
    app.state.notification_broker = broker
    try:
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post(
            "/api/dagster/events",
            json=_asset_payload(dagster_run_id="totally-unknown-run-999"),
            headers=_SECRET_HEADER,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["processed"] is False
        assert body["reason"] == "unknown_run"
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── T15: malformed partition_key → skips publish, HTTP 200 ───────────────────


def test_asset_materialization_malformed_partition_key_skips_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST ASSET_MATERIALIZATION with asset_key='chunks' and
    partition_key='BAD_FORMAT' → HTTP 200, notification_broker.publish NOT called.

    Also tests partition_key=null variant for completeness.
    (L1 fix / OQ-6 defensive ValueError path — T15 per agreed.md §10.)
    """
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", _TEST_SECRET)

    published_calls: list = []

    for partition_key_val in ["BAD_FORMAT", None]:
        run_row = _make_run_row_f052()
        app.dependency_overrides[get_session] = _make_session_dep(run_row)
        broker = NotificationBroker()

        original_publish = broker.publish

        def _spy_publish(user_id: int, event: dict, _calls: list = published_calls) -> None:
            _calls.append((user_id, event))
            original_publish(user_id=user_id, event=event)

        broker.publish = _spy_publish  # type: ignore[method-assign]
        app.state.notification_broker = broker

        payload: dict = {
            "event_type": "ASSET_MATERIALIZATION",
            "dagster_run_id": "backfill-f052",
            "timestamp": _NOW_ISO,
            "asset_key": "chunks",
            "metadata": {"chunk_count": 5},
        }
        if partition_key_val is not None:
            payload["partition_key"] = partition_key_val
        # (If partition_key_val is None, omit the key — tests null/missing case)

        try:
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                "/api/dagster/events",
                json=payload,
                headers=_SECRET_HEADER,
            )
            assert resp.status_code == 200, (
                f"Expected 200 for partition_key={partition_key_val!r}, got {resp.status_code}"
            )
            assert resp.json()["processed"] is True
            assert published_calls == [], (
                f"Expected publish NOT called for partition_key={partition_key_val!r}, "
                f"but got {published_calls}"
            )
        finally:
            app.dependency_overrides.pop(get_session, None)
            published_calls.clear()


# ── T16: notification_broker.publish exception → HTTP 200 still returned ─────


def test_notification_broker_publish_exception_still_returns_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mock notification_broker.publish to raise Exception("boom");
    POST valid ASSET_MATERIALIZATION → HTTP 200, no exception propagated.

    (L2 / §8a invariant: try/except in dagster_events.py wraps publish call.)
    """
    monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", _TEST_SECRET)
    run_row = _make_run_row_f052()
    app.dependency_overrides[get_session] = _make_session_dep(run_row)

    broker = NotificationBroker()

    def _raising_publish(user_id: int, event: dict) -> None:
        raise Exception("boom — broker explosion")

    broker.publish = _raising_publish  # type: ignore[method-assign]
    app.state.notification_broker = broker

    try:
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/dagster/events",
            json=_asset_payload(),
            headers=_SECRET_HEADER,
        )
        # Despite broker.publish raising, the handler must return 200.
        assert resp.status_code == 200
        assert resp.json()["processed"] is True
    finally:
        app.dependency_overrides.pop(get_session, None)
