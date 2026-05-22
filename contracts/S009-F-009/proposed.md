# S009-F-009 — Proposed Contract

**Status:** PROPOSED (Iter 2)
**Date drafted:** 2026-05-22
**Author:** Implementer (Claude)
**Sprint-id:** S009-F-009

---

## Iter-1 → Iter-2 Changelog

| Item | Change |
|---|---|
| H-1 (D4 / §9 R2) | Chose resolution path (b): tightened `IntegrityError` match to exact constraint name `"source_collection_name_key"` (confirmed on live DB via `\d source_collection`). DBAPI driver (asyncpg 0.30.0) documented with representative `str(exc.orig)`. |
| M-1 (§6 V2) | Tightened psql V2 assertion to `WHERE name='test-coll-checks' AND owner_id IS NOT NULL`. |
| M-2 (§3 / §6) | Explicit `all)` chain position stated: after `auth`, before `buckets`. Full updated `all)` block shown. |
| M-3 (§6) | Full token-minting preamble added to `collections)` layer pseudocode with `COLL_TOKEN` / `mktemp` pattern, variable naming rationale, and standalone-run caveat. |
| L-3 (§3 / §6) | Manual OpenAPI regen command inlined from S008 precedent (commit `91a2651`). |
| L-2 (optional, applied) | Added inline comment on `SourceCollectionOut.owner_id` noting "nullable at ORM level; always set by this POST handler; null is a data-integrity anomaly". |

---

## §1 Sprint Identity

- **Sprint-id:** S009-F-009
- **Feature-id:** F-009
- **Title:** Create a source collection — `POST /api/sources/collections`

### Dependency confirmation

| Dependency | Required state | Evidence |
|---|---|---|
| F-002 (Postgres baseline) | `passes: true` | `feature_list.json` confirms `"passes": true`. `source_collection` table confirmed in `db/models.py` and on live DB via `\d source_collection`. |
| F-008 (auth gate) | `passes: true` | Commit `750326d` message: `feat(auth): F-008 PASS — close sprint S008-F-008`. `feature_list.json` confirms `"passes": true`. |

Both dependencies are confirmed `passes: true`. Proceeding is correct per CLAUDE.md sprint workflow.

---

## §2 Goal & Non-Goals

**Goal:** Implement `POST /api/sources/collections` in the existing `sources` router so that authenticated users can create a new `source_collection` row in Postgres and receive the created record back with HTTP 201.

### Explicit non-goals (out of scope for this sprint)

- **F-010** — List collections (`GET /api/sources/collections` real body; the stub remains untouched).
- **F-011** — Upload a PDF source.
- **F-012** — Dagster partition notification after upload.
- **F-013** — `GET /api/sources/{id}` source detail.
- Pagination query parameters on any route.
- Granular ACL beyond the F-008 `get_current_user` auth gate (no per-resource ownership enforcement beyond recording `owner_id`).
- Soft-delete / archive of collections.
- `GET /api/sources/collections/{id}` — no individual-collection retrieval route in MVP.

---

## §3 Files Touched

| Path | New / Modified | Summary |
|---|---|---|
| `apps/api/dataplat_api/routers/sources.py` | MODIFIED | Add `POST /collections` handler using `get_current_user` + `get_session` dependencies; catch `IntegrityError` on constraint name `"source_collection_name_key"` and return 409. |
| `apps/api/dataplat_api/schemas/collections.py` | MODIFIED | Add `SourceCollectionCreate` (request body) and `SourceCollectionOut` (response) schemas. Leave `CollectionListResponse.items` as `list[Any]` — F-010 will narrow it. |
| `apps/api/tests/test_sources_collections_create.py` | NEW | Unit tests covering V1 (201 happy path), V2 (DB row + `owner_id == 1`), V3 (409 duplicate), auth gate (401 no token), 422 on missing / empty / whitespace / too-long `name`. |
| `verify/checks.sh` | MODIFIED | Add new `collections)` layer (token-mint preamble + V1/V2/V3 checks including `owner_id IS NOT NULL`); insert `collections` into the `all)` chain **after `auth`, before `buckets`**. |
| `packages/api-types/openapi.json` | MODIFIED | Regenerated in the same commit as the router change (hard invariant #6). No Makefile yet — use manual export: `cd apps/api && uv run python -c 'import json; from dataplat_api.main import app; print(json.dumps(app.openapi(), indent=2))' > ../../packages/api-types/openapi.json` (S008 precedent, commit `91a2651`). |
| `apps/api/tests/conftest.py` | NOT MODIFIED | Existing `_patch_engine_begin` and `_patch_httpx_no_ssl` fixtures are sufficient. No shared collections fixture needed — each test constructs its own mock session inline. |

---

## §4 Design Decisions

### D1 — Request schema fields

**`SourceCollectionCreate`:**

```python
class SourceCollectionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, strip_whitespace=True)
    dataset_card_md: str | None = None

    model_config = ConfigDict(extra="ignore")
```

- `name` is required (no default). `min_length=1` rejects empty strings after stripping. `max_length=255` is a pragmatic upper bound for a human-readable identifier; well within Postgres `TEXT` limits.
- `strip_whitespace=True` strips leading/trailing whitespace before the length check — a name that is purely whitespace becomes `""` and fails `min_length=1`.
- `dataset_card_md` is optional, defaulting to `None`. The design doc lists it as nullable in `source_collection`; V1 in `feature_list.json` sends it as a non-null string, confirming it must be accepted when present.
- `extra="ignore"` (Pydantic v2 `ConfigDict`): unknown fields in the request body are silently discarded. Strict rejection (`extra="forbid"`) would break forward-compatibility when new optional fields are added.

### D2 — Response schema fields

**`SourceCollectionOut`:**

```python
class SourceCollectionOut(BaseModel):
    id: int
    name: str
    # Nullable at the ORM/DB level (source_collection.owner_id is a nullable FK),
    # but always populated by this POST handler via current_user.id.
    # F-010 / F-011 implementers: a null owner_id is a data-integrity anomaly,
    # not a normal case produced by this endpoint.
    owner_id: int | None
    dataset_card_md: str | None
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
```

V1 only verifies `{id, name}` but the design doc §4.1 lists all six fields on `source_collection`. Returning the full record is the right choice because:
1. Clients that create a collection immediately need `created_at` / `updated_at` for display.
2. There is no planned `GET /api/sources/collections/{id}` in MVP — a richer POST response avoids forcing a second round-trip that has no endpoint.
3. The extra fields are zero-cost (already fetched from the ORM object via `session.refresh()`).
4. F-010 will use the same `SourceCollectionOut` for its list items — defining it here creates a single source of truth.

`from_attributes=True` (Pydantic v2 equivalent of `orm_mode=True`) allows constructing `SourceCollectionOut` directly from a `SourceCollection` ORM instance.

### D3 — HTTP status code semantics

| Scenario | Status code | Rationale |
|---|---|---|
| Successful creation | 201 Created | V1 explicitly requires 201. Correct semantics for resource creation. |
| Duplicate name | 409 Conflict | V3 explicitly requires 409. `UNIQUE` constraint violation is a conflict. |
| Malformed body (missing `name`, type error, validation failure) | 422 Unprocessable Entity | FastAPI default for Pydantic validation failure. |
| No / invalid token | 401 Unauthorized | F-008 gate — `get_current_user` raises before the handler body runs. |

The `status_code=201` is set on the route decorator: `@router.post("/collections", response_model=SourceCollectionOut, status_code=201)`.

### D4 — Duplicate-name detection strategy

**Chosen approach: rely on Postgres `UNIQUE` constraint + catch `sqlalchemy.exc.IntegrityError`, matching the specific constraint name `"source_collection_name_key"`.**

#### DBAPI driver confirmation

The project uses **asyncpg** (confirmed: `apps/api/pyproject.toml` line 16: `"asyncpg==0.30.0"`; `DATABASE_URL` uses `postgresql+asyncpg://`). With asyncpg, `exc.orig` is an instance of `asyncpg.exceptions.UniqueViolationError`. A representative `str(exc.orig)` produced when the `source_collection.name` UNIQUE constraint is violated is:

```
duplicate key value violates unique constraint "source_collection_name_key"
```

#### Constraint name confirmation

The constraint name `source_collection_name_key` is the Postgres auto-generated name from `sa.Column("name", sa.Text, nullable=False, unique=True)` in the alembic baseline migration (`0001_baseline_schema.py`). Confirmed on the live dev DB via `\d source_collection`:

```
"source_collection_name_key" UNIQUE CONSTRAINT, btree (name)
```

#### Tightened match (resolution path b — precise, future-proof)

The handler matches the exact constraint name rather than the generic substring `"unique"`:

```python
from sqlalchemy.exc import IntegrityError

try:
    session.add(collection)
    await session.commit()
    await session.refresh(collection)
except IntegrityError as exc:
    await session.rollback()
    if "source_collection_name_key" in str(exc.orig):
        raise HTTPException(status_code=409, detail="Collection name already exists")
    raise
```

**Why tighten to the constraint name:** A generic `"unique"` substring scan would match any future UNIQUE constraint added to `source_collection` (e.g., a composite index), producing a misleading "Collection name already exists" 409 for unrelated violations. The constraint name `"source_collection_name_key"` is precise.

**Driver portability:** asyncpg and psycopg3 both include the constraint name in the error string when reporting a UniqueViolation. Postgres itself puts the constraint name in the error message, so it appears in `str(exc.orig)` regardless of driver. If the project ever switches DBAPI drivers, the constraint-name match continues to work correctly.

**Why UNIQUE + IntegrityError over a pre-check SELECT:** A pre-check SELECT followed by INSERT has a TOCTOU race: two concurrent requests could both pass the SELECT check and both attempt INSERT, causing one to fail at the DB level anyway. Catching `IntegrityError` is atomic and is the standard SQLAlchemy pattern for UNIQUE violations.

### D5 — `owner_id` assignment

`owner_id` is set to `current_user.id`, sourced from the `get_current_user` dependency (F-008 carry-over requirement). The `User` ORM object returned by `get_current_user` carries `.id` as a populated integer. No additional DB lookup is needed.

### D6 — Validation: name length and character rules

- **Minimum length:** 1 character (after strip). Rejects empty string and pure-whitespace input.
- **Maximum length:** 255 characters. Pragmatic upper bound for a human-readable identifier.
- **Whitespace stripping:** Leading/trailing whitespace stripped via `Field(strip_whitespace=True)`. Prevents `"test-coll"` and `"  test-coll  "` from creating duplicate-but-distinct names.
- **Character set:** No restriction beyond UTF-8 (Postgres `TEXT`). Restricting to ASCII or alphanumeric-plus-hyphen would be premature at MVP; the design doc specifies no character constraint on `source_collection.name`.

### D7 — Whether to update `CollectionListResponse.items` in this sprint

**Decision: leave `CollectionListResponse.items` as `list[Any]`; F-010 will narrow it.**

Tightening `items` from `list[Any]` to `list[SourceCollectionOut]` in this sprint would change the OpenAPI schema for the `GET /collections` endpoint, coupling two features in one `make codegen` run. F-010 owns the list body and will naturally update `CollectionListResponse.items`. The carry-over obligation is explicitly noted in §8.

### D8 — Error response shape for 409

**Use FastAPI's default `{"detail": "..."}` shape.** Uniform with all other error responses in the API (confirmed from `test_auth.py` assertion patterns). The 409 response body:

```json
{"detail": "Collection name already exists"}
```

---

## §5 Endpoint Contract

```
POST /api/sources/collections
Authorization: Bearer <token>   (required — 401 if absent/invalid)
Content-Type: application/json

Request body:
{
  "name":            string   (required; 1–255 chars; leading/trailing whitespace stripped)
  "dataset_card_md": string   (optional; nullable; HF-style markdown description)
}

Response 201 Created:
{
  "id":              integer
  "name":            string
  "owner_id":        integer | null   (always set by this handler; null is a data-integrity anomaly)
  "dataset_card_md": string | null
  "created_at":      string (ISO 8601 datetime with timezone) | null
  "updated_at":      string (ISO 8601 datetime with timezone) | null
}

Response 401 Unauthorized (no / invalid / expired token):
{"detail": "Could not validate credentials"}

Response 409 Conflict (duplicate name):
{"detail": "Collection name already exists"}

Response 422 Unprocessable Entity (missing required field, type error, name empty/too long):
{"detail": [...]}    (FastAPI default validation error shape)

OpenAPI summary: "Create Source Collection"
OpenAPI operationId: "create_source_collection_api_sources_collections_post"
tags: ["sources"]
```

V1/V2/V3 resolution:
- V1: `POST {"name": "test-coll", "dataset_card_md": "desc"}` → 201 with `id` (int) and `"name": "test-coll"`.
- V2: `session.add()` + `await session.commit()` writes the row; `collections)` V2 confirms via psql with `owner_id IS NOT NULL`.
- V3: Second `POST` with same name → `IntegrityError` with `"source_collection_name_key"` in `str(exc.orig)` → 409.

---

## §6 Verification Plan

| ID | What | How | Pass criteria |
|----|------|-----|---------------|
| V1 | POST happy path returns 201 with `id` (int) and `name` match | Unit test `test_create_collection_201` + `checks.sh collections)` V1 (curl with `COLL_TOKEN`) | Status 201; body `"id"` is int; `"name" == "test-coll-checks"` |
| V2 | Row exists in DB with correct `owner_id` after creation | Unit test `test_create_collection_db_row_via_session_add` asserts `owner_id == 1` on object passed to `session.add()` + `checks.sh collections)` V2 psql: `WHERE name='test-coll-checks' AND owner_id IS NOT NULL` | Row found; `owner_id IS NOT NULL` on live DB |
| V3 | Duplicate POST returns 409 | Unit test `test_create_collection_duplicate_returns_409` + `checks.sh collections)` V3 (second curl with same name) | Status 409; body `{"detail": "Collection name already exists"}` |
| Auth-gate | No-token POST returns 401 | Unit test `test_create_collection_no_token_returns_401` | Status 401; `WWW-Authenticate: Bearer` header present |
| 422-missing | POST without `name` returns 422 | Unit test `test_create_collection_missing_name_returns_422` | Status 422 |
| 422-empty | POST with `name: ""` returns 422 | Unit test `test_create_collection_empty_name_returns_422` | Status 422 |
| 422-whitespace | POST with `name: "   "` returns 422 | Unit test `test_create_collection_whitespace_name_returns_422` | Status 422 (stripped to `""` → `min_length=1` fails) |
| 422-toolong | POST with `name` of 256 chars returns 422 | Unit test `test_create_collection_name_too_long_returns_422` | Status 422 |
| Optional-card-null | POST without `dataset_card_md` returns 201 | Unit test `test_create_collection_no_card_md_returns_201` | Status 201; `"dataset_card_md": null` |
| OpenAPI sync | `packages/api-types/openapi.json` reflects new POST route | `checks.sh contract)` or `git diff --exit-code packages/api-types/` after manual regen | No uncommitted diff; openapi.json in same commit as router change |

### `collections)` layer: full token-mint preamble and checks

```bash
collections)
  COMPOSE="docker/docker-compose.dev.yml"
  [[ -f "$COMPOSE" ]] || { echo "no $COMPOSE yet"; exit 0; }

  FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"

  echo "--- collections: mint Bearer token ---"
  COLL_TOKEN_BODY=$(mktemp)
  COLL_TOKEN_STATUS=$(curl -sS -X POST \
    "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
    -d "username=admin@example.com&password=testpassword123" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -w '%{http_code}' -o "$COLL_TOKEN_BODY")
  test "$COLL_TOKEN_STATUS" = "200" \
    || { echo "FAIL: collections) could not mint token (status $COLL_TOKEN_STATUS) — run 'bash $0 auth' first"; rm -f "$COLL_TOKEN_BODY"; exit 1; }
  COLL_TOKEN=$(python3 -c "import json; print(json.load(open('$COLL_TOKEN_BODY'))['access_token'])")
  rm -f "$COLL_TOKEN_BODY"

  echo "--- collections V1: POST returns 201 with id (int) and name ---"
  COLL_BODY=$(mktemp)
  COLL_STATUS=$(curl -sS -X POST \
    "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections" \
    -H "Authorization: Bearer $COLL_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"name": "test-coll-checks", "dataset_card_md": "desc"}' \
    -w '%{http_code}' -o "$COLL_BODY")
  test "$COLL_STATUS" = "201" \
    || { echo "FAIL: collections V1 returned $COLL_STATUS: $(cat "$COLL_BODY")"; rm -f "$COLL_BODY"; exit 1; }
  python3 -c "
import json, sys
body = json.load(open('$COLL_BODY'))
assert isinstance(body.get('id'), int), f'id not int: {body}'
assert body.get('name') == 'test-coll-checks', f'name mismatch: {body}'
print('  V1 OK: id =', body['id'], 'name =', body['name'])
" || { echo "FAIL: collections V1 response shape incorrect"; rm -f "$COLL_BODY"; exit 1; }
  rm -f "$COLL_BODY"

  echo "--- collections V2: row exists with owner_id IS NOT NULL ---"
  docker compose -f "$COMPOSE" exec -T postgres \
    psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
      "SELECT id FROM source_collection WHERE name='test-coll-checks' AND owner_id IS NOT NULL" \
    | grep -qE '^[0-9]+$' \
    || { echo "FAIL: collections V2 row not found or owner_id is null"; exit 1; }
  echo "  V2 OK: row exists with non-null owner_id"

  echo "--- collections V3: duplicate name returns 409 ---"
  DUP_STATUS=$(curl -sS -X POST \
    "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections" \
    -H "Authorization: Bearer $COLL_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"name": "test-coll-checks"}' \
    -o /dev/null -w '%{http_code}')
  test "$DUP_STATUS" = "409" \
    || { echo "FAIL: collections V3 returned $DUP_STATUS (expected 409)"; exit 1; }
  echo "  V3 OK: duplicate name → 409"
  ;;
```

Variable name `COLL_TOKEN` (not `TOKEN`) avoids namespace collision if `collections)` runs in the same shell session as `auth)` or `dagster)`. Standalone-run caveat: if the admin user has not been seeded, the token-mint guard exits with "run `bash $0 auth` first".

### `all)` chain position

`collections` is inserted **after `auth`, before `buckets`**:

```bash
all)
  bash "$0" smoke
  bash "$0" infra
  bash "$0" backend
  bash "$0" frontend
  bash "$0" contract
  bash "$0" migration
  bash "$0" auth        # seeds admin@example.com — required by collections) token mint
  bash "$0" collections # NEW
  bash "$0" buckets
  bash "$0" dagster
  bash "$0" runs
  ;;
```

`auth` is guaranteed to run first in `all)`, so `admin@example.com` / `testpassword123` exist when `collections)` mints its token. `collections)` must not appear before `auth` in the `all)` chain.

### OpenAPI sync procedure

No Makefile exists yet (same deferred treatment as S008). Regenerate manually after the router change:

```bash
cd apps/api && uv run python -c \
  'import json; from dataplat_api.main import app; print(json.dumps(app.openapi(), indent=2))' \
  > ../../packages/api-types/openapi.json
```

Commit `packages/api-types/openapi.json` in the **same commit** as the router and schema changes. The `contract)` layer's `[[ -f Makefile ]] || exit 0` guard prevents CI failure while the Makefile is absent.

---

## §7 Tests — List of Unit Test Cases

All tests live in `apps/api/tests/test_sources_collections_create.py`. All are pure unit tests using `TestClient(app)` with the `conftest.py` autouse fixtures (`_patch_engine_begin`, `_patch_httpx_no_ssl`). No live Postgres required.

Pattern for DB-touching tests:
- Override `get_current_user` → `app.dependency_overrides[get_current_user] = lambda: mock_user` (returns `User(id=1, ...)`).
- Override `get_session` → `app.dependency_overrides[get_session] = _make_session_dep(...)` returning an `AsyncMock` session.
- Always restore overrides in a `finally` block.

For auth-gate tests: no overrides — real `oauth2_scheme` / `get_current_user` enforce 401.

---

### Test 1: `test_create_collection_201`
- Override `get_current_user` → mock user (id=1).
- Override `get_session` → mock session: `session.add` is `MagicMock()`, `session.commit` is `AsyncMock()`, `session.refresh` is `AsyncMock` with `side_effect` that sets `obj.id = 42`, `obj.name = "test-coll"`, `obj.created_at = datetime(2026, 5, 22, tzinfo=timezone.utc)`, `obj.updated_at = same`.
- POST `{"name": "test-coll", "dataset_card_md": "desc"}`.
- Assert: status 201; `body["id"] == 42` (int); `body["name"] == "test-coll"`.

### Test 2: `test_create_collection_db_row_via_session_add`
- Same overrides as Test 1.
- POST `{"name": "test-coll-row"}`.
- Assert: `session.add.call_count == 1`; argument to `session.add` is a `SourceCollection` with `name == "test-coll-row"` and `owner_id == 1`.
- Assert: `session.commit` awaited once.
- Unit-test analogue of V2 (correct `owner_id` recorded).

### Test 3: `test_create_collection_duplicate_returns_409`
- Override `get_current_user` → mock user.
- Override `get_session` → mock session where `session.commit` raises:
  ```python
  IntegrityError(
      "",
      {},
      Exception('duplicate key value violates unique constraint "source_collection_name_key"'),
  )
  ```
- POST `{"name": "duplicate-name"}`.
- Assert: status 409; `body == {"detail": "Collection name already exists"}`.

### Test 4: `test_create_collection_no_token_returns_401`
- No dependency overrides (real `oauth2_scheme` / `get_current_user` active).
- POST `{"name": "test-coll"}` with no `Authorization` header.
- Assert: status 401; `response.headers["WWW-Authenticate"] == "Bearer"`.

### Test 5: `test_create_collection_missing_name_returns_422`
- Override `get_current_user` → mock user (bypasses auth; tests Pydantic validation path).
- POST `{"dataset_card_md": "desc"}` (no `name` field).
- Assert: status 422.

### Test 6: `test_create_collection_empty_name_returns_422`
- Override `get_current_user` → mock user.
- POST `{"name": ""}`.
- Assert: status 422.

### Test 7: `test_create_collection_whitespace_name_returns_422`
- Override `get_current_user` → mock user.
- POST `{"name": "   "}`.
- Assert: status 422 (whitespace stripped to `""` → `min_length=1` fails).

### Test 8: `test_create_collection_name_too_long_returns_422`
- Override `get_current_user` → mock user.
- POST `{"name": "a" * 256}` (256 chars, exceeds max 255).
- Assert: status 422.

### Test 9: `test_create_collection_no_card_md_returns_201`
- Override `get_current_user` + `get_session` (same as Test 1; `refresh` side_effect sets `dataset_card_md = None`).
- POST `{"name": "no-card"}` (no `dataset_card_md` field).
- Assert: status 201; `body["dataset_card_md"] is None`.

### Test 10: `test_create_collection_extra_fields_ignored`
- Override `get_current_user` + `get_session`.
- POST `{"name": "test-extra", "unknown_field": "garbage", "dataset_card_md": null}`.
- Assert: status 201 (unknown field silently discarded by `extra="ignore"`).

---

## §8 Carry-overs and Hand-offs

### F-010 obligation
When F-010 implements the real `GET /api/sources/collections` body, it MUST:
1. Update `CollectionListResponse.items` from `list[Any]` to `list[SourceCollectionOut]` (the schema introduced in this sprint).
2. Run the manual OpenAPI export (or `make codegen` when wired) and commit the resulting `packages/api-types/openapi.json` diff in the **same commit** as the route change (hard invariant #6, CAL-3).

### F-011 obligation
F-011 (upload PDF source) references `source_collection.id` via the `source.collection_id` FK. F-009 does not expose a `GET /api/sources/collections/{id}` route — F-011 must accept a `collection_id` integer in its request body and validate it against the DB directly.

### Reviewer-surprise pre-emptions

1. **Why constraint-name match and not generic `"unique"` scan?** A future UNIQUE constraint on another `source_collection` column would trigger the generic scan and produce a misleading 409 "Collection name already exists". The constraint name `"source_collection_name_key"` is precise and confirmed on the live DB. See D4.

2. **Why is the response richer than `{id, name}`?** No `GET /api/sources/collections/{id}` exists in MVP. Clients need the full record immediately after creation. Zero extra cost. See D2.

3. **Why is `CollectionListResponse.items` left as `list[Any]`?** To keep blast radius small and let F-009 ship a vertical slice. F-010 owns the list body and will tighten it. See D7.

4. **Why no new `conftest.py` shared fixture?** One test file; inline mocks are sufficient. F-010 may revisit if it also needs a collections mock.

5. **Why `collections)` as a new layer rather than extending `auth)`?** The `auth)` layer tests JWT enforcement, not business logic. Collections CRUD belongs in its own layer for independent invocation (`bash checks.sh collections`).

---

## §9 Risks & Open Questions

### R1 — `session.refresh()` mock complexity
The `session.add()` + `session.commit()` + `session.refresh()` sequence requires the mock to populate ORM object attributes during `refresh` (setting `id`, `created_at`, etc.). This is done via `side_effect` on the `AsyncMock`. The test must match the handler's exact call sequence. Mitigation: keep the handler simple (add → commit → refresh → return) and document the mock assumption in a comment.

### R2 — `IntegrityError` mock shape in tests
Resolved: Test 3 uses `IntegrityError("", {}, Exception('duplicate key value violates unique constraint "source_collection_name_key"'))`. The `orig` message contains the constraint name, so the handler's `if "source_collection_name_key" in str(exc.orig)` check matches and returns 409. The mock `orig` is a plain `Exception` (not an asyncpg-specific type), which is correct for unit tests — the handler inspects the string, not the type.

### R3 — `make codegen` availability
Resolved: No Makefile exists yet. The manual export command is documented inline in §3 and §6. Same treatment as S008 (commit `91a2651`). The `contract)` layer guard `[[ -f Makefile ]] || exit 0` prevents CI failure while the Makefile is absent.

### R4 — `checks.sh collections)` DB access
The V2 psql command uses the compose service name `postgres` and env vars `POSTGRES_USER` / `POSTGRES_DB`, matching the patterns in the existing `auth)` and `migration)` layers. No new infrastructure required.

### R5 — OpenAPI operationId uniqueness
FastAPI auto-generates `operationId` from method + path + function name. The stub `GET /collections` already has its operationId. The new `POST /collections` will generate a distinct operationId automatically. No conflict expected.
