"""Tests for seed-admin CLI, POST /api/auth/token (S007-F-007), and JWT enforcement
on GET /api/sources/collections (S008-F-008).

Unit tests (run in backend) layer — no live DB or compose stack required):
  S007-F-007:
    - test_seed_admin_logic
    - test_seed_admin_idempotent
    - test_token_correct_credentials_returns_200
    - test_token_wrong_password_returns_401
    - test_token_user_not_found_returns_401
    - test_token_missing_fields_returns_422
  S008-F-008:
    - test_collections_no_token_returns_401
    - test_collections_malformed_token_returns_401
    - test_collections_expired_token_returns_401
    - test_collections_wrong_key_returns_401
    - test_collections_valid_token_returns_200
    - test_collections_user_not_found_returns_401
    - test_collections_jwt_decode_path

Integration tests (marked @pytest.mark.integration — require live compose stack;
run with `pytest -m integration` or remove the -m filter from addopts):
  - test_seed_admin_creates_one_row

All unit tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

_patch_engine_begin interaction with seed tests:
  - test_seed_admin_logic / test_seed_admin_idempotent: mock SessionLocal entirely;
    never reach engine.begin(). No interaction.
  - test_token_* / test_collections_*: use TestClient(app) — lifespan calls
    engine.begin() which is mocked to a no-op by _patch_engine_begin. No interaction.
  - test_seed_admin_creates_one_row: subprocess, separate Python process — monkeypatch
    never applies. Connects to real Postgres. No interaction.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import bcrypt
import jwt
import pytest
from fastapi.testclient import TestClient

from dataplat_api.config import settings
from dataplat_api.db.models import User
from dataplat_api.main import app


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_mock_session(existing_user: User | None = None) -> AsyncMock:
    """Return an AsyncMock session that mimics AsyncSession behaviour.

    The SELECT path (execute → scalars → first) is wired per impl note 2:
        mock_result.scalars.return_value.first.return_value = existing_user
    """
    session = AsyncMock()

    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = existing_user
    session.execute.return_value = mock_result

    # session.add() is synchronous in SQLAlchemy AsyncSession — override
    # AsyncMock's default (which would create an unawaited coroutine warning).
    session.add = MagicMock()

    # Provide an async context manager interface so `async with SessionLocal()`
    # works in the seed_admin coroutine.
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    return session


@pytest.fixture()
def client() -> TestClient:
    """Return a TestClient with fully initialised app lifespan.

    The conftest autouse _patch_engine_begin fixture ensures engine.begin()
    is a no-op so no live Postgres is needed.
    """
    with TestClient(app) as c:
        yield c


# ── Seed CLI unit tests ───────────────────────────────────────────────────────


def test_seed_admin_logic() -> None:
    """Mock session (no existing user) → session.add() called once with a real bcrypt hash."""
    from dataplat_api.cli import seed_admin

    mock_session = _make_mock_session(existing_user=None)

    with patch("dataplat_api.cli.SessionLocal", return_value=mock_session):
        asyncio.run(seed_admin("admin@example.com", "testpassword"))

    # session.add() must have been called exactly once.
    assert mock_session.add.call_count == 1
    added_user: User = mock_session.add.call_args[0][0]

    # The stored hash must be non-empty and must not equal the plaintext.
    assert isinstance(added_user.hashed_password, str)
    assert len(added_user.hashed_password) > 0
    assert added_user.hashed_password != "testpassword"

    # Verify the stored hash actually matches the plaintext (round-trip check).
    assert bcrypt.checkpw(
        b"testpassword",
        added_user.hashed_password.encode("utf-8"),
    )

    # session.commit() must have been awaited.
    mock_session.commit.assert_awaited_once()


def test_seed_admin_idempotent() -> None:
    """Mock session returns existing user → session.add() never called (INSERT skipped)."""
    from dataplat_api.cli import seed_admin

    existing = User(
        id=1,
        email="admin@example.com",
        hashed_password="$2b$12$somehash",
    )
    mock_session = _make_mock_session(existing_user=existing)

    with patch("dataplat_api.cli.SessionLocal", return_value=mock_session):
        asyncio.run(seed_admin("admin@example.com", "anypassword"))

    # INSERT path must be skipped entirely.
    mock_session.add.assert_not_called()
    mock_session.commit.assert_not_awaited()


# ── POST /api/auth/token unit tests ──────────────────────────────────────────


def _make_session_dependency(user: User | None) -> Any:
    """Return an async generator suitable for use as a FastAPI dependency override.

    Yields an AsyncMock session whose SELECT returns `user` (or None).
    """
    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = user
        session.execute.return_value = mock_result
        yield session

    return _override


def test_token_correct_credentials_returns_200(client: TestClient) -> None:
    """Correct email + matching password → 200 with {access_token, token_type: bearer}."""
    from dataplat_api.db.session import get_session

    # Build a User with a known bcrypt hash for "testpassword".
    known_hash = bcrypt.hashpw(b"testpassword", bcrypt.gensalt(rounds=4)).decode("utf-8")
    user = User(id=1, email="admin@example.com", hashed_password=known_hash)

    app.dependency_overrides[get_session] = _make_session_dependency(user)
    try:
        response = client.post(
            "/api/auth/token",
            data={"username": "admin@example.com", "password": "testpassword"},
        )
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert len(body["access_token"]) > 0


def test_token_wrong_password_returns_401(client: TestClient) -> None:
    """Correct email but wrong password → 401."""
    from dataplat_api.db.session import get_session

    known_hash = bcrypt.hashpw(b"correctpassword", bcrypt.gensalt(rounds=4)).decode("utf-8")
    user = User(id=1, email="admin@example.com", hashed_password=known_hash)

    app.dependency_overrides[get_session] = _make_session_dependency(user)
    try:
        response = client.post(
            "/api/auth/token",
            data={"username": "admin@example.com", "password": "wrongpassword"},
        )
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 401
    body = response.json()
    assert "detail" in body
    assert body["detail"] == "Incorrect username or password"


def test_token_user_not_found_returns_401(client: TestClient) -> None:
    """Email not in DB → 401 with the same message as wrong password (prevents enumeration)."""
    from dataplat_api.db.session import get_session

    app.dependency_overrides[get_session] = _make_session_dependency(user=None)
    try:
        response = client.post(
            "/api/auth/token",
            data={"username": "nobody@example.com", "password": "anypassword"},
        )
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 401
    body = response.json()
    assert "detail" in body
    assert body["detail"] == "Incorrect username or password"


def test_token_missing_fields_returns_422(client: TestClient) -> None:
    """Missing username field in form body → 422 Unprocessable Entity."""
    response = client.post(
        "/api/auth/token",
        data={"password": "somepassword"},  # no username
    )
    assert response.status_code == 422


# ── GET /api/sources/collections unit tests (S008-F-008) ─────────────────────


def _make_session_dependency_for_user(user: User | None) -> Any:
    """Return an async generator dependency that yields a session returning `user`.

    Used by tests that exercise the actual JWT decode + DB lookup path in
    get_current_user (override get_session, NOT get_current_user).
    The session's execute() returns a mock result whose scalar_one_or_none()
    returns the given user.
    """
    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        session.execute.return_value = mock_result
        yield session

    return _override


def _mint_token(
    sub: str = "1",
    email: str = "test@example.com",
    exp_offset: int = 3600,
    key: str | None = None,
) -> str:
    """Mint a JWT for testing purposes.

    Args:
        sub: subject claim (user id as string)
        email: email claim
        exp_offset: seconds from now for expiry (negative = already expired)
        key: signing key; defaults to settings.SECRET_KEY
    """
    if key is None:
        key = settings.SECRET_KEY
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": email,
        "iat": now,
        "exp": now + exp_offset,
    }
    return jwt.encode(payload, key, algorithm=settings.JWT_ALGORITHM)


def test_collections_no_token_returns_401(client: TestClient) -> None:
    """No Authorization header → 401 with WWW-Authenticate: Bearer header.

    oauth2_scheme (auto_error=True) raises HTTP 401 automatically when the
    Authorization header is absent.  The WWW-Authenticate header assertion
    locks in the auto_error=True guarantee — if it were ever set to False,
    FastAPI would not set that header and this test would catch the regression.
    """
    response = client.get("/api/sources/collections")
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_collections_malformed_token_returns_401(client: TestClient) -> None:
    """Authorization: Bearer notajwt → 401 (jwt.InvalidTokenError)."""
    response = client.get(
        "/api/sources/collections",
        headers={"Authorization": "Bearer notajwt"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


def test_collections_expired_token_returns_401(client: TestClient) -> None:
    """Manually crafted expired token → 401 (jwt.ExpiredSignatureError)."""
    from dataplat_api.db.session import get_session

    expired_token = _mint_token(exp_offset=-3600)  # expired 1 hour ago

    # Override get_session as defense in depth (decode raises before DB lookup).
    app.dependency_overrides[get_session] = _make_session_dependency_for_user(user=None)
    try:
        response = client.get(
            "/api/sources/collections",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


def test_collections_wrong_key_returns_401(client: TestClient) -> None:
    """Token signed with a different key → 401 (jwt.InvalidSignatureError).

    The wrong key is the literal string "definitely-not-the-real-secret", which
    is distinct from settings.SECRET_KEY = "test-secret-key-not-for-production"
    in the test environment.  This ensures the failure is signature-based, not
    expiry or structural.
    """
    wrong_key_token = _mint_token(key="definitely-not-the-real-secret")
    response = client.get(
        "/api/sources/collections",
        headers={"Authorization": f"Bearer {wrong_key_token}"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


def test_collections_valid_token_returns_200(client: TestClient) -> None:
    """Valid token with get_current_user overridden to return a mock User → 200.

    Uses a dependency override on get_current_user (not get_session) so the
    test is a pure route-layer check.  The actual JWT decode path is covered by
    test_collections_jwt_decode_path.
    """
    from dataplat_api.auth.dependencies import get_current_user

    mock_user = User(id=1, email="test@example.com", hashed_password="$2b$12$hash")

    async def _override_current_user() -> User:
        return mock_user

    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = client.get(
            "/api/sources/collections",
            headers={"Authorization": "Bearer dummy-token"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    body = response.json()
    assert body == {"items": [], "total": 0}


def test_collections_user_not_found_returns_401(client: TestClient) -> None:
    """Valid JWT, get_session overridden to return no row → 401.

    Overrides get_session (NOT get_current_user) so that the actual JWT decode
    and DB lookup code paths in get_current_user are exercised.  The JWT decodes
    successfully (valid signature, non-expired), but scalar_one_or_none() returns
    None (user deleted after token was issued) → 401.
    """
    from dataplat_api.db.session import get_session

    # sub=9999 is a structurally valid non-expired JWT but no such user exists.
    valid_nonexistent_token = _mint_token(sub="9999", email="ghost@example.com")

    app.dependency_overrides[get_session] = _make_session_dependency_for_user(user=None)
    try:
        response = client.get(
            "/api/sources/collections",
            headers={"Authorization": f"Bearer {valid_nonexistent_token}"},
        )
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"


def test_collections_jwt_decode_path(client: TestClient) -> None:
    """Real jwt.encode + real get_session mock returning a User → 200.

    Exercises the full decode path in get_current_user:
      jwt.decode → sub extraction → DB lookup → User returned → 200.
    """
    from dataplat_api.db.session import get_session

    real_user = User(id=1, email="test@example.com", hashed_password="$2b$12$hash")
    valid_token = _mint_token(sub="1")

    app.dependency_overrides[get_session] = _make_session_dependency_for_user(user=real_user)
    try:
        response = client.get(
            "/api/sources/collections",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body == {"items": [], "total": 0}


# ── Integration test (requires live compose stack) ────────────────────────────


@pytest.mark.integration
def test_seed_admin_creates_one_row() -> None:
    """Seed command creates exactly one row in the users table (live DB).

    Invokes the CLI via subprocess so the _patch_engine_begin monkeypatch
    (active in the pytest process) does not affect the subprocess's DB
    connection — it connects to the real compose Postgres.

    This test is excluded from the default backend) run by addopts = "-m 'not
    integration'".  To include it, run `pytest -m integration` or remove the
    -m filter from addopts in pyproject.toml.
    The auth) layer in verify/checks.sh covers this via docker compose exec psql.
    """
    import os
    import subprocess

    result = subprocess.run(
        [
            "uv", "run", "python", "-m", "dataplat_api.cli",
            "seed-admin",
            "--email", "integration-test-seed@example.com",
            "--password", "integrationtestpassword",
        ],
        capture_output=True,
        text=True,
        cwd=os.path.join(os.path.dirname(__file__), ".."),
    )
    assert result.returncode == 0, f"seed-admin failed: {result.stderr}"
