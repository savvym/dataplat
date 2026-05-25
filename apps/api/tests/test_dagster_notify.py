"""Tests for F-012: Dagster partition + materialization notification.

Two test groups:
  A. Gateway unit tests — test add_source_partition() and
     report_source_materialization() in isolation using a mocked httpx client.
  B. Handler integration tests — test upload_source() best-effort notify path
     using app.dependency_overrides for session, S3, and gateway.

All tests are pure unit tests (no live Dagster, Postgres, or MinIO required).
conftest.py autouse fixtures handle engine/SSL mocking.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.dagster.dependencies import get_dagster_gateway
from dataplat_api.dagster.gateway import (
    DagsterGateway,
    DagsterGatewayError,
)
from dataplat_api.db.models import User
from dataplat_api.db.session import get_session
from dataplat_api.main import app
from dataplat_api.storage.s3 import get_s3_client

# ── Shared fixtures ────────────────────────────────────────────────────────────

_MOCK_USER = User(id=1, email="test@example.com", hashed_password="$2b$12$hash")

_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type /Pages /Kids[3 0 R] /Count 1>>endobj\n"
    b"3 0 obj<</Type /Page /MediaBox[0 0 612 792] /Parent 2 0 R>>endobj\n"
    b"xref\n0 4\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000115 00000 n \n"
    b"trailer<</Size 4 /Root 1 0 R>>\n"
    b"startxref\n182\n%%EOF\n"
)


async def _override_current_user() -> User:
    return _MOCK_USER


def _make_session_dep(flush_id: int = 7) -> Any:
    """Session override: flush sets source.id; commit is a no-op AsyncMock."""

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.add = MagicMock()

        def _flush_side_effect() -> None:
            added_obj = session.add.call_args[0][0]
            added_obj.id = flush_id

        session.flush = AsyncMock(side_effect=_flush_side_effect)
        session.commit = AsyncMock()
        yield session

    return _override


def _make_s3_dep() -> Any:
    """S3 override: put_object is a no-op AsyncMock."""

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        s3_mock = AsyncMock()
        s3_mock.put_object = AsyncMock(return_value={})
        yield s3_mock

    return _override


def _make_gateway_dep(
    partition_raises: Exception | None = None,
    mat_raises: Exception | None = None,
) -> Any:
    """Gateway override with controllable success/failure for each method."""

    def _override() -> DagsterGateway:
        gw = MagicMock(spec=DagsterGateway)
        gw.add_source_partition = AsyncMock(
            side_effect=partition_raises if partition_raises else None
        )
        gw.report_source_materialization = AsyncMock(
            side_effect=mat_raises if mat_raises else None
        )
        return gw

    return _override


def _post_upload(
    client: TestClient,
    pdf_bytes: bytes = _MINIMAL_PDF,
    content_type: str = "application/pdf",
) -> Any:
    return client.post(
        "/api/sources/upload",
        files={"file": ("test.pdf", pdf_bytes, content_type)},
        headers={"Authorization": "Bearer faketoken"},
    )


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# ─────────────────────────────────────────────────────────────────────────────
# A. Gateway unit tests
# ─────────────────────────────────────────────────────────────────────────────

def _make_mock_response(json_body: dict, status_code: int = 200) -> httpx.Response:
    """Build a fake httpx.Response for DagsterGateway unit tests."""
    import json
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(json_body).encode(),
        headers={"content-type": "application/json"},
    )


def _gateway_with_response(json_body: dict, status_code: int = 200) -> DagsterGateway:
    """Return a DagsterGateway whose _client.post is mocked to return json_body."""
    gw = DagsterGateway(graphql_url="http://test/graphql")
    gw._client = AsyncMock()
    gw._client.post = AsyncMock(
        return_value=_make_mock_response(json_body, status_code)
    )
    return gw


# ── add_source_partition ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_source_partition_success() -> None:
    """AddDynamicPartitionSuccess → returns None without raising."""
    gw = _gateway_with_response({
        "data": {
            "addDynamicPartition": {
                "__typename": "AddDynamicPartitionSuccess",
                "partitionKey": "src_1",
                "partitionsDefName": "sources",
            }
        }
    })
    result = await gw.add_source_partition("src_1")
    assert result is None


@pytest.mark.asyncio
async def test_add_source_partition_duplicate_is_noop() -> None:
    """DuplicateDynamicPartitionError → returns None (idempotent, no exception)."""
    gw = _gateway_with_response({
        "data": {
            "addDynamicPartition": {
                "__typename": "DuplicateDynamicPartitionError",
                "partitionsDefName": "sources",
                "partitionName": "src_1",
                "message": "Partition src_1 already exists",
            }
        }
    })
    result = await gw.add_source_partition("src_1")
    assert result is None


@pytest.mark.asyncio
async def test_add_source_partition_unauthorized_raises() -> None:
    """UnauthorizedError → raises DagsterGatewayError (partition def not loaded)."""
    gw = _gateway_with_response({
        "data": {
            "addDynamicPartition": {
                "__typename": "UnauthorizedError",
                "message": "The repository does not contain a dynamic partitions definition with the given name.",
            }
        }
    })
    with pytest.raises(DagsterGatewayError, match="UnauthorizedError"):
        await gw.add_source_partition("src_1")


@pytest.mark.asyncio
async def test_add_source_partition_python_error_raises() -> None:
    """PythonError → raises DagsterGatewayError."""
    gw = _gateway_with_response({
        "data": {
            "addDynamicPartition": {
                "__typename": "PythonError",
                "message": "Something went wrong in Dagster",
            }
        }
    })
    with pytest.raises(DagsterGatewayError, match="PythonError"):
        await gw.add_source_partition("src_1")


@pytest.mark.asyncio
async def test_add_source_partition_connect_error_raises() -> None:
    """httpx.ConnectError → raises DagsterGatewayError."""
    gw = DagsterGateway(graphql_url="http://test/graphql")
    gw._client = AsyncMock()
    gw._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(DagsterGatewayError, match="Cannot connect"):
        await gw.add_source_partition("src_1")


@pytest.mark.asyncio
async def test_add_source_partition_timeout_raises() -> None:
    """httpx.TimeoutException → raises DagsterGatewayError."""
    gw = DagsterGateway(graphql_url="http://test/graphql")
    gw._client = AsyncMock()
    gw._client.post = AsyncMock(
        side_effect=httpx.TimeoutException("timeout", request=MagicMock())
    )
    with pytest.raises(DagsterGatewayError, match="timed out"):
        await gw.add_source_partition("src_1")


@pytest.mark.asyncio
async def test_add_source_partition_http_error_raises() -> None:
    """httpx.HTTPError (other network failure) → raises DagsterGatewayError."""
    gw = DagsterGateway(graphql_url="http://test/graphql")
    gw._client = AsyncMock()
    gw._client.post = AsyncMock(
        side_effect=httpx.HTTPError("generic http error")
    )
    with pytest.raises(DagsterGatewayError, match="HTTP error"):
        await gw.add_source_partition("src_1")


@pytest.mark.asyncio
async def test_add_source_partition_non_2xx_raises() -> None:
    """HTTP 503 response → raises DagsterGatewayError."""
    gw = DagsterGateway(graphql_url="http://test/graphql")
    gw._client = AsyncMock()
    gw._client.post = AsyncMock(
        return_value=httpx.Response(503, content=b"Service Unavailable")
    )
    with pytest.raises(DagsterGatewayError, match="HTTP 503"):
        await gw.add_source_partition("src_1")


@pytest.mark.asyncio
async def test_add_source_partition_graphql_errors_raises() -> None:
    """Top-level GraphQL errors key → raises DagsterGatewayError."""
    gw = _gateway_with_response({"errors": [{"message": "syntax error"}]})
    with pytest.raises(DagsterGatewayError, match="GraphQL error"):
        await gw.add_source_partition("src_1")


# ── report_source_materialization ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_report_source_materialization_success() -> None:
    """ReportRunlessAssetEventsSuccess → returns None without raising."""
    gw = _gateway_with_response({
        "data": {
            "reportRunlessAssetEvents": {
                "__typename": "ReportRunlessAssetEventsSuccess",
                "assetKey": {"path": ["source"]},
            }
        }
    })
    result = await gw.report_source_materialization(
        partition_key="src_7",
        storage_uri="s3://sources/7/original.pdf",
        size_bytes=1234,
    )
    assert result is None


@pytest.mark.asyncio
async def test_report_source_materialization_unauthorized_raises() -> None:
    """UnauthorizedError → raises DagsterGatewayError."""
    gw = _gateway_with_response({
        "data": {
            "reportRunlessAssetEvents": {
                "__typename": "UnauthorizedError",
                "message": "not allowed",
            }
        }
    })
    with pytest.raises(DagsterGatewayError, match="UnauthorizedError"):
        await gw.report_source_materialization("src_7", "s3://sources/7/original.pdf", 1234)


@pytest.mark.asyncio
async def test_report_source_materialization_python_error_raises() -> None:
    """PythonError → raises DagsterGatewayError."""
    gw = _gateway_with_response({
        "data": {
            "reportRunlessAssetEvents": {
                "__typename": "PythonError",
                "message": "dagster crash",
            }
        }
    })
    with pytest.raises(DagsterGatewayError, match="PythonError"):
        await gw.report_source_materialization("src_7", "s3://sources/7/original.pdf", 1234)


@pytest.mark.asyncio
async def test_report_source_materialization_connect_error_raises() -> None:
    """httpx.ConnectError → raises DagsterGatewayError."""
    gw = DagsterGateway(graphql_url="http://test/graphql")
    gw._client = AsyncMock()
    gw._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(DagsterGatewayError, match="Cannot connect"):
        await gw.report_source_materialization("src_7", "s3://sources/7/original.pdf", 1234)


@pytest.mark.asyncio
async def test_report_source_materialization_payload_shape() -> None:
    """POST payload has correct eventType, assetKey path, partitionKeys."""
    gw = DagsterGateway(graphql_url="http://test/graphql")
    gw._client = AsyncMock()
    gw._client.post = AsyncMock(
        return_value=_make_mock_response({
            "data": {
                "reportRunlessAssetEvents": {
                    "__typename": "ReportRunlessAssetEventsSuccess",
                    "assetKey": {"path": ["source"]},
                }
            }
        })
    )
    await gw.report_source_materialization(
        partition_key="src_42",
        storage_uri="s3://sources/42/original.pdf",
        size_bytes=9999,
    )

    call_kwargs = gw._client.post.call_args
    # post(url, json=payload)
    payload = call_kwargs.kwargs.get("json") or call_kwargs.args[1]
    variables = payload["variables"]["params"]
    assert variables["eventType"] == "ASSET_MATERIALIZATION"
    assert variables["assetKey"] == {"path": ["source"]}
    assert variables["partitionKeys"] == ["src_42"]
    assert "s3://sources/42/original.pdf" in variables["description"]


# ─────────────────────────────────────────────────────────────────────────────
# B. Handler integration tests — upload_source best-effort notify
# ─────────────────────────────────────────────────────────────────────────────


def test_upload_notify_calls_gateway_methods(client: TestClient) -> None:
    """Successful upload → add_source_partition and report_source_materialization called."""
    gw_mock = MagicMock(spec=DagsterGateway)
    gw_mock.add_source_partition = AsyncMock()
    gw_mock.report_source_materialization = AsyncMock()

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(flush_id=7)
    app.dependency_overrides[get_s3_client] = _make_s3_dep()
    app.dependency_overrides[get_dagster_gateway] = lambda: gw_mock
    try:
        response = _post_upload(client)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 201
    gw_mock.add_source_partition.assert_called_once_with("src_7")
    gw_mock.report_source_materialization.assert_called_once()


def test_upload_notify_partition_key_format(client: TestClient) -> None:
    """Partition key passed to gateway matches ^src_[0-9]+$."""
    import re

    gw_mock = MagicMock(spec=DagsterGateway)
    gw_mock.add_source_partition = AsyncMock()
    gw_mock.report_source_materialization = AsyncMock()

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(flush_id=42)
    app.dependency_overrides[get_s3_client] = _make_s3_dep()
    app.dependency_overrides[get_dagster_gateway] = lambda: gw_mock
    try:
        response = _post_upload(client)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 201
    pk = gw_mock.add_source_partition.call_args[0][0]
    assert re.match(r"^src_[0-9]+$", pk), f"bad partition key format: {pk!r}"
    assert pk == "src_42"


def test_upload_notify_report_mat_gets_storage_uri(client: TestClient) -> None:
    """report_source_materialization receives correct storage_uri."""
    gw_mock = MagicMock(spec=DagsterGateway)
    gw_mock.add_source_partition = AsyncMock()
    gw_mock.report_source_materialization = AsyncMock()

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(flush_id=42)
    app.dependency_overrides[get_s3_client] = _make_s3_dep()
    app.dependency_overrides[get_dagster_gateway] = lambda: gw_mock
    try:
        response = _post_upload(client)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 201
    call_kwargs = gw_mock.report_source_materialization.call_args
    assert call_kwargs.kwargs["storage_uri"] == "s3://sources/42/original.pdf"
    assert call_kwargs.kwargs["partition_key"] == "src_42"


def test_upload_returns_201_even_if_add_partition_fails(client: TestClient) -> None:
    """add_source_partition raises DagsterGatewayError → handler still returns 201."""
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(flush_id=7)
    app.dependency_overrides[get_s3_client] = _make_s3_dep()
    app.dependency_overrides[get_dagster_gateway] = _make_gateway_dep(
        partition_raises=DagsterGatewayError("Dagster down")
    )
    try:
        response = _post_upload(client)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == 7
    assert body["storage_uri"] == "s3://sources/7/original.pdf"


def test_upload_returns_201_even_if_report_mat_fails(client: TestClient) -> None:
    """report_source_materialization raises DagsterGatewayError → handler still returns 201."""
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(flush_id=7)
    app.dependency_overrides[get_s3_client] = _make_s3_dep()
    app.dependency_overrides[get_dagster_gateway] = _make_gateway_dep(
        mat_raises=DagsterGatewayError("Dagster event log down")
    )
    try:
        response = _post_upload(client)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 201


def test_upload_calls_report_mat_even_if_add_partition_fails(client: TestClient) -> None:
    """Even when add_source_partition fails, report_source_materialization is still called."""
    gw_mock = MagicMock(spec=DagsterGateway)
    gw_mock.add_source_partition = AsyncMock(
        side_effect=DagsterGatewayError("partition def missing")
    )
    gw_mock.report_source_materialization = AsyncMock()

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(flush_id=7)
    app.dependency_overrides[get_s3_client] = _make_s3_dep()
    app.dependency_overrides[get_dagster_gateway] = lambda: gw_mock
    try:
        response = _post_upload(client)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 201
    # report_source_materialization MUST still have been called despite the earlier failure.
    gw_mock.report_source_materialization.assert_called_once()


def test_upload_notify_called_after_commit(client: TestClient) -> None:
    """session.commit() is called BEFORE gateway.add_source_partition()."""
    call_order: list[str] = []

    async def _ordered_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.add = MagicMock()

        def _flush_side_effect() -> None:
            added_obj = session.add.call_args[0][0]
            added_obj.id = 7

        session.flush = AsyncMock(side_effect=_flush_side_effect)

        async def _commit_side_effect() -> None:
            call_order.append("commit")

        session.commit = AsyncMock(side_effect=_commit_side_effect)
        yield session

    async def _ordered_add_partition(partition_key: str) -> None:
        call_order.append("add_source_partition")

    gw_mock = MagicMock(spec=DagsterGateway)
    gw_mock.add_source_partition = AsyncMock(side_effect=_ordered_add_partition)
    gw_mock.report_source_materialization = AsyncMock()

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _ordered_session
    app.dependency_overrides[get_s3_client] = _make_s3_dep()
    app.dependency_overrides[get_dagster_gateway] = lambda: gw_mock
    try:
        response = _post_upload(client)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 201
    assert call_order == ["commit", "add_source_partition"], (
        f"Expected commit before add_source_partition; got: {call_order}"
    )
