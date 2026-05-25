# S016-F-016 — Proposed Contract

**Status:** PROPOSED
**Date drafted:** 2026-05-25
**Author:** Leader (Claude)
**Sprint-id:** S016-F-016
**Depends on:** F-015 (passes: true — MinerU row seeded), F-008 (passes: true — JWT auth enforced)

---

## §1 Goal

F-016 exposes the operator registry over HTTP: a new authenticated endpoint
`GET /api/operators` returns all active operators, optionally filtered by the
`category` query parameter. The primary verification requirements are:

- `GET /api/operators?category=extractor` returns HTTP 200 with a JSON array
  containing the MinerU operator row inserted by F-015.
- Each item in the array includes at minimum: `id`, `name`, `version`,
  `category`, and `config_schema`.
- `GET /api/operators?category=tagger` returns HTTP 200 with a (possibly empty)
  JSON array whose items all have `category='tagger'`.
- The endpoint is auth-protected: a request without a valid Bearer token
  returns 401.

No migration, no new model, no pagination — the implementation is a new router
file, a new Pydantic schema, wiring in `main.py`, an extension to `checks.sh`,
and a regenerated `packages/api-types/openapi.json` committed in the same step.

---

## §2 Files Changed

| Path | New / Modified | Why |
|---|---|---|
| `apps/api/dataplat_api/routers/operators.py` | **NEW** | New router file implementing `GET /api/operators` with optional `?category=` filter. Follows the same structure as `routers/sources.py`: `APIRouter`, `Depends(get_current_user)`, `Depends(get_session)`, async select. |
| `apps/api/dataplat_api/schemas/operators.py` | **NEW** | `OperatorRead` Pydantic schema (`from_attributes=True`) covering the fields required by verification plus all columns worth exposing. |
| `apps/api/dataplat_api/main.py` | **MODIFIED** | Import `operators_router` from `routers.operators` and call `app.include_router(operators_router)`. |
| `verify/checks.sh` | **MODIFIED** | Extend the existing `operators)` layer (currently F-015 only) with four new checks: 401 on no-token, V1 (category=extractor array contains mineru), V2 (shape of each item), V3 (category=tagger returns 200+empty array). Also add `bash "$0" operators` to `all)` — **it is already there** (line 980); nothing to add. |
| `packages/api-types/openapi.json` | **MODIFIED** | Regenerated via the established manual Python export (no Makefile yet) and committed in the **same commit** as the router and schema files (hard invariant #6). |

**Files NOT touched:**

- `apps/api/dataplat_api/db/models.py` — `Operator` model unchanged; no schema change.
- Any Alembic migration file — no DB schema change.
- `docs/data_platform_design.md` — read-only per hard rule.
- `apps/api/dataplat_api/cli.py` — seed CLI unchanged.

---

## §3 Endpoint Contract

### §3.1 Route

```
GET /api/operators
```

Prefix: `/api/operators` (router `prefix="/api/operators"`, tag `"operators"`).

### §3.2 Query parameters

| Param | Type | Required | Default | Behaviour |
|---|---|---|---|---|
| `category` | `str \| None` | Optional | `None` | If provided, filters rows to `WHERE operator.category = category`. If omitted, returns all active operators across all categories. If the category value is not found in the DB, returns HTTP 200 with an empty array — **not** 404. This is consistent with how empty list queries behave throughout the codebase (e.g. `GET /api/sources/collections/{id}/sources` returns `{"items":[],"total":0}` on an empty collection, not 404). The verification only tests `extractor` and `tagger`; an unknown category value (e.g. `?category=nonexistent`) returns `[]`. |

Rationale for "optional, unknown-returns-empty": the caller is a UI or orchestrator
listing what is available; an empty list is informative and actionable ("nothing
registered in this category yet"). A 404 would be appropriate for a resource that
must exist (like a collection by id), not for a filtered query.

### §3.3 Response

**HTTP 200** — `application/json`

Response body: a plain JSON array of `OperatorRead` objects (not paginated).

Rationale for plain array vs. paginated envelope: (a) the verification criteria
explicitly show `returns an array`, not `{"items":[], "total": N}`; (b) the operator
registry is a small, bounded catalogue (dozens of rows, not millions); paginating it
adds no real value in the MVP and would change the verification-expected shape.
If the catalogue ever grows large enough to warrant pagination, that is a separate
feature.

### §3.4 OperatorRead schema

Defined in `apps/api/dataplat_api/schemas/operators.py`:

```python
class OperatorRead(BaseModel):
    id: int
    name: str
    version: str
    category: str
    input_kind: str
    output_kind: str
    image: str
    config_schema: dict | None
    description: str | None
    is_active: bool | None

    model_config = ConfigDict(from_attributes=True)
```

Fields included:
- `id`, `name`, `version`, `category` — required by F-016 V2.
- `config_schema` — required by F-016 V2; nullable (`Optional[dict]` in the ORM).
- `input_kind`, `output_kind` — directly useful to API callers composing pipelines; low cost to expose.
- `image` — operators are identified by their container image; essential for orchestration.
- `description` — human-readable label for UI display; nullable, zero risk.
- `is_active` — expose it so clients can reason about operator availability; nullable boolean.

Fields intentionally **omitted** from the response (not exposed in MVP):
- `output_schema`, `default_config`, `reference_url`, `example_input`, `example_output`,
  `entrypoint`, `estimated_cost_per_unit`, `rate_limit_per_minute`, `created_at` —
  these are either internal/operational details or not referenced in any verification
  criterion. They can be added later without a migration.

### §3.5 Status codes

| Status | When |
|---|---|
| 200 | Always on success (including empty result set). |
| 401 | No token / invalid token / expired token (enforced by `get_current_user`). |
| 422 | Pydantic validation error on query parameter (FastAPI default; no custom handling needed). |

No 404 for unknown categories (see §3.2).

### §3.6 Auth

`Depends(get_current_user)` from `dataplat_api.auth.dependencies` — identical to
every other protected route in the codebase. The endpoint is NOT public. A request
without a valid JWT Bearer token returns 401 (handled by the `get_current_user`
dependency, not by explicit handler logic).

---

## §4 "Active" Semantics — Resolution

**Finding:** The `Operator` model at `apps/api/dataplat_api/db/models.py:198–202` has:

```python
is_active: Mapped[Optional[bool]] = mapped_column(
    sa.Boolean,
    server_default=text("true"),
    nullable=True,
)
```

There is a dedicated `is_active` boolean column with `server_default=true`. The
composite index `idx_operator_category` is defined on `(category, is_active)` at
line 163, which is precisely the filter this endpoint uses — confirming the model
was designed for this query.

**Decision:** Filter on `is_active IS NOT FALSE`. This wording handles the nullable
case correctly: rows with `is_active = true` (the default) AND rows with
`is_active = NULL` are included; rows with `is_active = false` are excluded.

Reasoning for `IS NOT FALSE` vs. `= true`:
- The MinerU seed row does not set `is_active` explicitly; it relies on the
  `server_default`. SQLAlchemy `session.flush()` does not re-fetch server defaults
  unless `session.refresh()` is called. Using `IS NOT FALSE` means a row inserted
  without explicitly setting `is_active` (relying on the server default) is always
  included, which is the correct behaviour. Strict `= true` could silently exclude
  rows if the ORM-side value is `None` at the time of insert and the DB default
  hasn't been re-read.

SQLAlchemy async query:

```python
stmt = select(Operator).where(Operator.is_active.isnot(False))
if category is not None:
    stmt = stmt.where(Operator.category == category)
stmt = stmt.order_by(Operator.id.asc())
```

---

## §5 Ordering

Results are ordered by `id ASC` (database insertion order, stable across repeated
calls). This is the same ordering convention used by all other list endpoints in
this codebase (`list_collections` in `sources.py:77`, `list_sources_by_collection`
in `sources.py:188`). Deterministic ordering is required for the V1 and V2 checks
(which use `jq`/Python to locate the mineru row by iterating the array).

---

## §6 Verification Mapping

All four checks are added to the **existing** `operators)` layer in
`verify/checks.sh` (after the existing F-015 checks at lines 919–963).

### Setup: mint Bearer token (once, reused for all four checks)

Identical token-mint block to all other protected layers (`dagster)`, `runs)`,
`collections)`, etc.):

```bash
OP_TOKEN_BODY=$(mktemp)
OP_TOKEN_STATUS=$(curl -sS -X POST \
  "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
  -d "username=admin@example.com&password=testpassword123" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -w '%{http_code}' -o "$OP_TOKEN_BODY")
test "$OP_TOKEN_STATUS" = "200" \
  || { echo "FAIL: operators F016 could not mint token — run 'bash $0 auth' first"; rm -f "$OP_TOKEN_BODY"; exit 1; }
OP_TOKEN=$(python3 -c "import json; print(json.load(open('$OP_TOKEN_BODY'))['access_token'])")
rm -f "$OP_TOKEN_BODY"
```

### F016-AUTH: GET /api/operators without token → 401

```bash
echo "--- operators F016-AUTH: no token → 401 ---"
AUTH_STATUS=$(curl -sS -o /dev/null -w '%{http_code}' \
  "http://localhost:${FASTAPI_HOST_PORT}/api/operators")
test "$AUTH_STATUS" = "401" \
  || { echo "FAIL: F016-AUTH returned $AUTH_STATUS (expected 401)"; exit 1; }
echo "  F016-AUTH OK: no-token → 401"
```

### F016-V1: GET /api/operators?category=extractor returns 200 with mineru row

```bash
echo "--- operators F016-V1: category=extractor contains mineru ---"
V1_BODY=$(mktemp)
V1_STATUS=$(curl -sS -X GET \
  "http://localhost:${FASTAPI_HOST_PORT}/api/operators?category=extractor" \
  -H "Authorization: Bearer $OP_TOKEN" \
  -w '%{http_code}' -o "$V1_BODY")
test "$V1_STATUS" = "200" \
  || { echo "FAIL: F016-V1 returned $V1_STATUS: $(cat "$V1_BODY")"; rm -f "$V1_BODY"; exit 1; }
python3 -c "
import json, sys
body = json.load(open('$V1_BODY'))
assert isinstance(body, list), f'expected list, got {type(body).__name__}: {body}'
names = [op.get('name') for op in body]
assert 'mineru' in names, f'mineru not in operator names: {names}'
print('  F016-V1 OK: array len =', len(body), 'names =', names)
" || { echo "FAIL: F016-V1 assertion failed"; rm -f "$V1_BODY"; exit 1; }
rm -f "$V1_BODY"
```

### F016-V2: each item includes id, name, version, category, config_schema

```bash
echo "--- operators F016-V2: item shape check ---"
V2_BODY=$(mktemp)
V2_STATUS=$(curl -sS -X GET \
  "http://localhost:${FASTAPI_HOST_PORT}/api/operators?category=extractor" \
  -H "Authorization: Bearer $OP_TOKEN" \
  -w '%{http_code}' -o "$V2_BODY")
test "$V2_STATUS" = "200" \
  || { echo "FAIL: F016-V2 returned $V2_STATUS: $(cat "$V2_BODY")"; rm -f "$V2_BODY"; exit 1; }
python3 -c "
import json, sys
body = json.load(open('$V2_BODY'))
assert isinstance(body, list) and len(body) > 0, f'expected non-empty list: {body}'
required = ['id', 'name', 'version', 'category', 'config_schema']
for op in body:
    for field in required:
        assert field in op, f'item missing field {field!r}: {op}'
    assert isinstance(op['id'], int), f'id not int: {op}'
    assert op['category'] == 'extractor', f'category mismatch: {op}'
mineru = next((op for op in body if op['name'] == 'mineru'), None)
assert mineru is not None, f'mineru not found in {body}'
assert mineru['version'] == '0.1.0', f'version wrong: {mineru}'
assert isinstance(mineru['config_schema'], dict), f'config_schema not dict: {mineru}'
print('  F016-V2 OK: all items have required fields; mineru v0.1.0 config_schema is dict')
" || { echo "FAIL: F016-V2 shape assertion failed"; rm -f "$V2_BODY"; exit 1; }
rm -f "$V2_BODY"
```

### F016-V3: GET /api/operators?category=tagger returns 200 + empty array (vacuously valid)

Note: No tagger operator has been seeded. The check asserts HTTP 200 and that the
response is a JSON array whose every item (vacuously, since there are none) has
`category='tagger'`. This is the honest, correct behaviour — the endpoint does not
fabricate data.

```bash
echo "--- operators F016-V3: category=tagger returns 200 + array (empty ok) ---"
V3_BODY=$(mktemp)
V3_STATUS=$(curl -sS -X GET \
  "http://localhost:${FASTAPI_HOST_PORT}/api/operators?category=tagger" \
  -H "Authorization: Bearer $OP_TOKEN" \
  -w '%{http_code}' -o "$V3_BODY")
test "$V3_STATUS" = "200" \
  || { echo "FAIL: F016-V3 returned $V3_STATUS: $(cat "$V3_BODY")"; rm -f "$V3_BODY"; exit 1; }
python3 -c "
import json, sys
body = json.load(open('$V3_BODY'))
assert isinstance(body, list), f'expected list, got {type(body).__name__}: {body}'
for op in body:
    assert op.get('category') == 'tagger', f'item category != tagger: {op}'
print('  F016-V3 OK: category=tagger -> 200 + list of len', len(body),
      '(empty is expected — no tagger seeded yet)')
" || { echo "FAIL: F016-V3 assertion failed"; rm -f "$V3_BODY"; exit 1; }
rm -f "$V3_BODY"
```

### Placement in checks.sh

Insert the token-mint block + the four checks above at the end of the `operators)`
case block, immediately before the final `;;` on line 963. The `all)` chain already
calls `bash "$0" operators` at line 980 — no change needed there.

---

## §7 Invariant Compliance

| # | Invariant | Status |
|---|---|---|
| 1 | Lineage mandatory | N/A — no Commit or processor involved. |
| 2 | Storage separation + CAS | N/A — no blob storage involved. |
| 3 | Schema frozen post-publish | N/A — no Silver/Gold repo schema involved. |
| 4 | LLM calls through gateway | N/A — no LLM call. |
| 5 | Async SQLAlchemy only | **REQUIRED.** The router handler MUST use `async def`, `await session.execute(select(...))`, `result.scalars().all()`. `session.query()` and any sync session are forbidden. No exceptions. |
| 6 | OpenAPI ↔ TS type sync | **REQUIRED.** Adding a new route + new response schema changes the OpenAPI output. The implementer MUST regenerate `packages/api-types/openapi.json` in the same commit using the established manual export command (no Makefile yet): `cd apps/api && uv run python -c 'import json; from dataplat_api.main import app; print(json.dumps(app.openapi(), indent=2))' > ../../packages/api-types/openapi.json`. The `contract)` layer's `[[ -f Makefile ]] \|\| exit 0` guard will skip the TS generation step until the Makefile is wired, but the JSON file MUST still be committed. |

**Scope discipline (MVP boundaries):**
- No granular ACL invented. The endpoint returns operators visible to any authenticated user, consistent with the MVP `private|internal` visibility model.
- No new operator categories are invented (tagger, converter, etc.) — V3 simply tests that the filter works on a category that currently has no rows.
- No Celery/Dagster integration — the list endpoint is pure DB read.

---

## §8 Open Questions

1. **`is_active IS NOT FALSE` vs. strict `= true`:** Resolved as above (§4). The
   `is_active` column has a server default of `true` and is nullable; using
   `IS NOT FALSE` is the correct SQL expression. The reviewer should confirm this
   is acceptable.

2. **No-category behaviour (return all active operators):** Chosen as "return all
   active operators across categories" when `?category=` is absent. No verification
   criterion tests this case. The reviewer should confirm this is acceptable vs.
   requiring category to always be supplied (which would need a 422 on missing
   category — inconsistent with the array-of-all approach).

3. **V3 empty-array acknowledgement:** `category=tagger` will return `[]` because
   no tagger row is seeded. The check asserts 200 + array (vacuously all items have
   `category=tagger`). This is deliberately noted here so the reviewer confirms the
   check is meaningful and not a false pass. No tagger seed should be added as part
   of this sprint (that is scope creep).

---

## §9 Implementation Sequence

The implementer should work in this order to keep each step independently verifiable:

1. Create `apps/api/dataplat_api/schemas/operators.py` (`OperatorRead`).
2. Create `apps/api/dataplat_api/routers/operators.py` (the router + handler).
3. Modify `apps/api/dataplat_api/main.py` (wire `include_router`).
4. Run `cd apps/api && uv run ruff check dataplat_api/routers/operators.py dataplat_api/schemas/operators.py dataplat_api/main.py` and fix any issues.
5. Run `cd apps/api && uv run mypy dataplat_api/routers/operators.py dataplat_api/schemas/operators.py` and fix any issues.
6. Regenerate `packages/api-types/openapi.json`:
   ```bash
   cd apps/api && uv run python -c \
     'import json; from dataplat_api.main import app; print(json.dumps(app.openapi(), indent=2))' \
     > ../../packages/api-types/openapi.json
   ```
7. Extend `verify/checks.sh` `operators)` layer with the four F016 checks.
8. Commit all changed files (`routers/operators.py`, `schemas/operators.py`, `main.py`, `checks.sh`, `packages/api-types/openapi.json`) in a **single commit**.
9. Run `bash verify/checks.sh operators` against the live stack to confirm all checks pass.
10. Run `bash verify/checks.sh backend` to confirm lint/type/unit pass.

---

## §10 Files Summary

Total: **5 files** (2 new, 3 modified).

```
apps/api/dataplat_api/schemas/operators.py    NEW
apps/api/dataplat_api/routers/operators.py    NEW
apps/api/dataplat_api/main.py                 MODIFIED (1 import + 1 include_router line)
verify/checks.sh                              MODIFIED (extend operators) layer)
packages/api-types/openapi.json               MODIFIED (regenerated, same commit)
```
