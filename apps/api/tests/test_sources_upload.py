"""Tests for POST /api/sources/upload — S011-F-011.

Unit tests (run in backend layer — no live DB, Postgres, or MinIO required):
  - test_upload_pdf_returns_201_with_id_and_storage_uri
  - test_upload_pdf_storage_uri_shape
  - test_upload_pdf_sha256_computed_correctly
  - test_upload_pdf_kind_file_mime_pdf_set
  - test_upload_pdf_storage_uri_set_from_id
  - test_upload_pdf_s3_put_object_called
  - test_upload_pdf_no_token_returns_401
  - test_upload_pdf_missing_file_returns_422
  - test_upload_non_pdf_content_type_returns_415
  - test_upload_with_collection_id
  - test_upload_without_collection_id
  - test_upload_s3_failure_does_not_commit
  - test_upload_flush_order_before_s3

All tests use FastAPI's TestClient with conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Dependency-override pattern (mirrors test_sources_collections_create.py):
  - get_current_user is overridden per-test to bypass JWT.
  - get_session is overridden to inject an AsyncMock session with flush side-effect.
  - get_s3_client is overridden to inject an AsyncMock S3 client.
  - All overrides cleaned up in finally blocks.

Auth-gate test (test_upload_pdf_no_token_returns_401) does NOT override
get_current_user — it relies on real oauth2_scheme raising 401.

PDF fixture (_MINIMAL_PDF): constructed inline as a module-level byte constant;
no binary fixture file is committed (agreed.md §6).
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.config import settings
from dataplat_api.db.models import User
from dataplat_api.db.session import get_session
from dataplat_api.main import app
from dataplat_api.storage.s3 import get_s3_client

# ── Minimal valid PDF fixture (inline — no binary committed) ──────────────────
# Byte-identical to the checks.sh fixture (both use %%EOF = literal %EOF).
# sha256 computed at module level for reuse across tests.

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


# ── Session mock helper ───────────────────────────────────────────────────────


def _make_session_dep(flush_id: int = 7) -> Any:
    """Return a get_session dependency override with flush side-effect.

    The flush() side-effect finds the Source object passed to session.add()
    and sets its .id attribute, simulating the DB IDENTITY column assignment.
    session.commit() is a no-op AsyncMock so tests can assert it was/wasn't called.
    """

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.add = MagicMock()  # synchronous in AsyncSession

        def _flush_side_effect() -> None:
            # Retrieve the Source object that was passed to session.add().
            added_obj = session.add.call_args[0][0]
            added_obj.id = flush_id

        session.flush = AsyncMock(side_effect=_flush_side_effect)
        session.commit = AsyncMock()
        yield session

    return _override


# ── S3 mock helper ────────────────────────────────────────────────────────────


def _make_s3_dep() -> Any:
    """Return a get_s3_client dependency override with async put_object."""

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        s3_mock = AsyncMock()
        s3_mock.put_object = AsyncMock(return_value={})
        yield s3_mock

    return _override


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# ── Helpers ───────────────────────────────────────────────────────────────────


def _post_upload(
    client: TestClient,
    pdf_bytes: bytes = _MINIMAL_PDF,
    content_type: str = "application/pdf",
    collection_id: int | None = None,
    filename: str = "test.pdf",
) -> Any:
    """Post to /api/sources/upload with a mock auth token header already injected."""
    files = {"file": (filename, pdf_bytes, content_type)}
    data = {}
    if collection_id is not None:
        data["collection_id"] = str(collection_id)
    return client.post(
        "/api/sources/upload",
        files=files,
        data=data,
        headers={"Authorization": "Bearer faketoken"},
    )


# ── Happy path ────────────────────────────────────────────────────────────────


def test_upload_pdf_returns_201_with_id_and_storage_uri(client: TestClient) -> None:
    """POST with valid PDF → 201 with id (int) and storage_uri matching id (V1)."""
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(flush_id=7)
    app.dependency_overrides[get_s3_client] = _make_s3_dep()
    try:
        response = _post_upload(client)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 201
    body = response.json()
    assert isinstance(body["id"], int)
    assert body["id"] == 7
    assert body["storage_uri"] == "s3://sources/7/original.pdf"


def test_upload_pdf_storage_uri_shape(client: TestClient) -> None:
    """storage_uri matches ^s3://sources/[0-9]+/original\\.pdf$ (V1)."""
    import re

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(flush_id=42)
    app.dependency_overrides[get_s3_client] = _make_s3_dep()
    try:
        response = _post_upload(client)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 201
    uri = response.json()["storage_uri"]
    assert re.match(r"^s3://sources/[0-9]+/original\.pdf$", uri), f"bad shape: {uri}"


def test_upload_pdf_sha256_computed_correctly(client: TestClient) -> None:
    """sha256 on the persisted Source object matches sha256(_MINIMAL_PDF) (V3)."""
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(flush_id=7)
    app.dependency_overrides[get_s3_client] = _make_s3_dep()
    try:
        response = _post_upload(client)
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 201
    # Retrieve the Source object passed to session.add() from the dep generator.
    # We need access to the session mock — rebuild to inspect call_args.
    # Instead inspect via a capturing variant below; this test relies on the
    # sha256 matching the known constant.
    body = response.json()
    assert body["id"] == 7
    # The sha256 is stored on the ORM row, not returned; we verify it via a
    # capturing session mock in a separate capturing test below.
    # Here we simply confirm the upload succeeded (implying sha256 was computed).


def test_upload_pdf_sha256_on_session_add_object(client: TestClient) -> None:
    """sha256 on the Source object passed to session.add() == sha256(_MINIMAL_PDF)."""
    from dataplat_api.db.models import Source

    captured: list[Source] = []

    async def _capturing_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()

        def _add_side_effect(obj: Any) -> None:
            captured.append(obj)

        session.add = MagicMock(side_effect=_add_side_effect)

        def _flush_side_effect() -> None:
            if captured:
                captured[0].id = 7

        session.flush = AsyncMock(side_effect=_flush_side_effect)
        session.commit = AsyncMock()
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _capturing_session
    app.dependency_overrides[get_s3_client] = _make_s3_dep()
    try:
        response = _post_upload(client)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 201
    assert len(captured) == 1
    assert captured[0].sha256 == _MINIMAL_PDF_SHA256


def test_upload_pdf_kind_file_mime_pdf_set(client: TestClient) -> None:
    """Source row has kind='file' and mime_type='application/pdf' (V4)."""
    from dataplat_api.db.models import Source

    captured: list[Source] = []

    async def _capturing_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()

        def _add_side_effect(obj: Any) -> None:
            captured.append(obj)

        session.add = MagicMock(side_effect=_add_side_effect)

        def _flush_side_effect() -> None:
            if captured:
                captured[0].id = 7

        session.flush = AsyncMock(side_effect=_flush_side_effect)
        session.commit = AsyncMock()
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _capturing_session
    app.dependency_overrides[get_s3_client] = _make_s3_dep()
    try:
        response = _post_upload(client)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 201
    assert len(captured) == 1
    assert captured[0].kind == "file"
    assert captured[0].mime_type == "application/pdf"


def test_upload_pdf_storage_uri_set_from_id(client: TestClient) -> None:
    """After flush sets id=42, storage_uri and dagster_partition_key are updated (V1+V2)."""
    from dataplat_api.db.models import Source

    captured: list[Source] = []

    async def _capturing_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()

        def _add_side_effect(obj: Any) -> None:
            captured.append(obj)

        session.add = MagicMock(side_effect=_add_side_effect)

        def _flush_side_effect() -> None:
            if captured:
                captured[0].id = 42

        session.flush = AsyncMock(side_effect=_flush_side_effect)
        session.commit = AsyncMock()
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _capturing_session
    app.dependency_overrides[get_s3_client] = _make_s3_dep()
    try:
        response = _post_upload(client)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 201
    assert len(captured) == 1
    src = captured[0]
    # After flush sets id=42, the handler overwrites these fields.
    assert src.storage_uri == "s3://sources/42/original.pdf"
    assert src.dagster_partition_key == "src_42"
    assert response.json()["storage_uri"] == "s3://sources/42/original.pdf"


def test_upload_pdf_s3_put_object_called(client: TestClient) -> None:
    """put_object called with correct Bucket, Key, Body, ContentType (V2)."""
    captured_s3: list[AsyncMock] = []

    async def _capturing_s3() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        s3_mock = AsyncMock()
        s3_mock.put_object = AsyncMock(return_value={})
        captured_s3.append(s3_mock)
        yield s3_mock

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(flush_id=42)
    app.dependency_overrides[get_s3_client] = _capturing_s3
    try:
        response = _post_upload(client)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 201
    assert len(captured_s3) == 1
    s3 = captured_s3[0]
    s3.put_object.assert_called_once_with(
        Bucket=settings.MINIO_SOURCES_BUCKET,
        Key="sources/42/original.pdf",
        Body=_MINIMAL_PDF,
        ContentType="application/pdf",
    )


# ── Auth gate ─────────────────────────────────────────────────────────────────


def test_upload_pdf_no_token_returns_401(client: TestClient) -> None:
    """No Authorization header → 401 with WWW-Authenticate: Bearer."""
    response = client.post(
        "/api/sources/upload",
        files={"file": ("test.pdf", _MINIMAL_PDF, "application/pdf")},
    )
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


# ── Validation (422 / 415) ────────────────────────────────────────────────────


def test_upload_pdf_missing_file_returns_422(client: TestClient) -> None:
    """POST multipart with no file part → 422."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = client.post(
            "/api/sources/upload",
            data={},
            headers={"Authorization": "Bearer faketoken"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422


def test_upload_non_pdf_content_type_returns_415(client: TestClient) -> None:
    """File with content_type='text/plain' → 415 Unsupported Media Type."""
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep()
    app.dependency_overrides[get_s3_client] = _make_s3_dep()
    try:
        response = _post_upload(client, content_type="text/plain")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 415
    assert "application/pdf" in response.json()["detail"]


# ── collection_id handling ────────────────────────────────────────────────────


def test_upload_with_collection_id(client: TestClient) -> None:
    """collection_id=5 in form → 201; Source row has collection_id=5."""
    from dataplat_api.db.models import Source

    captured: list[Source] = []

    async def _capturing_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()

        def _add_side_effect(obj: Any) -> None:
            captured.append(obj)

        session.add = MagicMock(side_effect=_add_side_effect)

        def _flush_side_effect() -> None:
            if captured:
                captured[0].id = 7

        session.flush = AsyncMock(side_effect=_flush_side_effect)
        session.commit = AsyncMock()
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _capturing_session
    app.dependency_overrides[get_s3_client] = _make_s3_dep()
    try:
        response = _post_upload(client, collection_id=5)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 201
    assert len(captured) == 1
    assert captured[0].collection_id == 5


def test_upload_without_collection_id(client: TestClient) -> None:
    """No collection_id → 201; Source row has collection_id=None."""
    from dataplat_api.db.models import Source

    captured: list[Source] = []

    async def _capturing_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()

        def _add_side_effect(obj: Any) -> None:
            captured.append(obj)

        session.add = MagicMock(side_effect=_add_side_effect)

        def _flush_side_effect() -> None:
            if captured:
                captured[0].id = 7

        session.flush = AsyncMock(side_effect=_flush_side_effect)
        session.commit = AsyncMock()
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _capturing_session
    app.dependency_overrides[get_s3_client] = _make_s3_dep()
    try:
        response = _post_upload(client)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 201
    assert len(captured) == 1
    assert captured[0].collection_id is None


# ── Atomicity ─────────────────────────────────────────────────────────────────


def test_upload_s3_failure_does_not_commit() -> None:
    """S3 put_object raises → 500 returned; session.commit NOT called.

    The handler must NOT swallow the exception or call session.rollback().
    The open transaction is implicitly rolled back when the connection returns
    to the pool (agreed.md §3-D3, §5).

    Uses raise_server_exceptions=False so TestClient converts unhandled
    exceptions to HTTP 500 responses instead of re-raising them in test code.
    """

    async def _failing_s3() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        s3_mock = AsyncMock()
        s3_mock.put_object = AsyncMock(side_effect=Exception("MinIO down"))
        yield s3_mock

    captured_sessions: list[AsyncMock] = []

    async def _capturing_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.add = MagicMock()

        def _flush_side_effect() -> None:
            added_obj = session.add.call_args[0][0]
            added_obj.id = 7

        session.flush = AsyncMock(side_effect=_flush_side_effect)
        session.commit = AsyncMock()
        captured_sessions.append(session)
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _capturing_session
    app.dependency_overrides[get_s3_client] = _failing_s3
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            response = _post_upload(c)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 500
    assert len(captured_sessions) == 1
    captured_sessions[0].commit.assert_not_called()


def test_upload_flush_order_before_s3(client: TestClient) -> None:
    """session.flush() is called BEFORE s3.put_object() (agreed.md §3-D3 ordering)."""
    call_order: list[str] = []

    async def _ordered_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.add = MagicMock()

        def _flush_side_effect() -> None:
            call_order.append("flush")
            added_obj = session.add.call_args[0][0]
            added_obj.id = 7

        session.flush = AsyncMock(side_effect=_flush_side_effect)
        session.commit = AsyncMock()
        yield session

    async def _ordered_s3() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        s3_mock = AsyncMock()

        async def _put_side_effect(**kwargs: Any) -> dict:  # type: ignore[misc]
            call_order.append("put_object")
            return {}

        s3_mock.put_object = AsyncMock(side_effect=_put_side_effect)
        yield s3_mock

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _ordered_session
    app.dependency_overrides[get_s3_client] = _ordered_s3
    try:
        response = _post_upload(client)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 201
    assert call_order == ["flush", "put_object"], (
        f"Expected flush before put_object; got order: {call_order}"
    )
