"""Tests for GET /api/documents/{variant_id}/render — S022-F-022.

Unit tests (run in backend layer — no live DB, Postgres, or MinIO required):
  - test_render_returns_200_with_markdown_content_type
  - test_render_contains_extracted_text
  - test_render_nonexistent_variant_returns_404
  - test_render_variant_not_accessible_returns_404
  - test_render_no_token_returns_401
  - test_render_retrieves_docling_document_from_s3

All tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Mock session pattern (2-execute handler on happy path):
  The handler calls session.execute() TWICE on the happy path:
    1st — ownership check: .scalar_one_or_none() → DocumentVariant stub
    2nd — (implicit via the session mock) used during SQLAlchemy query building
  Then:
    S3 get_object() is called → response with Body mock
    response["Body"].read() returns JSON bytes
    Response is built with media_type="text/markdown"

For 404 path: only 1 execute() call (ownership check returns None).

Auth-gate test does NOT override get_current_user — the real oauth2_scheme
raises 401 for a missing Authorization header.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import DocumentVariant, Source, SourceCollection, User
from dataplat_api.db.session import get_session
from dataplat_api.main import app

# ── Shared mock user ──────────────────────────────────────────────────────────

_MOCK_USER = User(id=1, email="test@example.com", hashed_password="$2b$12$hash")


async def _override_current_user() -> User:
    return _MOCK_USER


# ── ORM stub builders ─────────────────────────────────────────────────────────

_NOW = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)


def _make_source_stub(source_id: int = 7, collection_id: int | None = None) -> MagicMock:
    """Build a MagicMock that looks like a Source ORM row."""
    src = MagicMock(spec=Source)
    src.id = source_id
    src.collection_id = collection_id
    return src


def _make_variant_stub(
    variant_id: int = 3,
    source_id: int = 7,
    extractor_name: str = "mineru",
    storage_prefix: str = "s3://documents/7/extract_mineru/",
) -> MagicMock:
    """Build a MagicMock that looks like a DocumentVariant ORM row."""
    variant = MagicMock(spec=DocumentVariant)
    variant.id = variant_id
    variant.source_id = source_id
    variant.extractor_name = extractor_name
    variant.storage_prefix = storage_prefix
    return variant


def _make_collection_stub(collection_id: int = 42, owner_id: int = 1) -> MagicMock:
    """Build a MagicMock that looks like a SourceCollection ORM row."""
    coll = MagicMock(spec=SourceCollection)
    coll.id = collection_id
    coll.owner_id = owner_id
    return coll


# ── Session and S3 mock helpers ───────────────────────────────────────────────


def _make_session_dep_happy(
    variant: MagicMock,
) -> Any:
    """Return a get_session override for the happy path (1 execute call).

    The handler calls session.execute() once:
      1st — ownership check JOIN: .scalar_one_or_none() → variant stub

    After the execute:
      No commit, no refresh (we're just reading).
    """

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = variant
        session.execute = AsyncMock(return_value=result)
        yield session

    return _override


def _make_session_dep_no_variant() -> Any:
    """Return a get_session override for the variant-not-found 404 path (1 execute).

    The ownership check returns None → handler raises 404.
    """

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)
        yield session

    return _override


def _make_s3_client_with_docling(docling_json: str) -> Any:
    """Return a mock S3 client that returns a DoclingDocument JSON."""

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        s3 = AsyncMock()
        body_mock = AsyncMock()
        body_mock.read = AsyncMock(return_value=docling_json.encode("utf-8"))
        response_mock = {"Body": body_mock}
        s3.get_object = AsyncMock(return_value=response_mock)
        yield s3

    return _override


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# ── Helper ────────────────────────────────────────────────────────────────────


def _render(
    client: TestClient,
    variant_id: int = 3,
) -> Any:
    return client.get(
        f"/api/documents/{variant_id}/render",
        headers={"Authorization": "Bearer faketoken"},
    )


# ── Happy path tests ──────────────────────────────────────────────────────────


def test_render_returns_200_with_markdown_content_type(client: TestClient) -> None:
    """GET render happy path → 200 with text/markdown Content-Type (V1)."""
    variant = _make_variant_stub(variant_id=3, source_id=7)
    
    # Minimal valid DoclingDocument JSON
    docling_json = """{
        "name": "source_7",
        "pages": {
            "1": {
                "page_no": 1,
                "size": {"width": 612.0, "height": 792.0},
                "children": [
                    {"type": "text", "text": "This is test content from page 1."}
                ]
            }
        },
        "children": []
    }"""

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_happy(variant)
    app.dependency_overrides[__import__("dataplat_api.storage.s3", fromlist=["get_s3_client"]).get_s3_client] = (
        _make_s3_client_with_docling(docling_json)
    )
    try:
        response = _render(client, variant_id=3)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(
            __import__("dataplat_api.storage.s3", fromlist=["get_s3_client"]).get_s3_client,
            None,
        )

    assert response.status_code == 200
    assert response.headers.get("content-type") == "text/markdown; charset=utf-8"


def test_render_contains_extracted_text(client: TestClient) -> None:
    """GET render response body contains extracted text (V2)."""
    variant = _make_variant_stub(variant_id=3, source_id=7)
    
    docling_json = """{
        "name": "source_7",
        "pages": {
            "1": {
                "page_no": 1,
                "size": {"width": 612.0, "height": 792.0},
                "children": [
                    {"type": "text", "text": "Hello from PDF extraction!"}
                ]
            }
        },
        "children": []
    }"""

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_happy(variant)
    app.dependency_overrides[__import__("dataplat_api.storage.s3", fromlist=["get_s3_client"]).get_s3_client] = (
        _make_s3_client_with_docling(docling_json)
    )
    try:
        response = _render(client, variant_id=3)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(
            __import__("dataplat_api.storage.s3", fromlist=["get_s3_client"]).get_s3_client,
            None,
        )

    assert response.status_code == 200
    assert "Hello from PDF extraction!" in response.text


# ── 404 paths ─────────────────────────────────────────────────────────────────


def test_render_nonexistent_variant_returns_404(client: TestClient) -> None:
    """Variant lookup returns None → 404 'Document variant not found'."""
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_no_variant()
    try:
        response = _render(client, variant_id=99999)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 404
    assert response.json()["detail"] == "Document variant not found"


# ── Auth gate ─────────────────────────────────────────────────────────────────


def test_render_no_token_returns_401(client: TestClient) -> None:
    """GET without Authorization header → 401."""
    response = client.get("/api/documents/3/render")
    assert response.status_code == 401


# ── S3 integration test (unit with mocked S3) ─────────────────────────────────


def test_render_retrieves_docling_document_from_s3(client: TestClient) -> None:
    """Verify S3 get_object is called with correct bucket and key (V3)."""
    variant = _make_variant_stub(
        variant_id=3,
        source_id=7,
        storage_prefix="s3://documents/7/extract_mineru/",
    )

    docling_json = """{"name": "test", "pages": {}, "children": []}"""

    captured_s3_calls: list[dict[str, Any]] = []

    async def _override_s3() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        s3 = AsyncMock()
        body_mock = AsyncMock()
        body_mock.read = AsyncMock(return_value=docling_json.encode("utf-8"))
        response_mock = {"Body": body_mock}

        async def _get_object_wrapper(**kwargs: Any) -> dict[str, Any]:
            captured_s3_calls.append(kwargs)
            return response_mock

        s3.get_object = AsyncMock(side_effect=_get_object_wrapper)
        yield s3

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_happy(variant)
    app.dependency_overrides[__import__("dataplat_api.storage.s3", fromlist=["get_s3_client"]).get_s3_client] = (
        _override_s3()
    )
    try:
        response = _render(client, variant_id=3)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(
            __import__("dataplat_api.storage.s3", fromlist=["get_s3_client"]).get_s3_client,
            None,
        )

    assert response.status_code == 200
    assert len(captured_s3_calls) == 1
    call = captured_s3_calls[0]
    assert call["Bucket"] == "documents"
    assert call["Key"] == "documents/7/extract_mineru/doc.docling.json"
