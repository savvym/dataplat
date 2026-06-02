# F-037 Mode B Review — Final

**Sprint:** S037-F-037 — POST /api/recipes  
**Reviewer:** Mode B (post-implementation, diff vs. agreed.md)  
**Date:** 2026-06-02  
**Commit reviewed:** ea5f342  

---

Verdict: **APPROVED**

---

## Findings

No blockers found. All contract requirements and Mode A feedback items verified as correctly implemented. Details follow.

---

### [INFO] B1 — Spec V1 assertions verified

`test_create_recipe_201` (lines 122–149) contains all three mandatory asserts from agreed.md §6:

```python
assert response.status_code == 201
body = response.json()
assert isinstance(body["id"], int)
assert body["name"] == "my-sft"
```

Input name `"my-sft"` is used in both the request JSON and the `refresh_name` parameter, so the echo-back assertion is a real check against handler output, not an accidental tautology. ✅

---

### [INFO] B2 — Spec V2 row-exists assertion verified

`test_create_recipe_db_row_via_session_add` (lines 151–194) uses the mock-based `session.add` side-effect path agreed in §6 ("session.add called with correct Recipe object").

The captured-object assertions are meaningful:

```python
assert len(captured) == 1
assert isinstance(added, Recipe)
assert added.name == "row-check"
assert added.owner_id == 1
```

This verifies the correct ORM object type, name propagation, and owner binding — consistent with the agreed contract. ✅

---

### [INFO] B3 — Spec V3 duplicate-409 mock exact form verified

`test_create_recipe_duplicate_returns_409` (lines 199–224) constructs:

```python
dup_exc = IntegrityError(
    "",
    {},
    Exception('duplicate key value violates unique constraint "recipe_name_key"'),
)
```

Exactly matches the form mandated by agreed.md §5 (F4 from Mode A review). The docstring also explains the guard mechanism, making it maintenance-friendly. Both `status_code == 409` and the exact `{"detail": "Recipe name already exists"}` body are asserted. ✅

---

### [INFO] B4 — F1 StringConstraints: no field_validator, correct Annotated form

`schemas/recipes.py` lines 34–37:

```python
RecipeName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]
```

No `field_validator` anywhere in the file. Structurally identical to `schemas/collections.py` `CollectionName`. The comment on line 32–33 documents why `Field()` is not used. ✅

---

### [INFO] B5 — F2 definition doc-comment verified

`schemas/recipes.py` line 46–48 (within `RecipeCreate`):

```python
# No size/depth guard at the API boundary — intentionally deferred to synthesis-time
# validation (F-082). A Starlette body-size limit can be enforced at the uvicorn/nginx
# layer if pathological payloads become a concern.
definition: dict[str, Any]
```

The module-level docstring (lines 7–21) additionally provides the full rationale with design-doc citations (§2.5, §4.2). Exceeds minimum requirement. ✅

---

### [INFO] B6 — Invariant #5 (Async SQLAlchemy) verified

`routers/recipes.py`:
- Handler declared `async def create_recipe(...)` ✅
- Uses `AsyncSession` ✅
- `session.add(recipe)` — correctly synchronous (as agreed; `add()` is synchronous in SQLAlchemy's async API) ✅
- `await session.commit()` ✅
- `await session.refresh(recipe)` ✅
- `await session.rollback()` on `IntegrityError` ✅
- No `session.query()` anywhere ✅

---

### [INFO] B7 — Rollback-before-re-raise pattern verified

The rollback occurs unconditionally before the constraint-name guard:

```python
except IntegrityError as exc:
    await session.rollback()          # ← always runs first
    if "recipe_name_key" in str(exc.orig):
        raise HTTPException(...)      # 409 branch
    raise                             # re-raise branch — session already clean
```

Both the 409 and the re-raise branch leave the session in a rolled-back state. This was a Mode A non-blocking observation; it is correctly implemented. ✅

---

### [INFO] B8 — Invariant #6 (OpenAPI ↔ TS type sync) verified

`packages/api-types/openapi.json` is included in commit ea5f342 (same commit as the code changes, per `git show --stat`).

Spot-check confirms:
- `/api/recipes` POST path present with `$ref: RecipeCreate` request body and `$ref: RecipeOut` 201 response ✅
- `RecipeCreate` schema: `name` (string, minLength 1, maxLength 255), `definition` (object), `description` (nullable string); `name` + `definition` required ✅
- `RecipeOut` schema: all 7 fields (`id`, `name`, `description`, `owner_id`, `definition`, `created_at`, `updated_at`) with correct types ✅
- OAuth2 security requirement present on the POST operation ✅

---

### [INFO] B9 — Invariant #2 (Storage separation) verified

The handler writes only to Postgres via the ORM (`session.add(recipe)`, `await session.commit()`). No MinIO/S3/blob operations. `definition` JSONB is metadata stored in Postgres, consistent with agreed.md §8. ✅

---

### [INFO] B10 — owner_id nullable in ORM verified

`db/models.py` line 218: `owner_id: Mapped[Optional[int]]` — nullable. Handler sets `owner_id=current_user.id`, so an authenticated request always produces a non-NULL value. Safe. ✅

---

### [INFO] B11 — `main.py` wiring verified

`main.py` line 24 imports `router as recipes_router` and line 58 calls `app.include_router(recipes_router)` — correct position (after `chunks_router`, before `llm_router`). ✅

---

### [INFO] B12 — Test coverage of all agreed.md cases

All 12 test functions match the list in agreed.md §6 exactly:

| Test | Status |
|---|---|
| `test_create_recipe_201` (V1) | ✅ |
| `test_create_recipe_db_row_via_session_add` (V2) | ✅ |
| `test_create_recipe_duplicate_returns_409` (V3) | ✅ |
| `test_create_recipe_no_token_returns_401` | ✅ |
| `test_create_recipe_missing_name_returns_422` | ✅ |
| `test_create_recipe_empty_name_returns_422` | ✅ |
| `test_create_recipe_whitespace_name_returns_422` | ✅ |
| `test_create_recipe_name_too_long_returns_422` | ✅ |
| `test_create_recipe_missing_definition_returns_422` | ✅ |
| `test_create_recipe_definition_not_object_returns_422` | ✅ |
| `test_create_recipe_no_description_returns_201` | ✅ |
| `test_create_recipe_extra_fields_ignored` | ✅ |

422 tests for name/definition validation correctly do NOT override `get_session` — Pydantic rejects the body before the handler runs, so no session mock is needed. This is correct behaviour (mirrors the collections test pattern). ✅

---

### [INFO] B13 — Code style observations

- All files have `from __future__ import annotations` ✅
- Module docstrings on all three new files ✅
- Inline comments justify non-obvious decisions (sync `session.add`, constraint-name guard, rollback ordering) ✅
- No obvious ruff/mypy issues visible; implementer reported both clean ✅

---

## Notes

- **`schema_template_operator_id` omission:** Correctly excluded from both `RecipeCreate` and `RecipeOut` per OQ-1 resolution in agreed.md §7. The ORM column is `nullable=True` with no server default; a NULL INSERT is safe. ✅
- **`definition: {}` at API boundary:** Empty dict accepted; enforcement deferred to F-082 synthesis-time validation. Correct per agreed.md §7 OQ-3. ✅
- **`description` no max-length cap:** Consistent with `operator.description` (Text column, no API cap). Correct per agreed.md §4. ✅
- **RecipeOut 7-field response shape:** Intentional extension of spec minimum, documented in both agreed.md §6 and the `RecipeOut` docstring. Not a deviation. ✅
- **Implementer-reported 209 passing tests:** Cannot independently run the suite in this review context, but the test file structure, dependency-override cleanup, and conftest autouse fixtures all follow the proven pattern from F-009 (`test_sources_collections_create.py`). No structural issues that would cause failures. Verifier is the appropriate next gate for runtime confirmation.

**Leader action:** delegate to verifier with `bash verify/checks.sh backend` (or equivalent) to confirm 209 passing, then flip `feature_list.json` `passes` to `true` for F-037.
