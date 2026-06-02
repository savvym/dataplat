# F-037 Mode A Review — Round 2

**Sprint:** S037-F-037 — POST /api/recipes  
**Reviewer:** Mode A (design review, pre-coding)  
**Date:** 2026-06-02  
**Round:** 2 (re-review after CHANGES_REQUESTED round 1)

---

Verdict: **APPROVED**

---

## Finding-by-finding disposition

### F1 [MEDIUM] — StringConstraints ✅ RESOLVED

**Required:** Replace `field_validator` + `Field(min_length=...)` with
`RecipeName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]`
and declare `name: RecipeName`.

**Verified in revised proposed.md §5 sketch:**

```python
from pydantic import BaseModel, ConfigDict, StringConstraints

RecipeName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]

class RecipeCreate(BaseModel):
    ...
    name: RecipeName
```

This is structurally identical to `schemas/collections.py` lines 23–26 (`CollectionName`).
No `field_validator` present anywhere in the revised sketch. ✅

---

### F2 [LOW] — `definition` doc comment ✅ RESOLVED

**Required:** One-line comment documenting intentional deferral of size/depth validation.

**Verified:** The revised §5 sketch contains:

```python
# No size/depth guard at the API boundary — intentionally deferred to synthesis-time
# validation (F-082). A Starlette body-size limit can be enforced at the uvicorn/nginx
# layer if pathological payloads become a concern.
definition: dict[str, Any]
```

Additionally, a new §3 "Schema Design — `definition` JSONB Column" section now articulates
the full rationale (design-doc §2.5/§4.2 deferral, synthesis-time enforcement, MVP boundary),
which is more than the minimum required. ✅

---

### F3 [LOW] — Explicit V1 asserts + 7-field richer response note ✅ RESOLVED

**Required:** (a) `test_create_recipe_201` must assert `isinstance(body["id"], int)` and
`body["name"] == "my-sft"` explicitly; (b) contract must note 7-field response as
intentional extension of spec's minimum.

**Verified in revised §6:**

> **V1 assertion requirements (explicit):** `test_create_recipe_201` MUST assert:
> - `response.status_code == 201`
> - `isinstance(body["id"], int)` — confirms id is an integer, not null/string
> - `body["name"] == "my-sft"` — confirms the input name is echoed back correctly

And immediately following:

> **Note on RecipeOut response shape:** `RecipeOut` returns 7 fields … This is **intentional**
> and consistent with the `SourceCollectionOut` precedent established in F-009 … This is not a
> spec deviation; it is a deliberate extension of the minimum contract.

Both sub-requirements met. ✅

---

### F4 [NIT] — Exact 409 mock string ✅ RESOLVED

**Required:** Test sketch must embed the exact `IntegrityError` constructor with the full
constraint name string, not a generic `IntegrityError`.

**Verified in revised §5:**

```python
dup_exc = IntegrityError(
    "", {},
    Exception('duplicate key value violates unique constraint "recipe_name_key"'),
)
```

The proposal further explains *why* a generic mock would cause the guard to silently fall
through (`if "recipe_name_key" in str(exc.orig)` would miss), making it clear to the
implementer what the constraint is. ✅

---

### F5 [NIT] — `schema_template_operator_id` OQ-1 resolved ✅ RESOLVED

**Required:** Record the resolution of OQ-1 explicitly in the contract.

**Verified in revised §7:** OQ-1 is struck through and marked **RESOLVED** with explicit
citations:
- Migration `0001_baseline_schema.py` lines 252–256 → `nullable=True`, no `server_default`
- ORM `models.py` lines 223–225 → `nullable=True`
- Explicit statement: "Excluding `schema_template_operator_id` from both `RecipeCreate` and
  `RecipeOut` is correct and safe for MVP."

✅

---

## Invariant compliance re-confirmed

| Invariant | Status |
|---|---|
| **#2 Storage separation + CAS** | `definition` JSONB is metadata in Postgres. No blob bytes, no MinIO. ✅ |
| **#5 Async SQLAlchemy** | `AsyncSession`, `await session.commit()`, `await session.refresh()`, no `session.query()`. ✅ |
| **#6 OpenAPI ↔ TS type sync** | `packages/api-types/openapi.json` listed in file table as "Regen — must be committed in same commit". §8 invariant table confirms. ✅ |

---

## Non-blocking observations (carried forward for implementer awareness)

- **Session rollback before constraint-name guard** is correct: `await session.rollback()`
  is called before the `if "recipe_name_key"` check, so even the re-raised branch leaves the
  session clean. ✅
- **`get_current_user` wiring** (`owner_id=current_user.id`) is safe; auth dependency returns
  `User` ORM object with `id: int`. ✅
- **`definition: {}` allowed at API boundary** — empty dict is syntactically valid; enforcement
  deferred to F-082 synthesis-time validation. Acceptable for MVP. ✅
- **`description` has no max-length cap** — consistent with `operator.description` (Text column,
  no API-layer cap). Acceptable. ✅

---

## Summary

All five round-1 findings (F1 MEDIUM, F2 LOW, F3 LOW, F4 NIT, F5 NIT) are fully and correctly
addressed in the revised `proposed.md`. No new blockers identified. The design is sound,
invariant-compliant, and consistent with established codebase patterns.

**Leader action:** copy `proposed.md` → `agreed.md` and proceed to implementation.
