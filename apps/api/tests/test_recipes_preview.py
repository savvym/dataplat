"""Tests for POST /api/recipes/{id}/preview — S041-F-041.

Unit tests (run in backend layer — no live DB or compose stack required):
  - test_preview_200_returns_samples                         (V1)
  - test_preview_sample_shape_sft_qa                        (V2)
  - test_preview_completes_under_30s                        (V3)
  - test_preview_n_samples_5                                (A1)
  - test_preview_n_samples_too_low                          (A2)
  - test_preview_n_samples_too_high                         (A3)
  - test_preview_requires_auth                              (A4)
  - test_preview_wrong_owner_404                            (A5)
  - test_preview_nonexistent_recipe_404                     (A6)
  - test_preview_unsupported_template_400                   (A7)
  - test_preview_missing_schema_template_400                (A8)
  - test_preview_lance_error_400                            (A9)
  - test_preview_no_chunks_400                              (A10)
  - test_preview_llm_parse_fail_with_fallback               (A11)
  - test_preview_llm_parse_fail_no_fallback_502             (A12)
  - test_preview_bad_prompt_template_field_returns_400      (A13)
  - test_preview_owner_scoping_sql                          (A14)

All tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Mock patterns:
  - LLMGateway: overridden via ``app.dependency_overrides[get_llm_gateway]``
    with a stub that returns a deterministic LLMResponse.
  - get_or_create_chunks_table: patched via ``monkeypatch.setattr`` on the
    import path ``dataplat_api.recipes.preview.get_or_create_chunks_table``.
    The stub returns a chainable object (search → where → select → limit →
    to_arrow → to_pylist) backed by MagicMock.
  - Session: same AsyncMock + scalar_one_or_none pattern as test_recipes_get.py.

_PAST constant (A14 / flake-prevention):
  Uses _PAST = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc) for recipe
  timestamps — consistent with test_recipes_update.py.

SQL-structural test (A14):
  Compiles the captured SELECT with literal_binds=True and asserts both
  ``"owner_id"`` and the mock user's id appear in the WHERE clause.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import Recipe, User
from dataplat_api.db.session import get_session
from dataplat_api.llm.gateway import LLMGateway, LLMResponse, get_llm_gateway
from dataplat_api.main import app

# ── Shared mock user ──────────────────────────────────────────────────────────

_MOCK_USER = User(
    id=7, email="recipe-preview@example.com", hashed_password="$2b$12$hash"
)


async def _override_current_user() -> User:
    return _MOCK_USER


# ── Timestamp constant (flake-prevention) ─────────────────────────────────────

_PAST = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

# ── SFT-QA definition fixture ─────────────────────────────────────────────────

_SFT_QA_DEFINITION: dict[str, Any] = {
    "schema": {
        "template": "sft_synthesis_qa",
        "config": {},
    },
    "filter": {"where": None},
}


# ── Mock recipe row factory ───────────────────────────────────────────────────
# Intentional local definition for self-containment; mirrors test_recipes_get.py
# and test_recipes_update.py _make_recipe_detail pattern.  All 7 ORM-mapped
# attributes populated because RecipeOut uses from_attributes=True.


def _make_recipe_detail(
    id: int,
    name: str,
    description: str | None = None,
    owner_id: int = 7,
    definition: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a MagicMock that looks like a Recipe ORM row.

    RecipeOut uses from_attributes=True, so model_validate() reads attributes
    directly from the object.  All 7 ORM-mapped attributes are populated.
    """
    row = MagicMock(spec=Recipe)
    row.id = id
    row.name = name
    row.description = description
    row.owner_id = owner_id
    row.definition = definition if definition is not None else {}
    row.created_at = _PAST
    row.updated_at = _PAST
    return row


# ── Session mock helper ───────────────────────────────────────────────────────


def _make_session_dep_returning(recipe: MagicMock | None) -> Any:
    """Return a get_session override whose execute().scalar_one_or_none() returns recipe."""

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = recipe
        session.execute = AsyncMock(return_value=result_mock)
        yield session

    return _override


# ── Lance table stub factory ──────────────────────────────────────────────────


def _make_lance_stub(chunks: list[dict]) -> MagicMock:
    """Build a MagicMock Lance table whose fluent query chain returns chunks.

    The chain: table.search().where(...).select(...).limit(...).to_arrow().to_pylist()
    is fully mocked so that the final to_pylist() returns the provided chunks list.
    """
    arrow_mock = MagicMock()
    arrow_mock.to_pylist.return_value = chunks

    limit_mock = MagicMock()
    limit_mock.to_arrow.return_value = arrow_mock

    select_mock = MagicMock()
    select_mock.limit.return_value = limit_mock

    where_mock = MagicMock()
    where_mock.select.return_value = select_mock

    search_mock = MagicMock()
    # .where() and .select() may be called directly on the search result
    # (when where_clause is None, the code calls .select() directly on search()).
    search_mock.where.return_value = where_mock
    search_mock.select.return_value = select_mock

    table_mock = MagicMock()
    table_mock.search.return_value = search_mock
    return table_mock


# ── LLM stub factory ──────────────────────────────────────────────────────────


def _make_llm_stub(content: str) -> LLMGateway:
    """Return a stub LLMGateway whose complete() immediately returns content."""
    stub = MagicMock(spec=LLMGateway)
    stub.complete = AsyncMock(return_value=LLMResponse(content=content, model="mock"))
    return stub


def _make_sft_qa_content(
    instruction: str = "What is X?", output: str = "X is Y."
) -> str:
    """Return a valid JSON string with instruction + output keys."""
    return json.dumps({"instruction": instruction, "output": output})


# ── Default chunks list ───────────────────────────────────────────────────────

_DEFAULT_CHUNKS: list[dict[str, Any]] = [
    {
        "chunk_id": f"c{i}",
        "source_id": 1,
        "text": f"Sample text {i}",
        "token_count": 20 + i,
        "source_refs": None,
        "attr_quality_score": 0.9,
        "attr_lang_code": "en",
    }
    for i in range(5)
]


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    """TestClient with app lifespan initialised.

    Does NOT set dependency overrides — each test sets and clears its own.
    """
    with TestClient(app) as c:
        yield c


# ── V1: 200 with samples ──────────────────────────────────────────────────────


def test_preview_200_returns_samples(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V1 — Valid sft_synthesis_qa recipe, mocked Lance + gateway → 200, len(samples)==3."""
    n = 3
    chunks = _DEFAULT_CHUNKS[:n]
    llm_content = _make_sft_qa_content()
    recipe_row = _make_recipe_detail(id=1, name="sft-qa", definition=_SFT_QA_DEFINITION)

    monkeypatch.setattr(
        "dataplat_api.recipes.preview.get_or_create_chunks_table",
        lambda: _make_lance_stub(chunks),
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(recipe_row)
    app.dependency_overrides[get_llm_gateway] = lambda: _make_llm_stub(llm_content)
    try:
        response = client.post("/api/recipes/1/preview", json={"n_samples": n})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_llm_gateway, None)

    assert response.status_code == 200
    body = response.json()
    assert "samples" in body
    assert len(body["samples"]) == n


# ── V2: sample shape ──────────────────────────────────────────────────────────


def test_preview_sample_shape_sft_qa(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V2 — Each sample in V1 response contains at minimum 'instruction' and 'output' keys."""
    n = 3
    chunks = _DEFAULT_CHUNKS[:n]
    llm_content = _make_sft_qa_content(
        instruction="Describe it.", output="It is a thing."
    )
    recipe_row = _make_recipe_detail(id=1, name="sft-qa", definition=_SFT_QA_DEFINITION)

    monkeypatch.setattr(
        "dataplat_api.recipes.preview.get_or_create_chunks_table",
        lambda: _make_lance_stub(chunks),
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(recipe_row)
    app.dependency_overrides[get_llm_gateway] = lambda: _make_llm_stub(llm_content)
    try:
        response = client.post("/api/recipes/1/preview", json={"n_samples": n})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_llm_gateway, None)

    assert response.status_code == 200
    for sample in response.json()["samples"]:
        assert "instruction" in sample, f"'instruction' missing from sample: {sample}"
        assert "output" in sample, f"'output' missing from sample: {sample}"
        assert isinstance(sample["instruction"], str)
        assert isinstance(sample["output"], str)


# ── V3: completes under 30 s ──────────────────────────────────────────────────


def test_preview_completes_under_30s(monkeypatch: pytest.MonkeyPatch) -> None:
    """V3 — Preview of 3 samples completes in under 30 s with deterministic mocks.

    Calls ``run_preview`` directly via ``asyncio.run(asyncio.wait_for(..., timeout=30))``
    to enforce the time budget without going through the full ASGI stack.  The
    30-second requirement is a test-harness check against mocked LLM + Lance;
    production latency is governed by the Anthropic SDK timeout (agreed.md §10).
    """
    from dataplat_api.recipes.preview import run_preview

    n = 3
    chunks = _DEFAULT_CHUNKS[:n]
    llm_content = _make_sft_qa_content()
    llm_stub = _make_llm_stub(llm_content)

    monkeypatch.setattr(
        "dataplat_api.recipes.preview.get_or_create_chunks_table",
        lambda: _make_lance_stub(chunks),
    )

    async def _run() -> None:
        samples = await run_preview(
            where_clause=None,
            n_samples=n,
            template="sft_synthesis_qa",
            config={},
            llm=llm_stub,
        )
        assert len(samples) == n
        for sample in samples:
            assert "instruction" in sample
            assert "output" in sample

    asyncio.run(asyncio.wait_for(_run(), timeout=30))


# ── A1: n_samples=5 ──────────────────────────────────────────────────────────


def test_preview_n_samples_5(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A1 — n_samples=5 returns exactly 5 items."""
    n = 5
    chunks = _DEFAULT_CHUNKS[:n]
    llm_content = _make_sft_qa_content()
    recipe_row = _make_recipe_detail(
        id=3, name="five-samples", definition=_SFT_QA_DEFINITION
    )

    monkeypatch.setattr(
        "dataplat_api.recipes.preview.get_or_create_chunks_table",
        lambda: _make_lance_stub(chunks),
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(recipe_row)
    app.dependency_overrides[get_llm_gateway] = lambda: _make_llm_stub(llm_content)
    try:
        response = client.post("/api/recipes/3/preview", json={"n_samples": n})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_llm_gateway, None)

    assert response.status_code == 200
    assert len(response.json()["samples"]) == n


# ── A2: n_samples=2 → 422 ────────────────────────────────────────────────────


def test_preview_n_samples_too_low(client: TestClient) -> None:
    """A2 — n_samples=2 (below minimum of 3) → 422 Unprocessable Entity."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = client.post("/api/recipes/1/preview", json={"n_samples": 2})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422


# ── A3: n_samples=6 → 422 ────────────────────────────────────────────────────


def test_preview_n_samples_too_high(client: TestClient) -> None:
    """A3 — n_samples=6 (above maximum of 5) → 422 Unprocessable Entity."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = client.post("/api/recipes/1/preview", json={"n_samples": 6})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422


# ── A4: no token → 401 ───────────────────────────────────────────────────────


def test_preview_requires_auth(client: TestClient) -> None:
    """A4 — No bearer token → 401 with WWW-Authenticate: Bearer.

    No dependency override — real oauth2_scheme (auto_error=True) raises 401
    automatically when the Authorization header is absent.
    """
    response = client.post("/api/recipes/1/preview", json={})
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


# ── A5: wrong owner → 404 ────────────────────────────────────────────────────


def test_preview_wrong_owner_404(client: TestClient) -> None:
    """A5 — Valid token, recipe owned by a different user → 404 'Recipe not found'.

    The owner-scoped query returns None (same as not-found), so both cases
    produce identical 404 detail — no enumeration leak.
    """
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(None)
    try:
        response = client.post("/api/recipes/99/preview", json={})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 404
    assert response.json() == {"detail": "Recipe not found"}


# ── A6: nonexistent recipe → 404 ─────────────────────────────────────────────


def test_preview_nonexistent_recipe_404(client: TestClient) -> None:
    """A6 — Valid token, recipe id does not exist → 404 'Recipe not found'."""
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(None)
    try:
        response = client.post("/api/recipes/999999/preview", json={})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 404
    assert response.json() == {"detail": "Recipe not found"}


# ── A7: unsupported template → 400 ───────────────────────────────────────────


def test_preview_unsupported_template_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A7 — schema.template == 'cpt_plain' (unsupported) → 400 with exact detail."""
    definition = {"schema": {"template": "cpt_plain", "config": {}}}
    recipe_row = _make_recipe_detail(id=4, name="bad-template", definition=definition)

    monkeypatch.setattr(
        "dataplat_api.recipes.preview.get_or_create_chunks_table",
        lambda: _make_lance_stub(_DEFAULT_CHUNKS[:3]),
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(recipe_row)
    app.dependency_overrides[get_llm_gateway] = lambda: _make_llm_stub("")
    try:
        response = client.post("/api/recipes/4/preview", json={})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_llm_gateway, None)

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Preview supports only 'sft_synthesis_qa' in MVP; got 'cpt_plain'"
    }


# ── A8: missing schema.template → 400 ────────────────────────────────────────


def test_preview_missing_schema_template_400(client: TestClient) -> None:
    """A8 — definition is missing schema.template entirely → 400 with exact detail."""
    # definition has no 'schema' key at all.
    definition: dict[str, Any] = {}
    recipe_row = _make_recipe_detail(id=5, name="no-template", definition=definition)

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(recipe_row)
    try:
        response = client.post("/api/recipes/5/preview", json={})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Recipe definition missing required field: schema.template"
    }


# ── A9: Lance raises exception → 400 ─────────────────────────────────────────


def test_preview_lance_error_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A9 — Lance raises an exception (bad filter.where syntax) → 400 'Lance query error: ...'."""
    definition = {
        "schema": {"template": "sft_synthesis_qa", "config": {}},
        "filter": {"where": "INVALID SQL !!"},
    }
    recipe_row = _make_recipe_detail(id=6, name="bad-filter", definition=definition)

    def _raise_lance() -> None:
        raise RuntimeError("DataFusion error: syntax error at INVALID")

    monkeypatch.setattr(
        "dataplat_api.recipes.preview.get_or_create_chunks_table",
        _raise_lance,
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(recipe_row)
    app.dependency_overrides[get_llm_gateway] = lambda: _make_llm_stub("")
    try:
        response = client.post("/api/recipes/6/preview", json={})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_llm_gateway, None)

    assert response.status_code == 400
    assert "Lance query error" in response.json()["detail"]


# ── A10: zero chunks → 400 ───────────────────────────────────────────────────


def test_preview_no_chunks_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A10 — Zero chunks match the filter → 400 with exact detail."""
    recipe_row = _make_recipe_detail(
        id=7, name="empty-result", definition=_SFT_QA_DEFINITION
    )

    monkeypatch.setattr(
        "dataplat_api.recipes.preview.get_or_create_chunks_table",
        lambda: _make_lance_stub([]),  # empty result
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(recipe_row)
    app.dependency_overrides[get_llm_gateway] = lambda: _make_llm_stub("")
    try:
        response = client.post("/api/recipes/7/preview", json={})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_llm_gateway, None)

    assert response.status_code == 400
    assert response.json() == {
        "detail": "No matching chunks for preview; check recipe filter.where"
    }


# ── A11: LLM non-JSON + fallback=true → 200 ──────────────────────────────────


def test_preview_llm_parse_fail_with_fallback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A11 — LLM returns non-JSON, fallback_on_failure=true → 200; sample derived."""
    definition = {
        "schema": {
            "template": "sft_synthesis_qa",
            "config": {"fallback_on_failure": True},
        },
    }
    chunks = _DEFAULT_CHUNKS[:3]
    recipe_row = _make_recipe_detail(id=8, name="fallback-true", definition=definition)

    monkeypatch.setattr(
        "dataplat_api.recipes.preview.get_or_create_chunks_table",
        lambda: _make_lance_stub(chunks),
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(recipe_row)
    app.dependency_overrides[get_llm_gateway] = lambda: _make_llm_stub(
        "NOT JSON AT ALL"
    )
    try:
        response = client.post("/api/recipes/8/preview", json={})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_llm_gateway, None)

    assert response.status_code == 200
    body = response.json()
    assert "samples" in body
    # Each sample should use the fallback structure.
    for sample in body["samples"]:
        assert "instruction" in sample
        assert "output" in sample
        assert sample["output"] == "NOT JSON AT ALL"


# ── A12: LLM non-JSON + fallback=false → 502 ─────────────────────────────────


def test_preview_llm_parse_fail_no_fallback_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A12 — LLM returns non-JSON, fallback_on_failure=false (or absent) → 502 with exact detail."""
    recipe_row = _make_recipe_detail(
        id=9, name="fallback-false", definition=_SFT_QA_DEFINITION
    )
    chunks = _DEFAULT_CHUNKS[:3]

    monkeypatch.setattr(
        "dataplat_api.recipes.preview.get_or_create_chunks_table",
        lambda: _make_lance_stub(chunks),
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(recipe_row)
    app.dependency_overrides[get_llm_gateway] = lambda: _make_llm_stub(
        "this is not json"
    )
    try:
        response = client.post("/api/recipes/9/preview", json={})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_llm_gateway, None)

    assert response.status_code == 502
    assert response.json() == {
        "detail": "LLM returned non-JSON output and fallback_on_failure is false"
    }


# ── A13: bad prompt template field → 400 ─────────────────────────────────────


def test_preview_bad_prompt_template_field_returns_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A13 — prompt_template references unknown field → 400 with exact detail."""
    definition = {
        "schema": {
            "template": "sft_synthesis_qa",
            "config": {"prompt_template": "Tell me about {nonexistent_field}."},
        },
    }
    chunks = _DEFAULT_CHUNKS[:3]
    recipe_row = _make_recipe_detail(id=10, name="bad-field", definition=definition)

    monkeypatch.setattr(
        "dataplat_api.recipes.preview.get_or_create_chunks_table",
        lambda: _make_lance_stub(chunks),
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(recipe_row)
    app.dependency_overrides[get_llm_gateway] = lambda: _make_llm_stub("")
    try:
        response = client.post("/api/recipes/10/preview", json={})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_llm_gateway, None)

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Prompt template references unknown chunk field: 'nonexistent_field'"
    }


# ── A14: owner-scoping SQL structural test ────────────────────────────────────


def test_preview_owner_scoping_sql(client: TestClient) -> None:
    """A14 — SQL structural: compile the SELECT with literal_binds=True; owner_id in WHERE.

    Verification approach mirrors test_get_recipe_owner_id_in_query (F-039):
      1. Capture the Select object from execute() via call_args_list.
      2. Compile with literal_binds=True so bound parameter values appear as literals.
      3. Assert "owner_id" and the mock user id (7) both appear in the compiled SQL.

    This guards against accidentally dropping the owner_id filter.
    The handler returns 404 (scalar_one_or_none=None) — that's fine; we only
    care about the SQL that was executed.
    """
    captured_session: list[AsyncMock] = []

    async def _capturing_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = (
            None  # 404 — we care about the SQL
        )
        session.execute = AsyncMock(return_value=result_mock)
        captured_session.append(session)
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _capturing_session
    app.dependency_overrides[get_llm_gateway] = lambda: _make_llm_stub("")
    try:
        client.post("/api/recipes/5/preview", json={})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_llm_gateway, None)

    assert len(captured_session) == 1
    session_mock = captured_session[0]
    assert session_mock.execute.call_count == 1

    # Compile the captured SELECT with literal_binds=True so bound parameter
    # values (id = 5, owner_id = 7) are rendered as literals in the SQL string.
    stmt = session_mock.execute.call_args_list[0].args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "owner_id" in compiled, f"'owner_id' not in compiled SQL: {compiled}"
    assert str(_MOCK_USER.id) in compiled, (
        f"user id {_MOCK_USER.id!r} not in compiled SQL: {compiled}"
    )
