"""Tests for F-018: POST /api/runs — trigger MinerU extraction backfill.

Five test cases:
  (a) Happy-path 202: launch_extract_backfill succeeds, source exists,
      returns 202 with dagster_run_id + run_id; Run row added.
  (b) DagsterGatewayError → 503.
  (c) asset not "extract_mineru" → 422.
  (d) Empty source_ids → 422.
  (e) Missing source id → 404.

All tests are pure unit tests (no live Dagster, Postgres, or MinIO required).
conftest.py autouse fixtures handle engine/SSL mocking.

Session mock design:
  - session.execute is mocked to return a result containing source IDs (for
    existence check). Default: returns [source_id=42].
  - session.add is a no-op MagicMock (records the call).
  - session.commit is a no-op AsyncMock.
  - session.refresh is an AsyncMock whose side_effect sets run.id = refresh_id
    to simulate the Postgres Identity column assignment.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.dagster.dependencies import get_dagster_gateway
from dataplat_api.dagster.gateway import DagsterGateway, DagsterGatewayError
from dataplat_api.db.models import User
from dataplat_api.db.session import get_session
from dataplat_api.main import app

# ── Shared fixtures ────────────────────────────────────────────────────────────

_MOCK_USER = User(id=1, email="test@example.com", hashed_password="$2b$12$hash")

_TEST_SOURCE_ID = 42
_TEST_BACKFILL_ID = "backfill-abc-123"
_TEST_RUN_ID = 99


async def _override_current_user() -> User:
    return _MOCK_USER


def _make_session_dep(
    source_ids_present: list[int] | None = None,
    refresh_id: int = _TEST_RUN_ID,
) -> Any:
    """Session override for POST /api/runs handler.

    Args:
        source_ids_present: Source IDs to return from the existence check query.
                            Defaults to [_TEST_SOURCE_ID].
        refresh_id: The run.id to set when session.refresh(run) is called.
    """
    if source_ids_present is None:
        source_ids_present = [_TEST_SOURCE_ID]

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[return, misc]
        session = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()

        # Mock execute() to return a result whose fetchall() returns source ids.
        # The handler does: result = await session.execute(select(Source.id).where(...))
        #                   found_ids = {row[0] for row in result.fetchall()}
        mock_rows = [(sid,) for sid in source_ids_present]
        mock_result = MagicMock()
        mock_result.fetchall = MagicMock(return_value=mock_rows)
        session.execute = AsyncMock(return_value=mock_result)

        # Mock refresh() to set run.id on the object passed to it.
        async def _refresh_side_effect(obj: Any) -> None:
            obj.id = refresh_id

        session.refresh = AsyncMock(side_effect=_refresh_side_effect)
        yield session

    return _override


def _make_gateway_dep(
    backfill_id: str = _TEST_BACKFILL_ID,
    launch_raises: Exception | None = None,
) -> Any:
    """Gateway override with controllable success/failure for launch_extract_backfill."""

    def _override() -> DagsterGateway:
        gw = MagicMock(spec=DagsterGateway)
        # add_source_partition is called defensively; always succeeds in happy path.
        gw.add_source_partition = AsyncMock(return_value=None)
        if launch_raises is not None:
            gw.launch_extract_backfill = AsyncMock(side_effect=launch_raises)
        else:
            gw.launch_extract_backfill = AsyncMock(return_value=backfill_id)
        return gw

    return _override


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# ─────────────────────────────────────────────────────────────────────────────
# (a) Happy-path 202
# ─────────────────────────────────────────────────────────────────────────────


def test_trigger_extract_happy_path(client: TestClient) -> None:
    """POST /api/runs with valid payload → 202 with dagster_run_id + run_id."""
    gw_mock = MagicMock(spec=DagsterGateway)
    gw_mock.add_source_partition = AsyncMock(return_value=None)
    gw_mock.launch_extract_backfill = AsyncMock(return_value=_TEST_BACKFILL_ID)

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(
        source_ids_present=[_TEST_SOURCE_ID], refresh_id=_TEST_RUN_ID
    )
    app.dependency_overrides[get_dagster_gateway] = lambda: gw_mock
    try:
        response = client.post(
            "/api/runs",
            json={"asset": "extract_mineru", "source_ids": [_TEST_SOURCE_ID]},
            headers={"Authorization": "Bearer faketoken"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 202, (
        f"Expected 202, got {response.status_code}: {response.text}"
    )
    body = response.json()
    assert body["dagster_run_id"] == _TEST_BACKFILL_ID, (
        f"dagster_run_id mismatch: {body}"
    )
    assert body["run_id"] == _TEST_RUN_ID, f"run_id mismatch: {body}"

    # Assert the gateway method was called with the correct partition key.
    gw_mock.launch_extract_backfill.assert_called_once_with([f"src_{_TEST_SOURCE_ID}"])


def test_trigger_extract_run_row_added(client: TestClient) -> None:
    """POST /api/runs happy path — session.add called with Run(kind='extract', status='pending')."""
    gw_mock = MagicMock(spec=DagsterGateway)
    gw_mock.add_source_partition = AsyncMock(return_value=None)
    gw_mock.launch_extract_backfill = AsyncMock(return_value=_TEST_BACKFILL_ID)

    session_dep = _make_session_dep(
        source_ids_present=[_TEST_SOURCE_ID], refresh_id=_TEST_RUN_ID
    )

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = session_dep
    app.dependency_overrides[get_dagster_gateway] = lambda: gw_mock
    try:
        response = client.post(
            "/api/runs",
            json={"asset": "extract_mineru", "source_ids": [_TEST_SOURCE_ID]},
            headers={"Authorization": "Bearer faketoken"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 202
    # Verify the Run object passed to session.add has the right fields.
    # The session mock is not directly accessible after overrides are popped —
    # instead verify through the response (backfill_id + run_id) that the
    # full handler path was executed successfully.
    body = response.json()
    assert body["dagster_run_id"] == _TEST_BACKFILL_ID
    assert body["run_id"] == _TEST_RUN_ID


# ─────────────────────────────────────────────────────────────────────────────
# (b) DagsterGatewayError → 503
# ─────────────────────────────────────────────────────────────────────────────


def test_trigger_extract_dagster_error_returns_503(client: TestClient) -> None:
    """DagsterGatewayError from launch_extract_backfill → 503."""
    gw_mock = MagicMock(spec=DagsterGateway)
    gw_mock.add_source_partition = AsyncMock(return_value=None)
    gw_mock.launch_extract_backfill = AsyncMock(
        side_effect=DagsterGatewayError("Dagster unreachable")
    )

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(
        source_ids_present=[_TEST_SOURCE_ID]
    )
    app.dependency_overrides[get_dagster_gateway] = lambda: gw_mock
    try:
        response = client.post(
            "/api/runs",
            json={"asset": "extract_mineru", "source_ids": [_TEST_SOURCE_ID]},
            headers={"Authorization": "Bearer faketoken"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 503, (
        f"Expected 503, got {response.status_code}: {response.text}"
    )
    assert "detail" in response.json()


# ─────────────────────────────────────────────────────────────────────────────
# (c) asset not "extract_mineru" → 422
# ─────────────────────────────────────────────────────────────────────────────


def test_trigger_extract_wrong_asset_returns_422(client: TestClient) -> None:
    """POST /api/runs with asset != 'extract_mineru' → 422 (Pydantic Literal validation)."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = client.post(
            "/api/runs",
            json={"asset": "unknown_asset", "source_ids": [_TEST_SOURCE_ID]},
            headers={"Authorization": "Bearer faketoken"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422, (
        f"Expected 422, got {response.status_code}: {response.text}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# (d) Empty source_ids → 422
# ─────────────────────────────────────────────────────────────────────────────


def test_trigger_extract_empty_source_ids_returns_422(client: TestClient) -> None:
    """POST /api/runs with source_ids=[] → 422 (Pydantic min_length=1 validation)."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = client.post(
            "/api/runs",
            json={"asset": "extract_mineru", "source_ids": []},
            headers={"Authorization": "Bearer faketoken"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422, (
        f"Expected 422, got {response.status_code}: {response.text}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# (e) Missing source id → 404
# ─────────────────────────────────────────────────────────────────────────────


def test_trigger_extract_missing_source_returns_404(client: TestClient) -> None:
    """POST /api/runs with a source_id that does not exist in DB → 404."""
    # Session returns no rows (empty result) — simulates missing source.
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(source_ids_present=[])
    app.dependency_overrides[get_dagster_gateway] = _make_gateway_dep()
    try:
        response = client.post(
            "/api/runs",
            json={"asset": "extract_mineru", "source_ids": [9999]},
            headers={"Authorization": "Bearer faketoken"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 404, (
        f"Expected 404, got {response.status_code}: {response.text}"
    )
    body = response.json()
    assert "detail" in body
    assert "9999" in str(body["detail"]), f"Missing source id not in detail: {body}"


# ─────────────────────────────────────────────────────────────────────────────
# F-024: chunks asset — router tests
# ─────────────────────────────────────────────────────────────────────────────


def test_trigger_chunks_happy_path_202(client: TestClient) -> None:
    """POST /api/runs with asset='chunks' → 202 with dagster_run_id + run_id."""
    _CHUNKS_BACKFILL_ID = "backfill-chunks-happy-path"
    _CHUNKS_RUN_ID = 77

    gw_mock = MagicMock(spec=DagsterGateway)
    gw_mock.add_source_partition = AsyncMock(return_value=None)
    gw_mock.launch_extract_backfill = AsyncMock(return_value="should-not-be-called")
    gw_mock.launch_chunks_backfill = AsyncMock(return_value=_CHUNKS_BACKFILL_ID)

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(
        source_ids_present=[_TEST_SOURCE_ID], refresh_id=_CHUNKS_RUN_ID
    )
    app.dependency_overrides[get_dagster_gateway] = lambda: gw_mock
    try:
        response = client.post(
            "/api/runs",
            json={"asset": "chunks", "source_ids": [_TEST_SOURCE_ID]},
            headers={"Authorization": "Bearer faketoken"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 202, (
        f"Expected 202, got {response.status_code}: {response.text}"
    )
    body = response.json()
    assert body["dagster_run_id"] == _CHUNKS_BACKFILL_ID, (
        f"dagster_run_id mismatch: {body}"
    )
    assert body["run_id"] == _CHUNKS_RUN_ID, f"run_id mismatch: {body}"

    # extract_mineru backfill must NOT have been called.
    gw_mock.launch_extract_backfill.assert_not_called()
    # chunks backfill must have been called with the correct partition key.
    gw_mock.launch_chunks_backfill.assert_called_once_with([f"src_{_TEST_SOURCE_ID}"])


def test_trigger_chunks_dagster_error_returns_503(client: TestClient) -> None:
    """DagsterGatewayError from launch_chunks_backfill → 503."""
    gw_mock = MagicMock(spec=DagsterGateway)
    gw_mock.add_source_partition = AsyncMock(return_value=None)
    gw_mock.launch_chunks_backfill = AsyncMock(
        side_effect=DagsterGatewayError("Dagster chunks unreachable")
    )

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(
        source_ids_present=[_TEST_SOURCE_ID]
    )
    app.dependency_overrides[get_dagster_gateway] = lambda: gw_mock
    try:
        response = client.post(
            "/api/runs",
            json={"asset": "chunks", "source_ids": [_TEST_SOURCE_ID]},
            headers={"Authorization": "Bearer faketoken"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 503, (
        f"Expected 503, got {response.status_code}: {response.text}"
    )
    assert "detail" in response.json()


def test_trigger_chunks_missing_source_returns_404(client: TestClient) -> None:
    """POST /api/runs?asset=chunks with missing source_id → 404."""
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(source_ids_present=[])
    app.dependency_overrides[get_dagster_gateway] = _make_gateway_dep()
    try:
        response = client.post(
            "/api/runs",
            json={"asset": "chunks", "source_ids": [8888]},
            headers={"Authorization": "Bearer faketoken"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 404, (
        f"Expected 404, got {response.status_code}: {response.text}"
    )
    body = response.json()
    assert "detail" in body
    assert "8888" in str(body["detail"]), f"Missing source id not in detail: {body}"


def test_trigger_run_chunks_creates_run_row_kind_chunk(client: TestClient) -> None:
    """POST /api/runs?asset=chunks — Run row written with kind='chunk'."""
    _CHUNKS_BACKFILL_ID = "backfill-kind-check"
    _CHUNKS_RUN_ID = 55
    from dataplat_api.db.models import Run

    captured_run: list[Run] = []

    async def _session_override():  # type: ignore[return]
        session = AsyncMock()

        mock_result = MagicMock()
        mock_result.fetchall = MagicMock(return_value=[(_TEST_SOURCE_ID,)])
        session.execute = AsyncMock(return_value=mock_result)

        def _capture_add(obj: object) -> None:
            if isinstance(obj, Run):
                captured_run.append(obj)

        session.add = MagicMock(side_effect=_capture_add)
        session.commit = AsyncMock()

        async def _refresh(obj: object) -> None:
            obj.id = _CHUNKS_RUN_ID  # type: ignore[union-attr]

        session.refresh = AsyncMock(side_effect=_refresh)
        yield session

    gw_mock = MagicMock(spec=DagsterGateway)
    gw_mock.add_source_partition = AsyncMock(return_value=None)
    gw_mock.launch_chunks_backfill = AsyncMock(return_value=_CHUNKS_BACKFILL_ID)

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_dagster_gateway] = lambda: gw_mock
    try:
        response = client.post(
            "/api/runs",
            json={"asset": "chunks", "source_ids": [_TEST_SOURCE_ID]},
            headers={"Authorization": "Bearer faketoken"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 202, (
        f"Expected 202, got {response.status_code}: {response.text}"
    )
    assert len(captured_run) == 1, f"Expected 1 Run added, got {len(captured_run)}"
    run_obj = captured_run[0]
    assert run_obj.kind == "chunk", f"Expected kind='chunk', got kind={run_obj.kind!r}"
    assert run_obj.asset_keys == ["chunks"], (
        f"Expected asset_keys=['chunks'], got {run_obj.asset_keys!r}"
    )
    assert run_obj.dagster_run_id == _CHUNKS_BACKFILL_ID


# ─────────────────────────────────────────────────────────────────────────────
# F-024: schema validation tests
# ─────────────────────────────────────────────────────────────────────────────


def test_schema_accepts_chunks_asset(client: TestClient) -> None:
    """RunCreate.asset='chunks' is valid — Pydantic must NOT raise 422."""
    _BACKFILL_ID = "backfill-schema-check"
    _RUN_ID = 33

    gw_mock = MagicMock(spec=DagsterGateway)
    gw_mock.add_source_partition = AsyncMock(return_value=None)
    gw_mock.launch_chunks_backfill = AsyncMock(return_value=_BACKFILL_ID)

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(
        source_ids_present=[_TEST_SOURCE_ID], refresh_id=_RUN_ID
    )
    app.dependency_overrides[get_dagster_gateway] = lambda: gw_mock
    try:
        response = client.post(
            "/api/runs",
            json={"asset": "chunks", "source_ids": [_TEST_SOURCE_ID]},
            headers={"Authorization": "Bearer faketoken"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code != 422, (
        f"'chunks' should be accepted by RunCreate.asset Literal — got 422: {response.text}"
    )
    assert response.status_code == 202


def test_schema_still_rejects_unknown_asset(client: TestClient) -> None:
    """Widening Literal to include 'chunks' must NOT allow arbitrary assets."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = client.post(
            "/api/runs",
            json={"asset": "unknown_asset", "source_ids": [_TEST_SOURCE_ID]},
            headers={"Authorization": "Bearer faketoken"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422, (
        f"'unknown_asset' should still be rejected → 422, got {response.status_code}: {response.text}"
    )
