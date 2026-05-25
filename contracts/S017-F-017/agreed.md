# S017-F-017 — Proposed Contract

**Status:** PROPOSED (reviewer iteration 1 applied — awaiting APPROVED)
**Date drafted:** 2026-05-25
**Author:** Leader (Claude)
**Sprint-id:** S017-F-017
**Depends on:** F-016 (passes: true — GET /api/operators list endpoint exists)

---

## §1 Goal

F-017 adds `GET /api/operators/{operator_id}` — the detail sibling of the F-016
list endpoint. It returns the **full** operator record for a given id, including
the three columns the verification specifically calls out (`config_schema`,
`output_schema`, `default_config`) and all remaining columns omitted from the
lean F-016 `OperatorRead` list schema. The endpoint returns 404 for an unknown
id and 401 without a valid JWT Bearer token. No migration, no new model, no
pagination — the work is a new Pydantic schema (`OperatorDetail`), one new
handler in the existing operators router, and a regenerated `openapi.json`
committed in the same step.

---

## §2 Files Changed

| Path | New / Modified | What changes and why |
|---|---|---|
| `apps/api/dataplat_api/schemas/operators.py` | **MODIFIED** | Add `OperatorDetail` class — a new schema exposing all 19 ORM columns (see §3.4 for exact field list). `OperatorRead` is unchanged; the list endpoint keeps its lean projection. |
| `apps/api/dataplat_api/routers/operators.py` | **MODIFIED** | Add `GET /{operator_id}` handler using `OperatorDetail`. Import `OperatorDetail` alongside existing `OperatorRead`. Update module docstring to document the new route. |
| `verify/checks.sh` | **MODIFIED** | Extend the existing `operators)` layer (after the F016-V3 block at line 1050, before the `;;`) with: F017-V1 (200 + full field shape check including config_schema/output_schema/default_config) and F017-V2 (404 for id 99999). The mineru id is derived dynamically from the F016 list call rather than hardcoded. |
| `packages/api-types/openapi.json` | **MODIFIED** | Regenerated (new path `/api/operators/{operator_id}` + new component schema `OperatorDetail`). Must be committed in the **same commit** as the router and schema changes (hard invariant #6). |

**Files NOT touched:**

- `apps/api/dataplat_api/main.py` — the operators router is already wired via `include_router(operators_router)` from F-016. No change needed.
- `apps/api/dataplat_api/db/models.py` — `Operator` model unchanged; no schema change.
- Any Alembic migration file — no DB schema change.
- `docs/data_platform_design.md` — read-only per hard rule.
- `apps/api/dataplat_api/cli.py` — seed CLI unchanged.

---

## §3 Endpoint Contract

### §3.1 Route

```
GET /api/operators/{operator_id}
```

Router prefix is `/api/operators` (established in F-016). The new handler is
registered at path `/{operator_id}`, making the full URL `/api/operators/{operator_id}`.

### §3.2 Shadowing analysis

The F-016 list route is registered at path `""` (empty string — FastAPI resolves
this as `/api/operators`). The new detail route is `/{operator_id}`. There is no
shadowing concern: a request to `/api/operators` matches the empty-path route, and
a request to `/api/operators/42` matches the `/{operator_id}` route. There is no
fixed-prefix segment like `/collections` that could conflict — the only routes
under the `operators` router are `""` (list) and `/{operator_id}` (detail), so
registration order does not matter here. This differs from the sources router where
`/collections` and `/{id}` had an ordering requirement.

### §3.3 Path parameter

| Param | Type | Validation | Behaviour |
|---|---|---|---|
| `operator_id` | `int` | FastAPI coerces the path segment to `int`. A non-integer segment (e.g. `/api/operators/abc`) returns **422** (FastAPI default validation error). | Used in `WHERE operator.id = operator_id`. |

### §3.4 Response schema — `OperatorDetail`

A new `OperatorDetail` Pydantic class in `schemas/operators.py` exposing **all
19 ORM columns**. `OperatorRead` (F-016 list schema) is left unchanged.

**Decision: new `OperatorDetail` schema, not extending `OperatorRead`.**

Rationale: `OperatorRead` was deliberately lean for the list endpoint — exposing
all 19 columns in a list response would transmit large JSONB blobs
(`example_input`, `example_output`, `estimated_cost_per_unit`) for every row in
every list call. The detail endpoint is a single-row fetch where the full payload
is expected and useful. A separate schema keeps the API contract explicit: callers
know exactly which fields are available at each endpoint without relying on
optional-field inference. This also avoids retroactively changing F-016's
`openapi.json` component for `OperatorRead`, which would be a needless diff.

**Full field list for `OperatorDetail`** (matching ORM `Mapped[...]` types exactly):

| Field | Python type | Nullability | ORM source |
|---|---|---|---|
| `id` | `int` | NOT NULL | `Mapped[int]`, `sa.Identity()`, primary key |
| `name` | `str` | NOT NULL | `Mapped[str]`, `nullable=False` |
| `version` | `str` | NOT NULL | `Mapped[str]`, `nullable=False` |
| `category` | `str` | NOT NULL | `Mapped[str]`, `nullable=False` |
| `input_kind` | `str` | NOT NULL | `Mapped[str]`, `nullable=False` |
| `output_kind` | `str` | NOT NULL | `Mapped[str]`, `nullable=False` |
| `image` | `str` | NOT NULL | `Mapped[str]`, `nullable=False` |
| `output_schema` | `dict \| None` | nullable | `Mapped[Optional[dict]]`, JSONB |
| `config_schema` | `dict \| None` | nullable | `Mapped[Optional[dict]]`, JSONB |
| `default_config` | `dict \| None` | nullable | `Mapped[Optional[dict]]`, JSONB, server_default `'{}'::jsonb` |
| `description` | `str \| None` | nullable | `Mapped[Optional[str]]` |
| `reference_url` | `str \| None` | nullable | `Mapped[Optional[str]]` |
| `example_input` | `dict \| None` | nullable | `Mapped[Optional[dict]]`, JSONB |
| `example_output` | `dict \| None` | nullable | `Mapped[Optional[dict]]`, JSONB |
| `entrypoint` | `str \| None` | nullable | `Mapped[Optional[str]]` |
| `estimated_cost_per_unit` | `dict \| None` | nullable | `Mapped[Optional[dict]]`, JSONB |
| `rate_limit_per_minute` | `int \| None` | nullable | `Mapped[Optional[int]]`, `sa.Integer` |
| `is_active` | `bool \| None` | nullable | `Mapped[Optional[bool]]`, server_default `true` |
| `created_at` | `datetime \| None` | nullable | `Mapped[Optional[sa.DateTime]]`, `timezone=True`, server_default `now()` |

`model_config = ConfigDict(from_attributes=True)` — same as `OperatorRead`.

Python declaration (for the implementer):

```python
from datetime import datetime

class OperatorDetail(BaseModel):
    id: int
    name: str
    version: str
    category: str
    input_kind: str
    output_kind: str
    image: str
    output_schema: dict | None
    config_schema: dict | None
    default_config: dict | None
    description: str | None
    reference_url: str | None
    example_input: dict | None
    example_output: dict | None
    entrypoint: str | None
    estimated_cost_per_unit: dict | None
    rate_limit_per_minute: int | None
    is_active: bool | None
    created_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
```

### §3.5 Status codes

| Status | When |
|---|---|
| 200 | Operator found; full `OperatorDetail` body returned. |
| 401 | No token / invalid token / expired token — enforced by `get_current_user`. |
| 404 | No operator row with `id = operator_id`. Detail string: `"Operator not found"`. |
| 422 | Path param is not a valid integer (FastAPI default; no custom handling). |

### §3.6 Auth

`Depends(get_current_user)` — identical to the list handler. The endpoint is not
public. 401 is returned for missing/invalid/expired tokens without revealing
whether an operator with the given id exists (no leakage risk since operators are
a global registry, but consistency with the rest of the codebase is good practice).

---

## §4 "404 Semantics" — No Owner Scoping

Operators are a **global registry** — they are not owned by any user, unlike
`SourceCollection` (which has an `owner_id` column). There is no `owner_id` or
similar field on the `Operator` model. Therefore:

- The query is a plain `SELECT * FROM operator WHERE id = :id` with no additional
  filter.
- If the row does not exist → 404, detail `"Operator not found"`.
- There is no "exists but belongs to another user → 404 to prevent enumeration"
  case, because operator visibility is unconditional for any authenticated user.

This mirrors `GET /api/admin/dagster-status` (global resource, no owner scoping),
not `GET /api/sources/{id}` (owner-scoped with anti-enumeration). The 404 response
is unambiguous: the id simply does not exist.

SQLAlchemy async query (agreed handler body):

```python
result = await session.execute(
    select(Operator).where(Operator.id == operator_id)
)
operator = result.scalar_one_or_none()
if operator is None:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Operator not found",
    )
return OperatorDetail.model_validate(operator)
```

---

## §5 Verification Mapping

All checks are appended to the **existing** `operators)` layer in `verify/checks.sh`,
immediately after the F016-V3 block (after line 1050, before the `;;` at line 1051).

The F016 token (`$OP_TOKEN`) minted earlier in the same `operators)` block is
**reused** — no second token-mint is needed. The F017 checks run after F016 checks
in the same shell session, so `$OP_TOKEN` and `$FASTAPI_HOST_PORT` are already set.

### Deriving the mineru id dynamically

Rather than hardcoding `id=1`, the mineru id is derived from the F016 list response.
This is robust against re-seeding, DB truncation between test runs, and any future
id gap. The derivation reuses the existing `GET /api/operators?category=extractor`
call:

```bash
MINERU_ID_BODY=$(mktemp)
curl -sS -X GET \
  "http://localhost:${FASTAPI_HOST_PORT}/api/operators?category=extractor" \
  -H "Authorization: Bearer $OP_TOKEN" \
  -o "$MINERU_ID_BODY"
MINERU_ID=$(python3 -c "
import json, sys
body = json.load(open('$MINERU_ID_BODY'))
mineru = next((op for op in body if op['name'] == 'mineru'), None)
if mineru is None:
    sys.exit(1)
print(mineru['id'], end='')
") || { echo "FAIL: F017 could not derive mineru id from extractor list"; rm -f "$MINERU_ID_BODY"; exit 1; }
rm -f "$MINERU_ID_BODY"
```

The `|| { ...; exit 1; }` on the assignment line catches python's `sys.exit(1)` with
a clear human-readable message. Stderr is NOT merged into `$MINERU_ID` (no `2>&1`),
so any Python traceback appears directly on the terminal and the variable is never
populated with error text. If this step fails (mineru absent), the check fails
immediately before V1 runs.

### F017-V1: GET /api/operators/{mineru_id} returns 200 with full field set

```bash
echo "--- operators F017-V1: GET /api/operators/${MINERU_ID} returns 200 + full fields ---"
V1_DETAIL_BODY=$(mktemp)
V1_DETAIL_STATUS=$(curl -sS -X GET \
  "http://localhost:${FASTAPI_HOST_PORT}/api/operators/${MINERU_ID}" \
  -H "Authorization: Bearer $OP_TOKEN" \
  -w '%{http_code}' -o "$V1_DETAIL_BODY")
test "$V1_DETAIL_STATUS" = "200" \
  || { echo "FAIL: F017-V1 returned $V1_DETAIL_STATUS: $(cat "$V1_DETAIL_BODY")"; rm -f "$V1_DETAIL_BODY"; exit 1; }
python3 -c "
import json, sys
body = json.load(open('$V1_DETAIL_BODY'))
# V2 required fields (spec): id, name, version, category, config_schema.
# F-017 additionally requires: output_schema, default_config.
# We assert all three spec-called-out JSONB fields are present as keys.
required_base = ['id', 'name', 'version', 'category', 'input_kind', 'output_kind', 'image', 'is_active']
required_jsonb = ['config_schema', 'output_schema', 'default_config']
for field in required_base + required_jsonb:
    assert field in body, f'missing field {field!r}: {body}'
assert body['id'] == ${MINERU_ID}, f'id mismatch: {body}'
assert body['name'] == 'mineru', f'name mismatch: {body}'
assert body['version'] == '0.1.0', f'version wrong: {body}'
assert body['category'] == 'extractor', f'category wrong: {body}'
# config_schema must be a valid JSON Schema object (dict with type=object per seed).
assert isinstance(body['config_schema'], dict), f'config_schema not dict: {body[\"config_schema\"]}'
assert body['config_schema'].get('type') == 'object', f'config_schema type != object: {body[\"config_schema\"]}'
# output_schema: present as a key but NULL for the mineru seed (seed never sets it).
# Assert key presence only — do NOT assert isinstance(dict).
assert 'output_schema' in body, f'output_schema key missing: {body}'
# default_config: the detail handler does a fresh SELECT which reads the actual stored
# DB value. The server_default '{}'::jsonb fired at INSERT time, so the DB holds {}.
# A SELECT always returns the stored value — the insert-buffer None concern (F-016)
# does not apply here. Assert strictly: must be a dict (empty is fine).
assert isinstance(body['default_config'], dict), \
  f'default_config not a dict: {body[\"default_config\"]}'
print('  F017-V1 OK: id=%d name=%s config_schema.type=%s output_schema=%s default_config=%s' % (
  body['id'], body['name'], body['config_schema']['type'],
  type(body['output_schema']).__name__, body['default_config']))
" || { echo "FAIL: F017-V1 assertion failed"; rm -f "$V1_DETAIL_BODY"; exit 1; }
rm -f "$V1_DETAIL_BODY"
```

### F017-V2: GET /api/operators/99999 returns 404

```bash
echo "--- operators F017-V2: GET /api/operators/99999 returns 404 ---"
V2_NOTFOUND_STATUS=$(curl -sS -X GET \
  "http://localhost:${FASTAPI_HOST_PORT}/api/operators/99999" \
  -H "Authorization: Bearer $OP_TOKEN" \
  -o /dev/null -w '%{http_code}')
test "$V2_NOTFOUND_STATUS" = "404" \
  || { echo "FAIL: F017-V2 returned $V2_NOTFOUND_STATUS (expected 404)"; exit 1; }
echo "  F017-V2 OK: /api/operators/99999 → 404"
```

### Placement in checks.sh

Insert the id-derivation block and the two checks (F017-V1, F017-V2) at lines
1051–1052 (currently `    ;;` and `  all)`), pushing `;;` down. `$OP_TOKEN` and
`$FASTAPI_HOST_PORT` are already in scope from the F016 block. No new token-mint
needed.

---

## §6 Invariant Compliance

| # | Invariant | Status |
|---|---|---|
| 1 | Lineage mandatory | N/A — no Commit or processor involved. |
| 2 | Storage separation + CAS | N/A — no blob storage involved. |
| 3 | Schema frozen post-publish | N/A — no Silver/Gold repo schema. |
| 4 | LLM calls through gateway | N/A — no LLM call. |
| 5 | Async SQLAlchemy only | **REQUIRED.** Handler must use `async def`, `await session.execute(select(Operator).where(...))`, `result.scalar_one_or_none()`. No `session.query()`, no sync session. |
| 6 | OpenAPI ↔ TS type sync | **REQUIRED.** New route + new `OperatorDetail` schema changes OpenAPI output. The implementer MUST regenerate `packages/api-types/openapi.json` in the same commit via: `cd apps/api && DATABASE_URL="..." SECRET_KEY="..." uv run python -c 'import json; from dataplat_api.main import app; print(json.dumps(app.openapi(), indent=2))' > ../../packages/api-types/openapi.json`. The `contract)` layer's `[[ -f Makefile ]] \|\| exit 0` guard will skip TS generation until the Makefile is wired, but the JSON file MUST be committed. The diff must contain `/api/operators/{operator_id}` path and `OperatorDetail` component schema. |

**Scope discipline:**
- No new operator columns invented. The 19 columns are exactly what the ORM model has.
- No owner-scoping logic introduced (operators are global).
- No pagination. Detail endpoint is a single-row fetch.
- No Celery/Dagster integration.

---

## §7 Implementation Sequence

1. Modify `apps/api/dataplat_api/schemas/operators.py`: add `OperatorDetail` class (add `from datetime import datetime` import).
2. Modify `apps/api/dataplat_api/routers/operators.py`: import `OperatorDetail`, add `GET /{operator_id}` handler, update module docstring.
3. Run `cd apps/api && uv run ruff check dataplat_api/schemas/operators.py dataplat_api/routers/operators.py` — fix any issues.
4. Run `cd apps/api && uv run mypy dataplat_api/schemas/operators.py dataplat_api/routers/operators.py` — fix any issues.
5. Regenerate `packages/api-types/openapi.json` (with `DATABASE_URL` + `SECRET_KEY` env vars as established in F-016). Verify the diff contains `/api/operators/{operator_id}` and `OperatorDetail`.
6. Extend `verify/checks.sh` `operators)` layer with MINERU_ID derivation + F017-V1 + F017-V2 (before the `;;` after the F016-V3 block).
7. Commit all four changed files in a **single commit**: `schemas/operators.py`, `routers/operators.py`, `checks.sh`, `packages/api-types/openapi.json` (+ `claude-progress.txt`).
8. Run `bash verify/checks.sh operators` — must exit 0.
9. Run `bash verify/checks.sh backend` — must exit 0.

---

## §8 Open Questions

1. **`output_schema` for the mineru seed row:** The F-015 seed CLI (`cli.py:81–124`) does not set `output_schema` — it is left `NULL` in the DB. The V1 check therefore cannot assert `isinstance(body['output_schema'], dict)`; it can only assert the key is present (value may be `None`). The check above is written to assert presence-as-key, not type, for `output_schema`. The reviewer should confirm this is the correct V1 assertion rather than requiring `output_schema` to be non-null (which would require seeding it first — out of scope).

2. **`default_config` — DECIDED (reviewer Mode A).** The detail handler does a fresh `SELECT` (not an insert buffer read), so the stored DB value `{}` (from `server_default '{}'::jsonb`) is always returned. The insert-buffer-None concern from F-016 does not apply to a SELECT. The check now asserts `isinstance(body['default_config'], dict)` strictly — `None` is not accepted. No `session.refresh()` required.

3. **`created_at` format in JSON response:** Pydantic v2 serialises `datetime` with timezone as an ISO 8601 string (e.g. `"2026-05-25T09:00:00+00:00"`). The V1 check does not assert on `created_at` format since the verification criterion does not require it. This is noted as an open question in case the reviewer wants a format assertion added.

---

## §9 Files Summary

Total: **4 files** modified (0 new files).

```
apps/api/dataplat_api/schemas/operators.py    MODIFIED (add OperatorDetail)
apps/api/dataplat_api/routers/operators.py    MODIFIED (add detail handler)
verify/checks.sh                              MODIFIED (F017-V1/V2 in operators) layer)
packages/api-types/openapi.json               MODIFIED (regenerated, same commit)
```
