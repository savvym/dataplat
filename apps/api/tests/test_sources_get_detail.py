"""Tests for GET /api/sources/{id} — S013-F-013.

Unit tests (run in backend layer — no live DB, Postgres, or MinIO required):
  - test_get_source_returns_200_with_all_fields
  - test_get_source_sha256_matches_upload
  - test_get_source_storage_uri_matches_id
  - test_get_source_mime_type_and_kind
  - test_get_source_collection_id_is_none_when_no_collection
  - test_get_source_collection_id_populated
  - test_get_source_not_found_returns_404
  - test_get_source_other_owners_collection_returns_404
  - test_get_source_no_token_returns_401

All tests use FastAPI's TestClient with conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Mock pattern:
  - get_current_user is overridden per-test to bypass JWT.
  - get_session is overridden to inject an AsyncMock session whose execute()
    returns a result whose scalar_one_or_none() returns a Source ORM stub
    (happy path) or None (404 path).
  - All overrides cleaned up in finally blocks.

Auth-gate test (test_get_source_no_token_returns_401) does NOT override
get_current_user — it relies on the real oauth2_scheme raising 401.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import Source, User
from dataplat_api.db.session import get_session
from dataplat_api.main import app

# ── Minimal PDF bytes (same fixture as test_sources_upload.py) ────────────────

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

_MINIMAL_PDF_SHA256 = hashlib.sha256(_MINIMAL_PDF).hexdigest()

# ── Shared mock user ──────────────────────────────────────────────────────────

_MOCK_USER = User(id=1, email="test@example.com", hashed_password="$2b$12$hash")


async def _override_current_user() -> User:
    return _MOCK_USER


# ── Source ORM stub builder ───────────────────────────────────────────────────


def _make_source_stub(
    source_id: int = 42,
    collection_id: int | None = None,
    sha256: str = _MINIMAL_PDF_SHA256,
    size: int = len(_MINIMAL_PDF),
    mime_type: str = "application/pdf",
    kind: str = "file",
    original_name: str = "test.pdf",
    dagster_partition_key: str | None = None,
    uploaded_at: datetime | None = None,
) -> Source:
    """Build a Source ORM object with known field values for assertions."""
    src = Source(
        id=source_id,
        collection_id=collection_id,
        kind=kind,
        original_name=original_name,
        storage_uri=f"s3://sources/{source_id}/original.pdf",
        sha256=sha256,
        size=size,
        mime_type=mime_type,
        dagster_partition_key=dagster_partition_key or f"src_{source_id}",
        uploaded_at=uploaded_at or datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc),
    )
    return src


# ── Session mock helpers ──────────────────────────────────────────────────────


def _make_session_dep_returning(source: Source | None) -> Any:
    """Return a get_session override whose execute().scalar_one_or_none() returns `source`."""

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[return,misc]
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = source
        session.execute = AsyncMock(return_value=result_mock)
        yield session

    return _override


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# ── Helper ────────────────────────────────────────────────────────────────────


def _get_source(client: TestClient, source_id: int) -> Any:
    return client.get(
        f"/api/sources/{source_id}",
        headers={"Authorization": "Bearer faketoken"},
    )


# ── Happy path — all fields present ──────────────────────────────────────────


def test_get_source_returns_200_with_all_fields(client: TestClient) -> None:
    """GET /api/sources/{id} for existing source → 200 with all required fields (V1)."""
    stub = _make_source_stub(source_id=42)
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(stub)
    try:
        response = _get_source(client, 42)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    required_fields = [
        "id",
        "collection_id",
        "kind",
        "original_name",
        "storage_uri",
        "sha256",
        "size",
        "mime_type",
        "dagster_partition_key",
        "uploaded_at",
    ]
    for field in required_fields:
        assert field in body, f"missing field '{field}' in response: {body}"
    assert body["id"] == 42


def test_get_source_sha256_matches_upload(client: TestClient) -> None:
    """sha256 field in response matches the known digest of the PDF bytes."""
    stub = _make_source_stub(source_id=10, sha256=_MINIMAL_PDF_SHA256)
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(stub)
    try:
        response = _get_source(client, 10)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    assert response.json()["sha256"] == _MINIMAL_PDF_SHA256


def test_get_source_storage_uri_matches_id(client: TestClient) -> None:
    """storage_uri field is s3://sources/{id}/original.pdf."""
    stub = _make_source_stub(source_id=99)
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(stub)
    try:
        response = _get_source(client, 99)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    assert response.json()["storage_uri"] == "s3://sources/99/original.pdf"


def test_get_source_mime_type_and_kind(client: TestClient) -> None:
    """mime_type == 'application/pdf', kind == 'file' for a standard PDF upload."""
    stub = _make_source_stub(source_id=5, mime_type="application/pdf", kind="file")
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(stub)
    try:
        response = _get_source(client, 5)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body["mime_type"] == "application/pdf"
    assert body["kind"] == "file"


def test_get_source_collection_id_is_none_when_no_collection(
    client: TestClient,
) -> None:
    """Source uploaded without collection_id → collection_id is null in response."""
    stub = _make_source_stub(source_id=3, collection_id=None)
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(stub)
    try:
        response = _get_source(client, 3)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    assert response.json()["collection_id"] is None


def test_get_source_collection_id_populated(client: TestClient) -> None:
    """Source linked to a collection → collection_id is the correct integer."""
    stub = _make_source_stub(source_id=7, collection_id=123)
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(stub)
    try:
        response = _get_source(client, 7)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    assert response.json()["collection_id"] == 123


# ── 404 paths ─────────────────────────────────────────────────────────────────


def test_get_source_not_found_returns_404(client: TestClient) -> None:
    """GET /api/sources/99999 → 404 (non-existent id) (V2)."""
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(None)
    try:
        response = _get_source(client, 99999)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 404
    assert response.json()["detail"] == "Source not found"


def test_get_source_other_owners_collection_returns_404(client: TestClient) -> None:
    """Source in another user's collection is invisible → mock returns None → 404.

    The LEFT JOIN + WHERE filter excludes sources from collections not owned by
    the caller. From the handler's perspective this is identical to not found.
    """
    app.dependency_overrides[get_current_user] = _override_current_user
    # Session returns None — simulates the owner-scoping query filtering out
    # the row (as if it belonged to a different user's collection).
    app.dependency_overrides[get_session] = _make_session_dep_returning(None)
    try:
        response = _get_source(client, 55)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 404
    assert response.json()["detail"] == "Source not found"


# ── Auth gate ─────────────────────────────────────────────────────────────────


def test_get_source_no_token_returns_401(client: TestClient) -> None:
    """GET /api/sources/{id} without Authorization header → 401."""
    response = client.get("/api/sources/42")
    assert response.status_code == 401
