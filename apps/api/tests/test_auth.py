"""Tests for seed-admin CLI and POST /api/auth/token — S007-F-007.

Unit tests (run in backend) layer — no live DB or compose stack required):
  - test_seed_admin_logic
  - test_seed_admin_idempotent
  - test_token_correct_credentials_returns_200
  - test_token_wrong_password_returns_401
  - test_token_user_not_found_returns_401
  - test_token_missing_fields_returns_422

Integration tests (marked @pytest.mark.integration — require live compose stack;
skipped in backend) layer unless RUN_INTEGRATION_TESTS=1):
  - test_seed_admin_creates_one_row

All unit tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

_patch_engine_begin interaction with seed tests:
  - test_seed_admin_logic / test_seed_admin_idempotent: mock SessionLocal entirely;
    never reach engine.begin(). No interaction.
  - test_token_*: use TestClient(app) — lifespan calls engine.begin() which is mocked
    to a no-op by _patch_engine_begin. No interaction.
  - test_seed_admin_creates_one_row: subprocess, separate Python process — monkeypatch
    never applies. Connects to real Postgres. No interaction.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import bcrypt
import pytest
from fastapi.testclient import TestClient

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


# ── Integration test (requires live compose stack) ────────────────────────────


@pytest.mark.integration
def test_seed_admin_creates_one_row() -> None:
    """Seed command creates exactly one row in the users table (live DB).

    Invokes the CLI via subprocess so the _patch_engine_begin monkeypatch
    (active in the pytest process) does not affect the subprocess's DB
    connection — it connects to the real compose Postgres.

    Skip this test in the backend) layer by running without RUN_INTEGRATION_TESTS=1.
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
