# Proposed — S037-F-037: Create Recipe (POST /api/recipes)

## 1. Goal

Expose a `POST /api/recipes` endpoint that accepts a recipe name and a free-form
`definition` JSONB blob, writes a row to the existing `recipe` table, and returns the
newly created record (HTTP 201).  Duplicate `name` values must be rejected with 409.
No new migration is needed: the `recipe` table was created in `0001_baseline_schema.py`
and the `Recipe` ORM model already exists in `dataplat_api/db/models.py`.  Work is
therefore limited to: Pydantic schemas, a router, wiring in `main.py`, and tests.

---

## 2. Files to Create / Change

| File | Action | Purpose |
|---|---|---|
| `apps/api/dataplat_api/schemas/recipes.py` | **Create** | `RecipeCreate` (request) + `RecipeOut` (response) Pydantic models |
| `apps/api/dataplat_api/routers/recipes.py` | **Create** | `POST /api/recipes` handler with async session + 409 guard |
| `apps/api/dataplat_api/main.py` | **Change** | Import and wire the new `recipes_router` via `app.include_router()` |
| `apps/api/tests/test_recipes_create.py` | **Create** | Unit tests covering all 3 verification criteria + edge cases |
| `packages/api-types/openapi.json` | **Regen** | `make codegen` — must be committed in the same commit as the code changes |

**No migration needed.** `0001_baseline_schema.py` already creates the `recipe` table
with all required columns (`id`, `name UNIQUE`, `description`, `owner_id`, `definition JSONB NOT NULL`,
`schema_template_operator_id`, `created_at`, `updated_at`).  The `Recipe` ORM model
at `dataplat_api/db/models.py` lines 212–235 is complete and requires no changes.

---

## 3. Schema Design — `definition` JSONB Column

**Decision: passthrough validation at the API boundary (any dict accepted).**

The `definition` field is typed as `dict[str, Any]` in the Pydantic request model.
Pydantic will reject non-object JSON (e.g. a bare string or array) with 422, but will
accept any well-formed JSON object without further structural inspection.

**Rationale:** The design doc (§2.5, §4.2) describes `definition` as a synthesis
blueprint whose internal shape is determined by the processor/operator pipeline
configured at synthesis time.  Enforcing a fixed schema now would either over-constrain
early users or require a versioned validation registry — both of which are deferred
per the MVP boundary rules.  Synthesis-time validation (e.g. against the operator's
`config_schema` JSON Schema) is the correct place for structural enforcement and is
explicitly out-of-scope for this sprint.  A docstring in the schema file will document
this deferral.

---

## 4. Endpoint Contract

### Request

```
POST /api/recipes
Authorization: Bearer <token>
Content-Type: application/json

{
  "name":        <string, required, min_length=1, max_length=255, stripped>,
  "definition":  <object, required>,
  "description": <string, optional, nullable>
}
```

`name` uses the same validators established for collection names in
`schemas/collections.py`: `min_length=1`, `max_length=255`, `strip_whitespace=True`.
`description` mirrors the nullable `Text` column in the DB (optional, no length cap
at API layer — consistent with `operator.description`).

### Response

| Status | Body | When |
|---|---|---|
| 201 Created | `RecipeOut` (see below) | Successful insert |
| 401 Unauthorized | `{"detail": "Not authenticated"}` + `WWW-Authenticate: Bearer` | No / invalid token |
| 409 Conflict | `{"detail": "Recipe name already exists"}` | `IntegrityError` with `recipe_name_key` in message |
| 422 Unprocessable Entity | Pydantic validation error body | Missing/invalid fields (e.g. `name` absent, empty after strip, >255 chars, `definition` not a JSON object) |

`RecipeOut` response body:
```json
{
  "id":          <int>,
  "name":        <string>,
  "description": <string | null>,
  "owner_id":    <int | null>,
  "definition":  <object>,
  "created_at":  <datetime | null>,
  "updated_at":  <datetime | null>
}
```

The full record is returned (matching the pattern in `SourceCollectionOut`) so that
the client has `id`, timestamps, and the echoed `definition` in one round trip.
`schema_template_operator_id` is omitted from `RecipeOut` for MVP (no FK validation
is performed; adding it is a trivial extension in a later sprint).

---

## 5. Implementation Sketch

### `dataplat_api/schemas/recipes.py`

```python
from __future__ import annotations
from datetime import datetime
from typing import Annotated, Any
from pydantic import BaseModel, ConfigDict, StringConstraints

RecipeName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]

class RecipeCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: RecipeName
    # No size/depth guard at the API boundary — intentionally deferred to synthesis-time
    # validation (F-082). A Starlette body-size limit can be enforced at the uvicorn/nginx
    # layer if pathological payloads become a concern.
    definition: dict[str, Any]
    description: str | None = None

class RecipeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    owner_id: int | None
    definition: dict[str, Any]
    created_at: datetime | None
    updated_at: datetime | None
```

### `dataplat_api/routers/recipes.py`

```python
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import Recipe, User
from dataplat_api.db.session import get_session
from dataplat_api.schemas.recipes import RecipeCreate, RecipeOut

router = APIRouter(prefix="/api/recipes", tags=["recipes"])

@router.post("", response_model=RecipeOut, status_code=status.HTTP_201_CREATED)
async def create_recipe(
    body: RecipeCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RecipeOut:
    recipe = Recipe(
        name=body.name,
        description=body.description,
        owner_id=current_user.id,
        definition=body.definition,
    )
    try:
        session.add(recipe)
        await session.commit()
        await session.refresh(recipe)
    except IntegrityError as exc:
        await session.rollback()
        if "recipe_name_key" in str(exc.orig):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Recipe name already exists",
            )
        raise
    return RecipeOut.model_validate(recipe)
```

**409 constraint name:** The `recipe` table's unique constraint on `name` was created
as `unique=True` on a single column in the migration (no explicit `name=` kwarg), so
Postgres auto-names it `recipe_name_key`.  This matches the same pattern as
`source_collection_name_key` already in production use.

**409 test mock (exact form required):** The `test_create_recipe_duplicate_returns_409`
test MUST use the exact `IntegrityError` mock with the full constraint string — not a
generic `IntegrityError`. The mock must be constructed as:
```python
dup_exc = IntegrityError(
    "", {},
    Exception('duplicate key value violates unique constraint "recipe_name_key"'),
)
```
This mirrors the analogue test for collections. Using a generic `IntegrityError` without
the constraint name string would cause the handler's `if "recipe_name_key" in str(exc.orig)`
guard to fall through to `raise`, making the 409 branch unreachable in the test.

### `dataplat_api/main.py` (change)

Add two lines following the existing `include_router` block:
```python
from dataplat_api.routers.recipes import router as recipes_router
# ...
app.include_router(recipes_router)
```

---

## 6. Verification Mapping

| Verification criterion | Test name |
|---|---|
| V1 — POST returns 201 with `{"id": <int>, "name": "my-sft", ...}` | `test_create_recipe_201` |
| V2 — Row exists in `recipe` table (session.add called with correct object) | `test_create_recipe_db_row_via_session_add` |
| V3 — POST with duplicate name returns 409 | `test_create_recipe_duplicate_returns_409` |

**V1 assertion requirements (explicit):** `test_create_recipe_201` MUST assert:
- `response.status_code == 201`
- `isinstance(body["id"], int)` — confirms id is an integer, not null/string
- `body["name"] == "my-sft"` — confirms the input name is echoed back correctly

**Note on RecipeOut response shape:** `RecipeOut` returns 7 fields (`id`, `name`, `description`,
`owner_id`, `definition`, `created_at`, `updated_at`), which is richer than the spec's minimum
of 2 (`id`, `name`). This is **intentional** and consistent with the `SourceCollectionOut`
precedent established in F-009 — returning the full record saves the client an extra GET
round-trip. This is not a spec deviation; it is a deliberate extension of the minimum contract.

Additional tests to be written (following `test_sources_collections_create.py` pattern):
- `test_create_recipe_no_token_returns_401`
- `test_create_recipe_missing_name_returns_422`
- `test_create_recipe_empty_name_returns_422`
- `test_create_recipe_whitespace_name_returns_422`
- `test_create_recipe_name_too_long_returns_422`
- `test_create_recipe_missing_definition_returns_422`
- `test_create_recipe_definition_not_object_returns_422` (e.g. `"definition": "string"`)
- `test_create_recipe_no_description_returns_201` (optional field absent)
- `test_create_recipe_extra_fields_ignored`

All tests use `FastAPI.dependency_overrides` with `AsyncMock` sessions (no live DB),
mirroring the established pattern in `test_sources_collections_create.py`.

---

## 7. Open Questions

1. **`schema_template_operator_id` in request body?**  ~~The DB column is nullable with
   no FK enforcement at the API layer today (no operator existence check implemented
   in any prior sprint).  Proposal: exclude from `RecipeCreate` and `RecipeOut` for
   MVP; add as an optional field in a later sprint when operator-linking semantics are
   defined.  Reviewer to confirm.~~
   **RESOLVED:** Confirmed safe to omit. The migration (`0001_baseline_schema.py` lines
   252–256) creates the column as `nullable=True` with no `server_default`. The ORM
   (`models.py` lines 223–225) also confirms `nullable=True`. An INSERT omitting the
   column stores NULL, which Postgres accepts without error. Excluding
   `schema_template_operator_id` from both `RecipeCreate` and `RecipeOut` is correct
   and safe for MVP.

2. **`created_by` vs `owner_id`?**  The DB column is `owner_id` (FK → `users.id`),
   which is set to `current_user.id` on creation — this is consistent with
   `SourceCollection.owner_id`.  No ambiguity; documenting here for traceability.

3. **`definition` empty-object `{}` allowed?**  Pydantic `dict[str, Any]` accepts `{}`.
   An empty definition is meaningless at synthesis time but harmless to store; synthesis-
   time validation is deferred.  Proposal: allow it at the API boundary.

4. **`max_length` on `name`:** Set to 255 (matching collection name convention).  The DB
   column is `TEXT` (unbounded), so this is an API-layer guard only.  Reviewer to
   confirm or adjust.

---

## 8. Invariant Compliance

| Invariant | Status |
|---|---|
| **#2 Storage separation + CAS** | Compliant — `definition` JSONB is metadata stored in Postgres. No blob bytes, no MinIO interaction. |
| **#5 Async SQLAlchemy** | Compliant — handler uses `AsyncSession`, `await session.commit()`, `await session.refresh()`. No `session.query()`. |
| **#6 OpenAPI ↔ TS type sync** | Compliant — `make codegen` must be run after the router is wired and the resulting `packages/api-types/openapi.json` diff committed in the same commit. |
