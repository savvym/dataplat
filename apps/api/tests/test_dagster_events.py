"""Tests for POST /api/dagster/events — S050-F-050.

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
