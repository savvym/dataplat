"""Tests for GET /api/sources/{source_id}/documents — S020-F-020.

Unit tests (run in backend layer — no live DB, Postgres, or MinIO required):
  - test_list_documents_returns_200_with_items
  - test_list_documents_item_fields_match_model
  - test_list_documents_returns_empty_list_when_no_variants
  - test_list_documents_source_not_found_returns_404
  - test_list_documents_other_owners_source_returns_404
  - test_list_documents_no_token_returns_401

All tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Mock session pattern (2-query handler):
  The handler calls session.execute() TWICE on the happy path:
    1st call — ownership check → result with .scalar_one_or_none() == Source stub or None
    2nd call — variant fetch   → result with .scalars().all()       == list of DocumentVariant stubs

  For the 404 path (source not found / not accessible), only ONE execute() call
  is made (the ownership check short-circuits to HTTPException).

  All result mocks are plain MagicMock, NOT AsyncMock. Only session.execute()
  itself is awaited; .scalar_one_or_none(), .scalars(), .all() are synchronous
  calls on the result proxy. Using AsyncMock for those would cause .scalars()
  to return a coroutine instead of a result object.

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
from dataplat_api.db.models import DocumentVariant, Source, User
from dataplat_api.db.session import get_session
from dataplat_api.main import app

# ── Shared mock user ──────────────────────────────────────────────────────────

_MOCK_USER = User(id=1, email="test@example.com", hashed_password="$2b$12$hash")


async def _override_current_user() -> User:
    return _MOCK_USER


# ── ORM stub builders ─────────────────────────────────────────────────────────

_NOW = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)


def _make_source_stub(source_id: int = 7) -> MagicMock:
    """Build a MagicMock that looks like a Source ORM row.

    A MagicMock with spec is sufficient for scalar_one_or_none() to return a
    truthy object that the handler can check for None.
    """
    src = MagicMock(spec=Source)
    src.id = source_id
    return src


def _make_variant_stub(
    variant_id: int = 1,
    source_id: int = 7,
    extractor_name: str = "mineru",
    extractor_version: str = "0.1.0",
    config_hash: str = "abc123def456" * 4 + "ab",  # 50 chars
    storage_prefix: str = "s3://documents/7/extract_mineru/",
    page_count: int | None = 10,
    image_count: int | None = 3,
    is_canonical: bool | None = True,
    materialized_at: datetime | None = None,
    dagster_run_id: str | None = "run-uuid-1234",
) -> MagicMock:
    """Build a MagicMock that looks like a DocumentVariant ORM row.

    Uses MagicMock(spec=DocumentVariant) so that Pydantic's model_validate
    with from_attributes=True can read the attributes off it directly.
    """
    variant = MagicMock(spec=DocumentVariant)
    variant.id = variant_id
    variant.source_id = source_id
    variant.extractor_name = extractor_name
    variant.extractor_version = extractor_version
    variant.config_hash = config_hash
    variant.storage_prefix = storage_prefix
    variant.page_count = page_count
    variant.image_count = image_count
    variant.is_canonical = is_canonical
    variant.materialized_at = materialized_at or _NOW
    variant.dagster_run_id = dagster_run_id
    return variant


# ── Session mock helpers ──────────────────────────────────────────────────────


def _make_session_dep_happy(
    source: MagicMock,
    variant_rows: list[MagicMock],
) -> Any:
    """Return a get_session override for the happy path (2 execute() calls).

    The handler calls session.execute() twice:
      1st — ownership check: scalar_one_or_none() → source stub (truthy) or None
      2nd — variant fetch:   scalars().all()       → variant_rows list
    """
    source_result = MagicMock()
    source_result.scalar_one_or_none.return_value = source

    variants_result = MagicMock()
    variants_result.scalars.return_value.all.return_value = variant_rows

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[source_result, variants_result])
        yield session

    return _override


def _make_session_dep_no_source() -> Any:
    """Return a get_session override for the 404 path (1 execute() call).

    The ownership check returns None → handler raises 404 before the
    variant query is issued.
    """
    source_result = MagicMock()
    source_result.scalar_one_or_none.return_value = None

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[source_result])
        yield session

    return _override


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# ── Helper ────────────────────────────────────────────────────────────────────


def _list_documents(client: TestClient, source_id: int) -> Any:
    return client.get(
        f"/api/sources/{source_id}/documents",
        headers={"Authorization": "Bearer faketoken"},
    )


# ── Happy path tests ──────────────────────────────────────────────────────────


def test_list_documents_returns_200_with_items(client: TestClient) -> None:
    """GET /api/sources/7/documents with 1 variant → 200, array[1], required fields present (V1)."""
    src = _make_source_stub(source_id=7)
    variant = _make_variant_stub(
        variant_id=1,
        source_id=7,
        extractor_name="mineru",
        extractor_version="0.1.0",
        storage_prefix="s3://documents/7/extract_mineru/",
        is_canonical=True,
        materialized_at=_NOW,
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_happy(src, [variant])
    try:
        response = _list_documents(client, source_id=7)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 1
    item = body[0]
    # Verify the 5 required fields from agreed.md §6 V1 are present.
    required_fields = [
        "extractor_name",
        "extractor_version",
        "storage_prefix",
        "is_canonical",
        "materialized_at",
    ]
    for field in required_fields:
        assert field in item, f"item missing required field '{field}': {item}"
    # Verify correct values for two of the required fields.
    assert item["extractor_name"] == "mineru"
    assert item["extractor_version"] == "0.1.0"


def test_list_documents_item_fields_match_model(client: TestClient) -> None:
    """All 10 DocumentVariantRead fields are present in the response item."""
    src = _make_source_stub(source_id=7)
    variant = _make_variant_stub(
        variant_id=42,
        source_id=7,
        extractor_name="mineru",
        extractor_version="0.1.0",
        config_hash="deadbeef" * 8,
        storage_prefix="s3://documents/7/extract_mineru/",
        page_count=5,
        image_count=2,
        is_canonical=False,
        materialized_at=_NOW,
        dagster_run_id="run-abc-xyz",
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_happy(src, [variant])
    try:
        response = _list_documents(client, source_id=7)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    item = body[0]
    # Verify all 10 fields defined in DocumentVariantRead are present.
    all_fields = [
        "id",
        "extractor_name",
        "extractor_version",
        "config_hash",
        "storage_prefix",
        "page_count",
        "image_count",
        "is_canonical",
        "materialized_at",
        "dagster_run_id",
    ]
    for field in all_fields:
        assert field in item, f"item missing field '{field}': {item}"
    # Spot-check values.
    assert item["id"] == 42
    assert item["config_hash"] == "deadbeef" * 8
    assert item["page_count"] == 5
    assert item["image_count"] == 2
    assert item["is_canonical"] is False
    assert item["dagster_run_id"] == "run-abc-xyz"


def test_list_documents_returns_empty_list_when_no_variants(client: TestClient) -> None:
    """Source exists but has no variants yet → 200 with empty array []."""
    src = _make_source_stub(source_id=7)
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_happy(src, [])
    try:
        response = _list_documents(client, source_id=7)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    assert response.json() == []


# ── 404 paths ─────────────────────────────────────────────────────────────────


def test_list_documents_source_not_found_returns_404(client: TestClient) -> None:
    """Non-existent source → ownership check returns None → 404 (V2)."""
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_no_source()
    try:
        response = _list_documents(client, source_id=99999)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 404
    assert response.json()["detail"] == "Source not found"


def test_list_documents_other_owners_source_returns_404(client: TestClient) -> None:
    """Source owned by another user → ownership query returns None → 404.

    From the caller's perspective this is indistinguishable from the source
    not existing — both produce 404 to prevent enumeration leaks.
    """
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_no_source()
    try:
        response = _list_documents(client, source_id=55)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 404
    assert response.json()["detail"] == "Source not found"


# ── Auth gate ─────────────────────────────────────────────────────────────────


def test_list_documents_no_token_returns_401(client: TestClient) -> None:
    """GET without Authorization header → 401."""
    response = client.get("/api/sources/7/documents")
    assert response.status_code == 401
