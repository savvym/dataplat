# Sprint S049-F-049 — Proposed Contract

**Feature**: F-049 — List runs: `GET /api/runs` returns paginated run records, filterable by status; runs are ordered by `started_at` descending  
**Depends on**: F-048 (`passes: true`)  
**Sprint directory**: `contracts/S049-F-049/`  
**Author**: leader (inline)  
**Date**: 2026-06-05  
**Revision**: 2

---

## §1 Goal

Add a `GET /api/runs` list handler to the existing `runs_router` in `apps/api/dataplat_api/routers/runs.py`. The handler returns all `Run` rows owned by the authenticated caller (scoped by `Run.triggered_by == current_user.id`), ordered `started_at DESC NULLS LAST, id DESC`, and optionally filtered by `?status=<value>`. The response uses the standard `{items, total}` envelope established by `GET /api/datasets` (F-045) and `GET /api/recipes` (F-038).

Two new Pydantic schemas — `RunListItem` and `RunListResponse` — are added to `apps/api/dataplat_api/schemas/runs.py`. These extend the public OpenAPI surface, so `make codegen` must be run and the resulting `packages/api-types/` diff committed in the same commit per hard invariant #6.

**Route registration constraint (critical):** The new `GET ""` handler MUST be declared in `runs_router` **before** the existing `GET /{id}` and `GET /dagster/{dagster_run_id}` handlers. This follows the conventional `POST ""` + `GET ""` ordering established in the datasets and recipes routers (F-045 precedent) and aids readability. The correct declaration order after this sprint is:

```
POST ""                         — trigger extract run (F-018, existing)
GET  ""                         — list runs (F-049, NEW — MUST come first among GETs)
GET  /{id}                      — run detail by Postgres id (F-048, existing)
GET  /dagster/{dagster_run_id}  — Dagster-proxy status (F-005, existing)
```

---

## §2 Out of Scope

The following are explicitly **not** implemented in this sprint:

- Pagination `limit`/`offset` query parameters — deferred (see §6).
- Multiple `?status=` values in a single request — MVP accepts a single value (see §10 OQ-1).
- Admin "list all runs" bypassing owner-scope — MVP uses `triggered_by == current_user.id` only (§11.6).
- `GET /api/runs/{id}/logs` proxy — deferred (noted in `runs.py` module docstring).
- WebSocket run-status events — F-051.
- Sorting by any field other than `started_at DESC NULLS LAST, id DESC`.
- Filtering by `kind`, `dataset_id`, `recipe_id`, `source_collection_id`, or date range.
- Cross-user visibility or shared/internal runs.

---

## §3 Files Changed

| File | Status | Reason |
|---|---|---|
| `apps/api/dataplat_api/schemas/runs.py` | **edit** | Add `RunListItem` (10 fields) and `RunListResponse` envelope after `RunDetailResponse`. Keep all existing schemas unchanged. Update module-level docstring to document the new `GET /api/runs` → `RunListResponse` surface. |
| `apps/api/dataplat_api/routers/runs.py` | **edit** | Add `GET ""` route `list_runs()` **before** the existing `GET /{id}` handler. Add `func` import from `sqlalchemy`. Add `RunListItem`, `RunListResponse` to the existing `from dataplat_api.schemas.runs import …` line. `select`, `AsyncSession`, `get_session`, `get_current_user` are already imported — do NOT duplicate. Update module-level docstring to document the new route. |
| `apps/api/tests/test_runs_list.py` | **create** | New test module — 12 unit tests for the list endpoint (see §8). |
| `packages/api-types/openapi.json` | **generated** | Updated by `make codegen` after schema additions; committed in the same commit per invariant #6. |

No Alembic migration is needed — all columns referenced (`id`, `dagster_run_id`, `kind`, `status`, `started_at`, `ended_at`, `triggered_by`, `dataset_id`, `recipe_id`, `source_collection_id`) already exist on the `run` table (created in F-002 baseline migration, confirmed from `apps/api/dataplat_api/db/models.py` lines 285–327).

**Codegen hard requirement (invariant #6):** Implementer MUST run `make codegen` (or manually regenerate `packages/api-types/openapi.json` using the snippet below) and commit the resulting diff in the **same** commit as all Python source changes:

```bash
cd apps/api && uv run python -c "
import json
from dataplat_api.main import app
from fastapi.openapi.utils import get_openapi
spec = get_openapi(title=app.title, version=app.version, routes=app.routes)
with open('../../packages/api-types/openapi.json', 'w') as f:
    json.dump(spec, f, indent=2)
"
```

---

## §4 Endpoint Contract

### Method and path

```
GET /api/runs
```

### Auth

Bearer token required (`Depends(get_current_user)` — same as all other protected endpoints in `runs_router`). Missing or invalid token → **401** with `WWW-Authenticate: Bearer`.

### Query parameters

| Parameter | Type | Required | Default | Validation |
|---|---|---|---|---|
| `status` | `Optional[Literal["pending", "running", "success", "failure"]]` | No | `None` (no filter) | FastAPI validates at the route layer using a typed `Optional[Literal[...]]` annotation; invalid values produce **422** before any SQL is executed. |

**Rationale for `Literal` over a separate `RunStatus` Python enum**: the `Run.status` column is stored as `sa.Text` (not a Postgres enum type). There is no existing `RunStatus` Python enum in the codebase. Defining the query param as `Optional[Literal["pending", "running", "success", "failure"]]` mirrors the set of values set by the trigger handler and updated by Dagster sensor callbacks, keeps the schema lean, and avoids introducing a new enum class for a single query param. If a `RunStatus` enum is ever added (e.g. for F-050+), the type annotation can be updated to reference it — no structural change needed.

### Owner-scope

`Run.triggered_by == current_user.id` — the same field used by `GET /api/runs/{id}` (F-048). This filter is applied to **both** the page query (`.scalars().all()`) and the COUNT query (`.scalar_one()`). See §5 for the two-query pattern.

### Response schema

**HTTP 200** — always (including empty list):

```json
{
  "items": [
    {
      "id": 7,
      "dagster_run_id": "backfill-run-abc123",
      "kind": "extract",
      "status": "pending",
      "started_at": null,
      "ended_at": null,
      "triggered_by": 9,
      "dataset_id": null,
      "recipe_id": null,
      "source_collection_id": null
    }
  ],
  "total": 1
}
```

### Field types (`RunListItem`, `model_config = ConfigDict(from_attributes=True)`)

| Field | Python type | Nullable | Rationale |
|---|---|---|---|
| `id` | `int` | No | BigInteger PK — required for any item-level navigation |
| `dagster_run_id` | `str` | No | TEXT UNIQUE NOT NULL — needed to call `GET /dagster/{dagster_run_id}` for polling |
| `kind` | `str` | No | TEXT NOT NULL — e.g. `"extract"`, `"chunk"`, `"attr_quality"`, `"attr_lang"`, `"attr_minhash"` |
| `status` | `str` | No | TEXT NOT NULL — e.g. `"pending"`, `"running"`, `"success"`, `"failure"` |
| `started_at` | `datetime \| None` | Yes | Null for `pending` rows (set by Dagster sensor on run start) |
| `ended_at` | `datetime \| None` | Yes | Null until run completes (set by Dagster sensor) |
| `triggered_by` | `int \| None` | Yes | FK → `users.id`; the owner; nullable per ORM schema but always populated for application-created rows |
| `dataset_id` | `int \| None` | Yes | FK → `dataset.id`; null for extract/attr runs not yet linked to a dataset |
| `recipe_id` | `int \| None` | Yes | FK → `recipe.id`; null for extract/attr runs |
| `source_collection_id` | `int \| None` | Yes | FK → `source_collection.id`; null unless run was triggered over a collection |

**Excluded from `RunListItem` (present on `RunDetailResponse`, absent here):**

| Field | Why excluded |
|---|---|
| `asset_keys` | Postgres `ARRAY(Text)` — bulky, rarely needed in list view; available on detail |
| `partition_keys` | Postgres `ARRAY(Text)` — bulky; detail-level field |
| `config` | JSONB — can be large; detail-level field; currently always `None` |
| `trigger_context` | JSONB — internal/opaque; detail-level field; currently always `None` |

**Justification (list-vs-detail divide, per F-045/F-046 precedent):** `DatasetListItem` exposes 7 of 13 ORM columns; the remaining 6 (including the JSONB `recipe_snapshot`, `stats`, `dataset_card_md`) are reserved for the detail endpoint. The same principle applies here: the 4 excluded fields are either JSONB blobs or large arrays that serve no useful purpose in a list view. The 10-field `RunListItem` is sufficient to render a run list (id, kind, status, timestamps, owner, and FK links). Clients needing full detail call `GET /api/runs/{id}`.

### `RunListResponse` envelope

| Field | Python type | Notes |
|---|---|---|
| `items` | `list[RunListItem]` | Ordered `started_at DESC NULLS LAST, id DESC` |
| `total` | `int` | Owner-scoped + status-filtered COUNT; equals `len(items)` for unpaginated MVP; included for forward-compatibility |

### Ordering

```sql
ORDER BY run.started_at DESC NULLS LAST, run.id DESC
```

- `started_at` is `NULL` for `status='pending'` rows (not yet started). `NULLS LAST` pushes queued/pending runs to the bottom so that active and completed runs surface first. This mirrors the `materialized_at DESC NULLS LAST` ordering established in F-045 for the datasets list.
- `id DESC` tiebreaker: deterministic ordering when two rows share the same `started_at` (e.g. two runs triggered in the same second).
- SQLAlchemy implementation: `Run.started_at.desc().nulls_last(), Run.id.desc()` — `.nulls_last()` generates `ORDER BY run.started_at DESC NULLS LAST` on Postgres (confirmed on SQLAlchemy 2.0.41).

### Status filter

When `?status=<value>` is provided:

- Applied to the `WHERE` clause of **both** the page query and the COUNT query.
- SQLAlchemy: `.where(Run.status == status)` appended after the owner-scope filter.
- When `status=None` (absent), neither query is modified — all statuses are returned.

### Status codes

| Code | Condition |
|---|---|
| 200 | Success (including empty list) |
| 401 | Missing or invalid Bearer token |
| 422 | `?status=` value not in `Literal["pending", "running", "success", "failure"]` |

No 404 is returned — empty list is the correct response when the user has no runs (or no runs matching the filter).

---

## §5 Two-Query Implementation Pattern

The handler issues exactly **two** `await session.execute()` calls, mirroring `list_datasets()` (F-045) and `list_recipes()` (F-038):

```python
# Query 1: fetch page rows (owner-scoped, optionally status-filtered, ordered)
stmt_page = (
    select(Run)
    .where(Run.triggered_by == current_user.id)
    .order_by(Run.started_at.desc().nulls_last(), Run.id.desc())
)
if status is not None:
    stmt_page = stmt_page.where(Run.status == status)
result = await session.execute(stmt_page)
rows = result.scalars().all()

# Query 2: count (same owner-scope + status filter, no ORDER BY)
stmt_count = (
    select(func.count()).select_from(Run)
    .where(Run.triggered_by == current_user.id)
)
if status is not None:
    stmt_count = stmt_count.where(Run.status == status)
total = (await session.execute(stmt_count)).scalar_one()
```

**Why two queries instead of `len(rows)`?** For unpaginated MVP, `total` always equals `len(rows)`. The two-query pattern is used for forward-compatibility: when `limit`/`offset` are added (post-MVP), the `total` count across all pages must remain correct. This follows the exact precedent of F-045 agreed.md §4 (OQ-2).

**M1 lynchpin constraint**: both queries MUST carry the `triggered_by` owner filter (and the `status` filter when provided). This is verified structurally in the test suite via `literal_binds=True` SQL compilation assertions (see §8, tests T6 and T7).

---

## §6 Pagination Decision

**Decision: defer `limit`/`offset` query params to a post-MVP sprint.**

Rationale (identical to F-045):
- The spec verification criteria for F-049 do not mention pagination — they test `total=3`, status filter behaviour, and ordering. There is no requirement to ship `limit`/`offset` in this sprint.
- Per CLAUDE.md scope discipline, features not required by the spec's verification criteria should not be added in MVP without human approval.
- Run counts per user are small in MVP (tens to low hundreds). Returning all rows in a single query is acceptable.
- The `total` field in the response envelope is explicitly included for forward-compatibility: when pagination is added, `total` will report the full count while `items` contains only the current page.

If `limit`/`offset` are needed before a dedicated sprint, a reviewer/human can approve adding them to this sprint's scope.

---

## §7 Route Registration Order

**Current declaration order in `runs_router` (after F-048):**

```
POST ""            — trigger_extract_run        (F-018)
GET  /{id}         — get_run_detail             (F-048)
GET  /dagster/...  — get_run_status             (F-005)
```

**Required declaration order after this sprint:**

```
POST ""                        — trigger_extract_run   (F-018)   [unchanged position]
GET  ""                        — list_runs             (F-049)   [NEW — insert here]
GET  /{id}                     — get_run_detail        (F-048)   [unchanged]
GET  /dagster/{dagster_run_id} — get_run_status        (F-005)   [unchanged]
```

**Why order matters**: Declaring `GET ""` before `GET /{id}` is conventional and follows the established `POST ""` + `GET ""` pattern used in the datasets and recipes routers (F-045 precedent). It is not a FastAPI path-collision safeguard per se — `GET /api/runs` (no trailing path segment) and `GET /api/runs/{id}` (one trailing segment required) are structurally distinct paths that FastAPI can dispatch correctly regardless of declaration order, since `{id}` requires a non-empty segment. The real path-shadowing concern in this router is between `GET /{id}` and `GET /dagster/{dagster_run_id}` (same segment count, fixed prefix `dagster/` disambiguates them), which was addressed in F-048. Declaring `GET ""` first is good convention and aids readability; it is not required for correctness.

**Verified in `apps/api/dataplat_api/routers/runs.py`**: as of F-048, the current order is `POST ""`, then `GET /{id}`, then `GET /dagster/{dagster_run_id}`. The new `GET ""` handler must be inserted between `POST ""` and `GET /{id}`.

---

## §8 Test Plan

**File:** `apps/api/tests/test_runs_list.py` (new)

All tests follow the `test_datasets_list.py` pattern (F-045): `TestClient(app)`, `MagicMock(spec=Run)` row factory (`_make_run_list_item()`), `AsyncMock` session with `side_effect=[page_result, count_result]`. The `conftest.py` autouse `_patch_engine_begin` and `_patch_httpx_no_ssl` fixtures apply automatically.

**Session mock pattern** (two `execute()` calls):
```python
page_result = MagicMock()
page_result.scalars.return_value.all.return_value = [row1, row2]
count_result = MagicMock()
count_result.scalar_one.return_value = 2
session = AsyncMock()
session.execute = AsyncMock(side_effect=[page_result, count_result])
```
Note: `.scalars()`, `.all()`, and `.scalar_one()` are **synchronous** calls on the result proxy returned from `await session.execute()`. Use plain `MagicMock()` for the result proxies (not `AsyncMock`).

**Mock factory**: `_make_run_list_item()` populates all 14 ORM attributes (same discipline as `_make_run_detail()` in `test_runs_get.py`) using `MagicMock(spec=Run)`. All 14 columns are set even though `RunListItem` reads only 10, to avoid MagicMock attribute-access surprises.

**Expected `RunListItem` key set** (constant `_LIST_ITEM_KEYS`):
```python
_LIST_ITEM_KEYS = {
    "id", "dagster_run_id", "kind", "status",
    "started_at", "ended_at", "triggered_by",
    "dataset_id", "recipe_id", "source_collection_id",
}
```

**`_EXCLUDED_DETAIL_KEYS`** (must NOT appear in list items):
```python
_EXCLUDED_DETAIL_KEYS = {
    "asset_keys", "partition_keys", "config", "trigger_context",
}
```

### Test cases

| # | Test name | What it verifies | Maps to spec criterion |
|---|---|---|---|
| T1 | `test_list_runs_returns_200_with_items_and_total` | Three rows in session mock → `status_code == 200`, `body["total"] == 3`, `len(body["items"]) == 3` | **V1** |
| T2 | `test_list_runs_empty_returns_empty_list` | Session mock returns `[]` rows and `total=0` → `response.json() == {"items": [], "total": 0}` | Edge case |
| T3 | `test_list_runs_no_token_returns_401` | No `Authorization` header; no dep override → `status_code == 401`, `response.headers["WWW-Authenticate"] == "Bearer"` | Auth gate |
| T4 | `test_list_runs_owner_isolation` | Two separate user overrides (user A: 2 runs, user B: 1 run), independent session mocks → each user's call returns only their own runs (`total` matches) | Owner isolation |
| T5 | `test_list_runs_items_have_required_fields` | One `status='pending'` row (all nullable fields null) → all 10 keys in `_LIST_ITEM_KEYS` present; `isinstance(item["id"], int)`, `item["status"] == "pending"` | Field completeness |
| T6 | `test_list_runs_triggered_by_in_both_queries` (SQL-structural / M1 lynchpin) | Capture both `execute()` call args; compile each with `literal_binds=True`; assert `"triggered_by"` and mock user's id both appear in **page query** (call index 0) AND **COUNT query** (call index 1) — prevents `total` silently returning a global count if owner filter is dropped from either query | Owner-scope SQL guard (both queries) |
| T7 | `test_list_runs_status_filter_in_both_queries` (SQL-structural / M1 lynchpin extension, **parameterized**) | Decorated with `@pytest.mark.parametrize("status_value", ["pending", "running", "success", "failure"])`. For each variant: call `GET /api/runs?status=<status_value>`; capture both `execute()` call args; compile each with `literal_binds=True`; assert the literal string `status_value` appears in BOTH the page query (call index 0) AND the COUNT query (call index 1) compiled SQL. This ensures the status filter is wired into both queries for every valid status value, including `"running"` (V3) and `"success"` (V2). | Status filter SQL guard — **V2 + V3 structural (all four status values parameterized)** |
| T8 | `test_list_runs_status_filter_success` | Session mock returns only `status='success'` rows when `?status=success` → `body["items"]` all have `status == "success"`, `body["total"] == 2` | **V2** |
| T9 | `test_list_runs_status_filter_running` | Session mock returns only `status='running'` rows when `?status=running` → `body["items"]` all have `status == "running"`, `body["total"] == 1` | **V3** |
| T10 | `test_list_runs_invalid_status_returns_422` | `GET /api/runs?status=bogus` → `status_code == 422` (FastAPI `Literal` validation fires before handler body) | Enum validation |
| T11 | `test_list_runs_no_extra_fields_in_items` (schema guard) | One row with all 14 ORM attributes populated → `set(item.keys()) == _LIST_ITEM_KEYS`; none of `_EXCLUDED_DETAIL_KEYS` appear in the item | Schema boundary guard |
| T12 | `test_list_runs_page_query_has_correct_order_by` (SQL-structural / ORDER BY guard) | Capture the page query via `session.execute.call_args_list[0].args[0]`; compile with `literal_binds=True`; assert the compiled SQL contains the substrings `"started_at"`, `"NULLS LAST"`, and `"id"` in the ORDER BY clause. This prevents a handler that accidentally omits `.order_by()` from passing all tests undetected. | Ordering correctness |

**Test count: 12**.

**Ordering note**: An explicit ordering assertion is provided as T12 (added per reviewer finding M2). The SQL-structural tests T6 and T7 assert owner-scope and status-filter presence respectively; they do NOT independently verify the ORDER BY clause. T12 fills that gap using the same `literal_binds=True` SQL compilation pattern.

---

## §9 Verification Mapping

### V1: After triggering 3 runs, `GET /api/runs` returns `{"items": [...], "total": 3}`

Covered by:
- **T1** (`test_list_runs_returns_200_with_items_and_total`) — mocks 3 run rows and `total=3`, asserts both `len(items) == 3` and `body["total"] == 3`.
- **T5** (`test_list_runs_items_have_required_fields`) — confirms each item has all required fields with correct types.

### V2: `GET /api/runs?status=success` returns only completed runs

Covered by:
- **T8** (`test_list_runs_status_filter_success`) — mocks `status='success'` rows, asserts all returned items have `status == "success"`.
- **T7** (`test_list_runs_status_filter_in_both_queries`) — SQL-structural: asserts `"success"` literal appears in both the page query AND the COUNT query when `?status=success` is passed.

### V3: `GET /api/runs?status=running` returns only in-progress runs

Covered by:
- **T9** (`test_list_runs_status_filter_running`) — mocks `status='running'` rows, asserts all returned items have `status == "running"`.
- **T7** (`test_list_runs_status_filter_in_both_queries`, parameterized) — SQL-structural: the `@pytest.mark.parametrize` over all four status values includes `"running"` as one variant; that variant compiles its own SQL and asserts the literal `"running"` appears in both the page query AND the COUNT query. V3 SQL-structural coverage is therefore explicit, not implicit.

---

## §10 Open Questions

**OQ-1 — Multiple `?status=` values in a single request?**

*Question*: Should `GET /api/runs?status=success&status=failure` return runs with either status?

*Recommendation*: Single value only for MVP. A multi-value `status` filter would require `IN (...)` SQL and a `list[Literal[...]]` or `Query` annotation. No spec verification criterion tests multi-status. Reviewer should confirm single-value is sufficient.

**OQ-2 — Literal vs. Enum for status param?**

*Question*: Should we define a `RunStatus` Python enum (or `StrEnum`) for the query param, or use `Optional[Literal["pending", "running", "success", "failure"]]`?

*Recommendation*: `Literal` for MVP. The `Run.status` column is `sa.Text` (no Postgres enum type). There is no existing `RunStatus` enum in the codebase. Adding a `Literal` annotation is the minimal change. If a `RunStatus` enum is later needed for response schema validation or other purposes, it can be introduced as a separate concern. Reviewer should confirm.

**OQ-3 — Should `limit`/`offset` be added now?**

*Recommendation*: Defer. The spec verification criteria for F-049 test `total=3` (implying no pagination applied) and status filtering. No pagination test is present in the spec. See §6 for full rationale.

**OQ-4 — Should `triggered_by` field be excluded from `RunListItem` response?**

*Recommendation*: Include it. `triggered_by` is the caller's own user id (owner-scoped). The caller already knows their own id from the JWT. Including it is consistent with `RunDetailResponse` and makes the list item self-describing. Reviewer should confirm.

**OQ-5 — Ordering for an explicit ordering assertion test?**

*Resolved*: T12 (`test_list_runs_page_query_has_correct_order_by`) is required. T6 and T7 do NOT verify the ORDER BY clause — they only assert owner-scope and status-filter presence respectively. A handler that omits `.order_by()` entirely would pass all of T1–T11. T12 closes this gap using the same `literal_binds=True` SQL compilation pattern, asserting `"started_at"`, `"NULLS LAST"`, and `"id"` appear in the page query's ORDER BY clause.

---

## §11 Hard Invariants Audit

| # | Invariant (CLAUDE.md) | Status | One-line reason |
|---|---|---|---|
| 1 | **Lineage mandatory** — any Commit MUST record `parents[]` + processor identity + config hash + input refs | **N/A** | `GET /api/runs` is a pure read-only endpoint. No `Commit` object is created; no lineage event fires. |
| 2 | **Storage separation + CAS** — metadata in Postgres; content in MinIO/S3 by `sha256(content)`; no blob bytes in Postgres | **✓ Respected** | The endpoint reads only Postgres `run` rows. No MinIO/S3 interaction. No blob bytes anywhere. |
| 3 | **Schema frozen post-publish** — Silver/Gold schema changes require new commit | **N/A** | No schema mutations. Read-only endpoint. |
| 4 | **LLM calls go through the gateway** | **N/A** | No LLM calls. A list endpoint has no use for LLM calls. |
| 5 | **Async SQLAlchemy from day one** — every DB session is async; no `session.query()`; no sync sessions | **✓ Required** | Handler uses `async def`, `AsyncSession = Depends(get_session)`, `await session.execute(select(...).where(...))`, `.scalars().all()`, `.scalar_one()`. No `session.query()` anywhere. Same pattern as `list_datasets()` (F-045). |
| 6 | **OpenAPI ↔ TS type sync** — API schema change MUST be followed by `make codegen`; `packages/api-types/` diff in same commit | **Required — hard requirement** | `RunListItem` (10 fields) and `RunListResponse`, plus the new `GET /api/runs` path entry, extend the OpenAPI surface. Implementer MUST regenerate `packages/api-types/openapi.json` (see §3 snippet) and commit the diff in the **same** commit as Python source changes. CI will reject mismatches. |

---

## §12 Definition of Done

A sprint is `done` iff **all** of the following hold:

- [ ] `contracts/S049-F-049/agreed.md` exists with every item addressed.
- [ ] `apps/api/dataplat_api/schemas/runs.py` contains `RunListItem` (10 fields) and `RunListResponse`; module-level docstring updated.
- [ ] `apps/api/dataplat_api/routers/runs.py` contains `GET ""` handler `list_runs()` declared **before** `GET /{id}` and `GET /dagster/{dagster_run_id}`; `func` imported from `sqlalchemy`; `RunListItem` and `RunListResponse` added to the schema import line.
- [ ] `apps/api/tests/test_runs_list.py` contains all 12 tests (T1–T12, where T7 is parameterized over 4 status values); all pass.
- [ ] `bash verify/checks.sh backend` exits 0 (all pytest tests pass, including pre-existing F-048/F-018 suites).
- [ ] `packages/api-types/openapi.json` regenerated and committed in the **same** commit.
- [ ] `bash verify/checks.sh all` exits 0.
- [ ] `contracts/S049-F-049/review-final.md` ends with `APPROVED`.
- [ ] `spec/feature_list.json` F-049 `passes` flipped to `true`.
- [ ] `claude-progress.txt` closing entry appended.
- [ ] `git push` executed after sprint close.

---

## §13 Round-1 Addenda (reviewer Mode A round-1 → revision 2)

The following changes were made in response to the four findings in `contracts/S049-F-049/feedback.md`. Each finding is addressed below.

### M1 (MEDIUM) — T7 parameterized over all four status values

**Finding**: T7 was described as a single call with `?status=success`; the §9 V3 mapping claimed T7 covered V3 "by parameterisation", which was not reflected in the T7 test description. The SQL-structural assertion for `?status=running` was therefore absent in the test plan, leaving V3 without SQL-structural coverage.

**Fix applied**:
- **§8 T7** rewritten to use `@pytest.mark.parametrize("status_value", ["pending", "running", "success", "failure"])`. Each variant independently compiles the page and COUNT queries with `literal_binds=True` and asserts the status literal appears in both. The "Maps to spec criterion" column updated to `"V2 + V3 structural (all four status values parameterized)"`.
- **§9 V3** updated to describe T7's `"running"` variant explicitly — the parameterization is now accurately described, and the claim is no longer false.

### M2 (MEDIUM) — T12 added for ORDER BY structural assertion

**Finding**: No test verified the `ORDER BY run.started_at DESC NULLS LAST, run.id DESC` ordering. T6 and T7 only assert owner-scope and status-filter presence; a handler omitting `.order_by()` would pass all 11 tests undetected. OQ-5 was left open ("reviewer should decide").

**Fix applied**:
- **§8** — T12 `test_list_runs_page_query_has_correct_order_by` added. Uses the same `literal_binds=True` SQL compilation pattern as T6; captures the page query via `session.execute.call_args_list[0].args[0]`; asserts the compiled SQL contains `"started_at"`, `"NULLS LAST"`, and `"id"`. The incorrect ordering note (claiming T6/T7 "implicitly" verify ORDER BY) replaced with an accurate note describing T12's role.
- **Test count** updated from 11 to **12** in §8 and in the §12 DoD checklist.
- **OQ-5** resolved: T12 is required (no longer deferred to reviewer discretion).
- **§3 files table** test count updated to 12.

### L1 (LOW) — §3 "9 fields" corrected to "10 fields"

**Finding**: The `schemas/runs.py` row in §3 read "(9 fields)" but `RunListItem` has exactly 10 fields (`id`, `dagster_run_id`, `kind`, `status`, `started_at`, `ended_at`, `triggered_by`, `dataset_id`, `recipe_id`, `source_collection_id`).

**Fix applied**: Changed "(9 fields)" → "(10 fields)" in the §3 files table. No other changes needed — §4 and §8 already correctly reflected 10 fields.

### NIT-1 (NIT) — §7 shadowing-risk explanation softened

**Finding**: §7 claimed that declaring `GET /{id}` before `GET ""` would cause `GET /api/runs` to "fail to match or be caught by the parametric handler". This overstates the risk — `GET /api/runs` (no trailing segment) and `GET /api/runs/{id}` (one trailing segment required) are structurally distinct and FastAPI dispatches them correctly regardless of declaration order.

**Fix applied**:
- **§7 "Why order matters"** reworded: declaring `GET ""` before `GET /{id}` is conventional and follows the F-045 `POST ""` + `GET ""` pattern; it is not a FastAPI path-collision safeguard. The real shadowing concern in this router (between `GET /{id}` and `GET /dagster/{dagster_run_id}`) is correctly noted as addressed in F-048.
- **§1 route registration constraint** preamble similarly softened: "conventional" and "aids readability" rather than framing it as required to prevent match failure.
- The ordering recommendation itself (declare `GET ""` first) is unchanged — only the justification is sharpened.
