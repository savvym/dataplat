"""Tests for POST /api/sources/{source_id}/documents/{extractor_name}/set-canonical — S021-F-021.

Unit tests (run in backend layer — no live DB, Postgres, or MinIO required):
  - test_set_canonical_returns_200
  - test_set_canonical_response_has_is_canonical_true
  - test_set_canonical_source_not_found_returns_404
  - test_set_canonical_variant_not_found_returns_404
  - test_set_canonical_no_token_returns_401
  - test_set_canonical_commit_called_once
  - test_set_canonical_idempotent_when_already_canonical

All tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Mock session pattern (4-execute handler on happy path):
  The handler calls session.execute() FOUR times on the happy path:
    1st — ownership check:  .scalar_one_or_none() → Source stub or None
    2nd — variant SELECT:   .scalar_one_or_none() → DocumentVariant stub or None
    3rd — CLEAR UPDATE:     result not inspected (MagicMock)
    4th — SET UPDATE:       result not inspected (MagicMock)
  Then:
    session.commit()  → AsyncMock returning None
    session.refresh() → AsyncMock; side_effect sets target.is_canonical = True

For 404 paths: only 1 or 2 execute() calls (short-circuit before UPDATEs).

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

_NOW = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)


def _make_source_stub(source_id: int = 7) -> MagicMock:
    """Build a MagicMock that looks like a Source ORM row.

    A MagicMock with spec is sufficient for scalar_one_or_none() to return a
    truthy object that the handler can check for None.
    """
    src = MagicMock(spec=Source)
    src.id = source_id
    return src


def _make_variant_stub(
    variant_id: int = 3,
    source_id: int = 7,
    extractor_name: str = "mineru",
    extractor_version: str = "0.1.0",
    config_hash: str = "abc123def456" * 4 + "ab",  # 50 chars
    storage_prefix: str = "s3://documents/7/extract_mineru/",
    page_count: int | None = 10,
    image_count: int | None = 3,
    is_canonical: bool | None = False,
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
    target: MagicMock,
) -> Any:
    """Return a get_session override for the happy path (4 execute() calls).

    The handler calls session.execute() four times:
      1st — ownership check: .scalar_one_or_none() → source stub
      2nd — variant SELECT:  .scalar_one_or_none() → target stub
      3rd — CLEAR UPDATE:    result not inspected
      4th — SET UPDATE:      result not inspected

    After the 4 executes:
      session.commit()        → AsyncMock returning None
      session.refresh(target) → AsyncMock; side_effect sets target.is_canonical=True
    """
    source_result = MagicMock()
    source_result.scalar_one_or_none.return_value = source

    variant_result = MagicMock()
    variant_result.scalar_one_or_none.return_value = target

    clear_result = MagicMock()
    set_result = MagicMock()

    def _refresh_side_effect(obj: MagicMock) -> None:
        obj.is_canonical = True

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[source_result, variant_result, clear_result, set_result]
        )
        session.commit = AsyncMock(return_value=None)
        session.refresh = AsyncMock(side_effect=_refresh_side_effect)
        yield session

    return _override


def _make_session_dep_no_source() -> Any:
    """Return a get_session override for the source-not-found 404 path (1 execute()).

    The ownership check returns None → handler raises 404 "Source not found"
    before the variant query is issued.
    """
    source_result = MagicMock()
    source_result.scalar_one_or_none.return_value = None

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[source_result])
        yield session

    return _override


def _make_session_dep_no_variant(source: MagicMock) -> Any:
    """Return a get_session override for the variant-not-found 404 path (2 execute()).

    The source check succeeds; the variant SELECT returns None → handler raises
    404 "Variant not found" before any UPDATE is issued.
    """
    source_result = MagicMock()
    source_result.scalar_one_or_none.return_value = source

    variant_result = MagicMock()
    variant_result.scalar_one_or_none.return_value = None

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[source_result, variant_result])
        yield session

    return _override


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# ── Helper ────────────────────────────────────────────────────────────────────


def _set_canonical(
    client: TestClient,
    source_id: int = 7,
    extractor_name: str = "mineru",
) -> Any:
    return client.post(
        f"/api/sources/{source_id}/documents/{extractor_name}/set-canonical",
        headers={"Authorization": "Bearer faketoken"},
    )


# ── Happy path tests ──────────────────────────────────────────────────────────


def test_set_canonical_returns_200(client: TestClient) -> None:
    """POST set-canonical happy path → 200 (V1)."""
    src = _make_source_stub(source_id=7)
    variant = _make_variant_stub(variant_id=3, source_id=7, is_canonical=False)
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_happy(src, variant)
    try:
        response = _set_canonical(client, source_id=7, extractor_name="mineru")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200


def test_set_canonical_response_has_is_canonical_true(client: TestClient) -> None:
    """POST set-canonical response body has is_canonical=true and extractor_name='mineru' (V1)."""
    src = _make_source_stub(source_id=7)
    variant = _make_variant_stub(variant_id=3, source_id=7, is_canonical=False)
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_happy(src, variant)
    try:
        response = _set_canonical(client, source_id=7, extractor_name="mineru")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body["extractor_name"] == "mineru"
    assert body["is_canonical"] is True


# ── 404 paths ─────────────────────────────────────────────────────────────────


def test_set_canonical_source_not_found_returns_404(client: TestClient) -> None:
    """Source check returns None → 404 'Source not found'; no further execute calls."""
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_no_source()
    try:
        response = _set_canonical(client, source_id=99999, extractor_name="mineru")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 404
    assert response.json()["detail"] == "Source not found"


def test_set_canonical_variant_not_found_returns_404(client: TestClient) -> None:
    """Source found but variant SELECT returns None → 404 'Variant not found'; no UPDATE calls."""
    src = _make_source_stub(source_id=7)
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_no_variant(src)
    try:
        response = _set_canonical(client, source_id=7, extractor_name="nonexistent")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 404
    assert response.json()["detail"] == "Variant not found"


# ── Auth gate ─────────────────────────────────────────────────────────────────


def test_set_canonical_no_token_returns_401(client: TestClient) -> None:
    """POST without Authorization header → 401."""
    response = client.post("/api/sources/7/documents/mineru/set-canonical")
    assert response.status_code == 401


# ── Commit / call-count tests ─────────────────────────────────────────────────


def test_set_canonical_commit_called_once(client: TestClient) -> None:
    """Happy path: session.commit() called exactly once after both UPDATEs (V2 unit)."""
    src = _make_source_stub(source_id=7)
    variant = _make_variant_stub(variant_id=3, source_id=7, is_canonical=False)

    source_result = MagicMock()
    source_result.scalar_one_or_none.return_value = src

    variant_result = MagicMock()
    variant_result.scalar_one_or_none.return_value = variant

    clear_result = MagicMock()
    set_result = MagicMock()

    def _refresh_side_effect(obj: MagicMock) -> None:
        obj.is_canonical = True

    captured_session: list[AsyncMock] = []

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[source_result, variant_result, clear_result, set_result]
        )
        session.commit = AsyncMock(return_value=None)
        session.refresh = AsyncMock(side_effect=_refresh_side_effect)
        captured_session.append(session)
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _override
    try:
        response = _set_canonical(client, source_id=7, extractor_name="mineru")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    assert len(captured_session) == 1
    captured_session[0].commit.assert_called_once()


# ── Idempotency test ──────────────────────────────────────────────────────────


def test_set_canonical_idempotent_when_already_canonical(client: TestClient) -> None:
    """Target already has is_canonical=True; handler still issues CLEAR+SET and returns 200.

    No early-exit branch — the handler always runs CLEAR then SET regardless of
    the current is_canonical value. This is the intended idempotent behaviour.
    """
    src = _make_source_stub(source_id=7)
    # Target is ALREADY canonical — handler must still complete without error.
    variant = _make_variant_stub(variant_id=3, source_id=7, is_canonical=True)
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_happy(src, variant)
    try:
        response = _set_canonical(client, source_id=7, extractor_name="mineru")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body["is_canonical"] is True
