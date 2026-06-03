"""Tests for GET /api/sources/collections/{id}/sources — S014-F-014.

Unit tests (run in backend layer — no live DB, Postgres, or MinIO required):
  - test_list_sources_by_collection_returns_200_with_items
  - test_list_sources_by_collection_items_have_required_fields
  - test_list_sources_by_collection_total_is_full_count_not_page
  - test_list_sources_by_collection_offset_works
  - test_list_sources_by_collection_collection_not_found_returns_404
  - test_list_sources_by_collection_other_owners_collection_returns_404
  - test_list_sources_by_collection_empty_collection_returns_zero
  - test_list_sources_by_collection_no_token_returns_401
  - test_list_sources_by_collection_invalid_limit_zero_returns_422
  - test_list_sources_by_collection_invalid_limit_over_cap_returns_422
  - test_list_sources_by_collection_invalid_offset_negative_returns_422

All tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Mock session pattern (3-query handler):
  The handler calls session.execute() THREE times on the happy path:
    1st call — ownership check → result with .scalar_one_or_none() == collection stub or None
    2nd call — paginated page  → result with .scalars().all()        == list of Source stubs
    3rd call — COUNT query     → result with .scalar_one()           == int total

  For the 404 path (collection not found / not owned), only ONE execute() call
  is made (the ownership check short-circuits to HTTPException).

  All result mocks are plain MagicMock, NOT AsyncMock. Only session.execute()
  itself is awaited; .scalar_one_or_none(), .scalars(), .all(), .scalar_one()
  are synchronous calls on the result proxy. Using AsyncMock for those would
  cause .scalars() to return a coroutine instead of a result object.

Auth-gate test does NOT override get_current_user — the real oauth2_scheme
raises 401 for a missing Authorization header.
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
from dataplat_api.db.models import Source, SourceCollection, User
from dataplat_api.db.session import get_session
from dataplat_api.main import app

# ── Minimal PDF bytes (same fixture as other source tests) ────────────────────

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


# ── ORM stub builders ─────────────────────────────────────────────────────────

_NOW = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)


def _make_collection_stub(
    collection_id: int = 10,
    owner_id: int = 1,
    name: str = "test-collection",
) -> MagicMock:
    """Build a MagicMock that looks like a SourceCollection ORM row.

    SourceCollection uses SQLAlchemy instrumented attributes; constructing via
    __new__ would be missing _sa_instance_state. A MagicMock with spec is
    sufficient for scalar_one_or_none() to return a truthy object that the
    handler can check for None.
    """
    coll = MagicMock(spec=SourceCollection)
    coll.id = collection_id
    coll.owner_id = owner_id
    coll.name = name
    return coll


def _make_source_stub(
    source_id: int = 42,
    collection_id: int = 10,
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
        uploaded_at=uploaded_at or _NOW,
    )
    return src


# ── Session mock helpers ──────────────────────────────────────────────────────


def _make_session_dep_happy(
    collection: MagicMock,
    source_rows: list[Source],
    total: int,
) -> Any:
    """Return a get_session override for the happy path (3 execute() calls).

    The handler calls session.execute() three times:
      1st — ownership check: scalar_one_or_none() → collection stub (truthy)
      2nd — paginated page:  scalars().all()       → source_rows list
      3rd — COUNT query:     scalar_one()           → total int
    """
    coll_result = MagicMock()
    coll_result.scalar_one_or_none.return_value = collection

    page_result = MagicMock()
    page_result.scalars.return_value.all.return_value = source_rows

    count_result = MagicMock()
    count_result.scalar_one.return_value = total

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[coll_result, page_result, count_result]
        )
        yield session

    return _override


def _make_session_dep_no_collection() -> Any:
    """Return a get_session override for the 404 path (1 execute() call).

    The ownership check returns None → handler raises 404 before the
    source queries are issued.
    """
    coll_result = MagicMock()
    coll_result.scalar_one_or_none.return_value = None

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[coll_result])
        yield session

    return _override


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# ── Helper ────────────────────────────────────────────────────────────────────


def _list_sources(
    client: TestClient,
    collection_id: int,
    params: dict[str, Any] | None = None,
) -> Any:
    return client.get(
        f"/api/sources/collections/{collection_id}/sources",
        headers={"Authorization": "Bearer faketoken"},
        params=params or {},
    )


# ── Happy path tests ──────────────────────────────────────────────────────────


def test_list_sources_by_collection_returns_200_with_items(client: TestClient) -> None:
    """GET /collections/{id}/sources for owned collection with 3 sources → 200, total==3 (V1)."""
    coll = _make_collection_stub(collection_id=10)
    sources = [
        _make_source_stub(source_id=1, collection_id=10),
        _make_source_stub(source_id=2, collection_id=10),
        _make_source_stub(source_id=3, collection_id=10),
    ]
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_happy(coll, sources, 3)
    try:
        response = _list_sources(client, collection_id=10)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert "total" in body
    assert body["total"] == 3
    assert len(body["items"]) == 3


def test_list_sources_by_collection_items_have_required_fields(
    client: TestClient,
) -> None:
    """Each item in response contains the 5 F-014 required fields (V1)."""
    coll = _make_collection_stub(collection_id=10)
    sources = [
        _make_source_stub(source_id=7, collection_id=10),
    ]
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_happy(coll, sources, 1)
    try:
        response = _list_sources(client, collection_id=10)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    required_fields = ["id", "original_name", "storage_uri", "sha256", "uploaded_at"]
    for field in required_fields:
        assert field in item, f"item missing required field '{field}': {item}"
    # Also verify correctness of key fields.
    assert item["id"] == 7
    assert item["storage_uri"] == "s3://sources/7/original.pdf"
    assert item["sha256"] == _MINIMAL_PDF_SHA256


def test_list_sources_by_collection_total_is_full_count_not_page(
    client: TestClient,
) -> None:
    """limit=2 page returns 2 items but total reflects the full collection count (3)."""
    coll = _make_collection_stub(collection_id=10)
    # Mock returns only 2 rows (as if DB applied LIMIT 2), but count=3.
    sources = [
        _make_source_stub(source_id=1, collection_id=10),
        _make_source_stub(source_id=2, collection_id=10),
    ]
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_happy(coll, sources, 3)
    try:
        response = _list_sources(client, collection_id=10, params={"limit": 2})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["total"] == 3


def test_list_sources_by_collection_offset_works(client: TestClient) -> None:
    """offset=2 of 3 sources returns 1 item, total still == 3."""
    coll = _make_collection_stub(collection_id=10)
    # Mock returns 1 row (as if DB applied OFFSET 2), but count=3.
    sources = [_make_source_stub(source_id=3, collection_id=10)]
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_happy(coll, sources, 3)
    try:
        response = _list_sources(client, collection_id=10, params={"offset": 2})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["total"] == 3


def test_list_sources_by_collection_empty_collection_returns_zero(
    client: TestClient,
) -> None:
    """Owned collection that has zero sources → 200, items=[], total=0."""
    coll = _make_collection_stub(collection_id=10)
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_happy(coll, [], 0)
    try:
        response = _list_sources(client, collection_id=10)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body == {"items": [], "total": 0}


# ── 404 paths ─────────────────────────────────────────────────────────────────


def test_list_sources_by_collection_collection_not_found_returns_404(
    client: TestClient,
) -> None:
    """Non-existent collection → 404 with detail='Collection not found' (V2)."""
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_no_collection()
    try:
        response = _list_sources(client, collection_id=999999)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 404
    assert response.json()["detail"] == "Collection not found"


def test_list_sources_by_collection_other_owners_collection_returns_404(
    client: TestClient,
) -> None:
    """Collection owned by another user → ownership query returns None → 404.

    The handler short-circuits on the first query (ownership check). From the
    caller's perspective this is identical to the collection not existing.
    """
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_no_collection()
    try:
        response = _list_sources(client, collection_id=55)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 404
    assert response.json()["detail"] == "Collection not found"


# ── Auth gate ─────────────────────────────────────────────────────────────────


def test_list_sources_by_collection_no_token_returns_401(client: TestClient) -> None:
    """GET without Authorization header → 401."""
    response = client.get("/api/sources/collections/10/sources")
    assert response.status_code == 401


# ── Pagination validation tests ───────────────────────────────────────────────


def test_list_sources_by_collection_invalid_limit_zero_returns_422(
    client: TestClient,
) -> None:
    """`?limit=0` violates ge=1 → 422."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = _list_sources(client, collection_id=10, params={"limit": 0})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422


def test_list_sources_by_collection_invalid_limit_over_cap_returns_422(
    client: TestClient,
) -> None:
    """`?limit=201` violates le=200 → 422."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = _list_sources(client, collection_id=10, params={"limit": 201})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422


def test_list_sources_by_collection_invalid_offset_negative_returns_422(
    client: TestClient,
) -> None:
    """`?offset=-1` violates ge=0 → 422."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = _list_sources(client, collection_id=10, params={"offset": -1})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422
