# Sprint S045-F-045 — Proposed Contract

**Feature**: F-045 — List datasets: `GET /api/datasets` returns all datasets with their status, recipe_id, version_tag, sample_count, and size_bytes  
**Depends on**: F-044 (`passes: true`)  
**Sprint directory**: `contracts/S045-F-045/`  
**Author**: leader (inline)  
**Date**: 2026-06-04  
**Revision**: 2

---

## 1. Goal

Add a `GET /api/datasets` endpoint to the existing datasets router that returns all dataset rows owned by the authenticated caller, scoped by `Dataset.materialized_by == current_user.id`. The response uses the same `{items, total}` envelope established by `GET /api/recipes` (F-038). Each item exposes the seven fields required by F-045's `verification[]`: `id`, `recipe_id`, `version_tag`, `status`, `sample_count`, `size_bytes`, and `materialized_at`. Items are ordered newest-first (`materialized_at DESC NULLS LAST, id DESC`) to surface completed datasets at the top. Two new Pydantic schemas — `DatasetListItem` and `DatasetListResponse` — are added to `apps/api/dataplat_api/schemas/datasets.py`, matching the `RecipeListItem` / `RecipeListResponse` precedent. Because these schemas extend the public OpenAPI surface, `make codegen` must be run and the resulting `packages/api-types/` diff committed in the same commit per hard invariant #6.

---

## 2. Out of Scope

The following are explicitly **not** implemented in this sprint:

- `GET /api/datasets/{id}` — single-dataset detail endpoint (F-046).
- Pagination, cursor, limit/offset query parameters — MVP list is unpaginated (same rationale as F-038; dataset counts per user are small).
- Filters by `status`, `recipe_id`, or date range.
- Cross-user visibility or shared/internal datasets — MVP uses owner-scoping only (`materialized_by == current_user.id`).
- Admin "list all datasets" variant.
- Returning `recipe_snapshot`, `hf_repo_uri`, `dagster_run_id`, `stats`, or `dataset_card_md` in the list item — these are detail-level fields deferred to F-046.
- Any writes or state mutations.

---

## 3. Files Changed

| File | Status | Reason |
|---|---|---|
| `apps/api/dataplat_api/schemas/datasets.py` | **edit** | Add `DatasetListItem` (7 fields) and `DatasetListResponse` envelope; keep existing `MaterializeResponse` unchanged. |
| `apps/api/dataplat_api/routers/datasets.py` | **edit** | Add `GET ""` route `list_datasets()` above the existing `POST /{recipe_id}/materialize` route; import `DatasetListItem`, `DatasetListResponse` from `dataplat_api.schemas.datasets`. `func`, `select` are already imported. |
| `apps/api/tests/test_datasets_list.py` | **create** | New test module — 9 unit tests for the list endpoint (see §5). |
| `packages/api-types/` | **generated** | Updated by `make codegen` after schema additions; committed in the same commit per invariant #6. |

No other files are touched. No Alembic migration needed — all columns referenced (`id`, `recipe_id`, `version_tag`, `status`, `sample_count`, `size_bytes`, `materialized_at`, `materialized_by`) already exist on the `dataset` table (confirmed from `apps/api/dataplat_api/db/models.py`).

**Codegen hard requirement (invariant #6):** Implementer MUST run `make codegen` (or verify `packages/api-types/openapi.json` reflects the new `DatasetListItem` + `DatasetListResponse` schemas) and commit the resulting diff in the SAME commit. If `Makefile` is absent, the OpenAPI diff must be confirmed manually — it is not sufficient to rely on the `checks.sh contract` no-op guard.

---

## 4. Endpoint Contract

### Method and path

```
GET /api/datasets
```

### Auth

Bearer token required (same `Depends(get_current_user)` as all other protected endpoints). Missing or invalid token → **401** with `WWW-Authenticate: Bearer`.

### Owner-scoping rule

The query filters on `Dataset.materialized_by == current_user.id`. This is the field set by `POST /api/datasets/{recipe_id}/materialize` (F-042, confirmed in `datasets.py` line `materialized_by=current_user.id`). A user sees exactly and only the datasets they triggered — no cross-user leakage.

### Response schema

**HTTP 200** — always (even for an empty list):

```json
{
  "items": [
    {
      "id":              42,
      "recipe_id":       7,
      "version_tag":     "v1",
      "status":          "done",
      "sample_count":    1500,
      "size_bytes":      204800,
      "materialized_at": "2026-06-04T10:00:00Z"
    }
  ],
  "total": 1
}
```

**Field types** (Pydantic `DatasetListItem`, `from_attributes=True`):

| Field | Python type | Nullable | Notes |
|---|---|---|---|
| `id` | `int` | No | BigInteger PK |
| `recipe_id` | `int \| None` | Yes | FK to `recipe.id`; None only if row was orphaned — should not occur in practice. Frontend/client must guard against null before constructing a recipe detail URL. F-046 and F-069 implementers should be aware. |
| `version_tag` | `str` | No | e.g. `"v1"`, `"v2"`. F-042 always sets this before INSERT — non-null guarantee holds. |
| `status` | `str` | No | `"pending"`, `"running"`, `"failed"`, `"done"` |
| `sample_count` | `int \| None` | Yes | Null until materialization completes (F-044 sets it) |
| `size_bytes` | `int \| None` | Yes | Null until materialization completes (F-044 sets it) |
| `materialized_at` | `datetime \| None` | Yes | Null until materialization completes (F-044 sets it) |

**Envelope** (`DatasetListResponse`):

| Field | Python type | Notes |
|---|---|---|
| `items` | `list[DatasetListItem]` | Ordered newest-first |
| `total` | `int` | Count of all owner-scoped dataset rows (same as `len(items)` for unpaginated MVP; included for forward-compatibility) |

### Ordering

`ORDER BY materialized_at DESC NULLS LAST, id DESC`

Rationale: `materialized_at` is `NULL` for `status='pending'` and `status='running'` rows (F-044 sets it only on `'done'`). `NULLS LAST` pushes in-flight datasets to the bottom. Tie-break on `id DESC` ensures deterministic ordering when two rows have identical `materialized_at`. All dataset rows are returned regardless of status (pending, running, failed, done). Failed rows are tombstones (per F-042 agreed.md) and are included in the list for audit visibility.

### Status codes

| Code | Condition |
|---|---|
| 200 | Success (including empty list) |
| 401 | Missing or invalid Bearer token |
| 422 | Malformed request (no query params for MVP, so this is effectively unreachable) |

No 404 is returned — an empty list is the correct response when the user has no datasets.

---

## 5. Tests

All tests live in `apps/api/tests/test_datasets_list.py`. They follow the same unit-test pattern as `test_recipes_list.py`: `TestClient` with `conftest.py` autouse fixtures, `AsyncMock` session overrides, no live DB or compose stack required.

The `GET /api/datasets` handler calls `session.execute()` **twice**: once for the full row list (`.scalars().all()`), once for the total count (`.scalar_one()`). The session mock uses `AsyncMock(side_effect=[page_result, count_result])` where both result proxies are plain `MagicMock` (not `AsyncMock`), matching the pattern in `test_recipes_list.py`.

Dataset row mocks are built with a `_make_dataset()` factory that sets all 13 ORM-mapped attributes (`id`, `recipe_id`, `recipe_snapshot`, `version_tag`, `hf_repo_uri`, `dataset_card_md`, `sample_count`, `size_bytes`, `stats`, `status`, `materialized_by`, `materialized_at`, `dagster_run_id`) even though `DatasetListItem` reads only 7 of them — avoids MagicMock attribute-access surprises if fields are added. Uses `MagicMock(spec=Dataset)`, consistent with `_make_recipe()` in `test_recipes_list.py`.

### Test cases

1. **`test_list_datasets_returns_200_with_items_and_total`**  
   Two dataset rows in session mock → asserts `status_code == 200`, `body["total"] == 2`, `len(body["items"]) == 2`.

2. **`test_list_datasets_items_have_required_fields`**  
   One `status='done'` row with all fields populated → asserts all 7 required keys (`id`, `recipe_id`, `version_tag`, `status`, `sample_count`, `size_bytes`, `materialized_at`) are present in the item; asserts `isinstance(item["id"], int)`, `isinstance(item["version_tag"], str)`, `item["status"] == "done"`.

3. **`test_list_datasets_no_token_returns_401`**  
   No dependency override, no `Authorization` header → asserts `status_code == 401` and `response.headers["WWW-Authenticate"] == "Bearer"`.

4. **`test_list_datasets_empty_returns_empty_list`**  
   Session mock returns `[]` rows and `total=0` → asserts `response.json() == {"items": [], "total": 0}`.

5. **`test_list_datasets_only_own_datasets`**  
   Two separate user overrides (user A has 2 datasets, user B has 1 dataset), each with independent session mocks → asserts that each user's call returns the correct `total` and `len(items)` for their own data (isolation check).

6. **`test_list_datasets_materialized_by_in_query`** (SQL-structural)  
   Captures `session.execute.call_args_list[0].args[0]`, compiles with `literal_binds=True`, asserts `"materialized_by"` and the string representation of the mock user's id both appear in the compiled SQL string (row-list query). Then captures `session.execute.call_args_list[1].args[0]`, compiles with `literal_binds=True`, and asserts `"materialized_by"` and the mock user's id also appear in that compiled SQL string (COUNT query). This ensures the owner filter is applied to both the list and the total-count queries, so `total` cannot silently return a global row count.

7. **`test_list_datasets_pending_row_has_null_fields`**  
   One row with `status='pending'`, `sample_count=None`, `size_bytes=None`, `materialized_at=None` → asserts the response item has `status == "pending"` and `sample_count is None` and `size_bytes is None` and `materialized_at is None`.

8. **`test_list_datasets_done_row_fields_all_present`** (maps to F-045 verification[0])  
   One row with `status='done'`, `sample_count=1500`, `size_bytes=204800`, `materialized_at=<non-null datetime>` → asserts `item["status"] == "done"`, `item["sample_count"] == 1500`, `item["size_bytes"] == 204800`, `item["materialized_at"]` is not `None`.

9. **`test_list_datasets_extra_fields_not_in_items`** (schema guard)  
   One row in mock (all 13 attributes populated) → asserts none of `["recipe_snapshot", "hf_repo_uri", "dataset_card_md", "dagster_run_id", "stats", "materialized_by"]` appear in the response item, confirming the slim `DatasetListItem` schema excludes detail-level fields.

---

## 6. Verification Mapping

### F-045 `verification[0]`
> "After a successful materialization, `GET /api/datasets` returns array containing the new dataset with `status='done'`"

Covered by:
- **`test_list_datasets_done_row_fields_all_present`** (test §5, item 8) — mocks a `status='done'` dataset row and asserts the endpoint returns it in `items` with `status == "done"`. (Note: 'array' in the spec means `items` key in the `{items, total}` envelope.)
- **`test_list_datasets_returns_200_with_items_and_total`** (item 1) — confirms the endpoint returns a non-empty array.

For `bash verify/checks.sh backend`: the pytest run (`uv run pytest -q`) in `apps/api/` will execute these tests. Both must pass.

### F-045 `verification[1]`
> "Each item includes `id`, `recipe_id`, `version_tag`, `status`, `sample_count`, `size_bytes`, `materialized_at`"

Covered by:
- **`test_list_datasets_items_have_required_fields`** (item 2) — asserts all 7 field names are present in the response item.
- **`test_list_datasets_done_row_fields_all_present`** (item 8) — asserts non-null values for `sample_count`, `size_bytes`, `materialized_at` when `status='done'`.
- **`test_list_datasets_pending_row_has_null_fields`** (item 7) — asserts nullable fields are correctly null for `status='pending'`.
- **`test_list_datasets_extra_fields_not_in_items`** (item 9) — asserts no extra fields leak into the response (schema boundary guard).

---

## 7. Risks / Open Questions

**OQ-1 — `NULLS LAST` SQL dialect.**  
Confirmed: `Dataset.materialized_at.desc().nulls_last()` generates `ORDER BY dataset.materialized_at DESC NULLS LAST` on SQLAlchemy 2.0.41 + Postgres. This is the required implementation. No fallback needed.

**OQ-2 — `total` double-query vs. `len(items)`.**  
For the unpaginated MVP, `total` will always equal `len(items)`. We still issue two queries (following the `recipes.py` precedent exactly) for forward-compatibility. This is a minor performance cost that is acceptable at MVP scale.

**OQ-3 — `recipe_id` nullability in response.**  
`Dataset.recipe_id` is `nullable=True` in the ORM model. The `DatasetListItem` schema declares it as `int | None`. In practice every dataset row should have a `recipe_id` (set at INSERT time by F-042), but the nullable type is preserved to match the DB schema faithfully.

**OQ-4 — codegen gating.**  
See §3 codegen hard requirement. Implementer MUST run `make codegen` (or verify `packages/api-types/openapi.json` reflects the new `DatasetListItem` + `DatasetListResponse` schemas) and commit the resulting diff in the SAME commit. If `Makefile` is absent, the OpenAPI diff must be confirmed manually — it is not sufficient to rely on the `checks.sh contract` no-op guard.

**OQ-5 — Ordering field on `status='pending'`/`status='running'` rows.**  
`materialized_at` is `NULL` for these statuses. `NULLS LAST` pushes them to the bottom of the list. If users later want in-progress jobs at the top, the ordering clause must be changed. For MVP, newest-completed-first is the correct UX.

---

## 8. Invariants Check

| # | Invariant | Status |
|---|---|---|
| 1 | **Lineage is mandatory** (parents[] + processor identity + config hash + input refs) | **N/A** — this endpoint is a pure SELECT; no commit, no new Commit record, no lineage event. |
| 2 | **Storage separation + CAS** (metadata in Postgres; content in MinIO by sha256) | **Respected** — the endpoint reads only Postgres `dataset` rows. No MinIO access, no blob bytes in Postgres. |
| 3 | **Schema frozen post-publish** (Silver/Gold schema changes require new commit) | **N/A** — this endpoint makes no schema mutations. |
| 4 | **LLM calls go through the gateway** | **N/A** — no LLM calls in a list endpoint. |
| 5 | **Async SQLAlchemy from day one** (no `session.query()`, no sync sessions) | **Respected** — handler uses `async def`, `AsyncSession`, and `await session.execute(select(...))`. No `session.query()` anywhere. |
| 6 | **OpenAPI ↔ TS type sync** (`make codegen` + `packages/api-types/` diff in same commit) | **Required** — new schemas `DatasetListItem` and `DatasetListResponse` extend the OpenAPI surface. Implementer MUST run `make codegen` (or verify `packages/api-types/openapi.json` reflects the new `DatasetListItem` + `DatasetListResponse` schemas) and commit the resulting diff in the SAME commit. If `Makefile` is absent, the OpenAPI diff must be confirmed manually — it is not sufficient to rely on the `checks.sh contract` no-op guard. |

---

## §11 Round-2 Addenda

Changes applied in revision 2 in response to Mode A reviewer feedback (`contracts/S045-F-045/feedback.md`). Each entry references the finding ID and states where the fix lives.

| Finding | Severity | Fix summary | Location in this document |
|---------|----------|-------------|---------------------------|
| **M1** | MEDIUM | Added second assertion block on `session.execute.call_args_list[1]` (COUNT query) compiled with `literal_binds=True`, asserting `"materialized_by"` and user id appear — prevents `total` from silently returning a global count if the owner filter is accidentally omitted from the COUNT query. | §5, test 6 (`test_list_datasets_materialized_by_in_query`) |
| **M2** | MEDIUM | Replaced OQ-4 advisory language with an explicit hard requirement in both §3 and §8 (invariant #6): implementer MUST confirm `packages/api-types/openapi.json` diff and commit it in the SAME commit; manual verification required if `Makefile` is absent. | §3 (codegen hard requirement paragraph), §7 OQ-4, §8 invariant #6 |
| **L1** | LOW | Added note to `recipe_id` field row: frontend/client must guard against null before constructing a recipe detail URL; F-046 and F-069 implementers should be aware. | §4 field types table, `recipe_id` row |
| **L2** | LOW | Added explicit sentence that all statuses (pending, running, failed, done) are returned; failed rows are tombstones per F-042 and are included for audit visibility. | §4 Ordering rationale paragraph |
| **L3** | LOW | Added note to `version_tag` field row confirming F-042 always sets this before INSERT — non-null guarantee holds. | §4 field types table, `version_tag` row |
| **NIT-1** | NIT | Clarified `routers/datasets.py` import note: only `DatasetListItem` and `DatasetListResponse` need to be added; `func` and `select` are already imported. | §3 files changed table, `routers/datasets.py` row |
| **NIT-2** | NIT | Added explicit note that `_make_dataset()` uses `MagicMock(spec=Dataset)`, consistent with `_make_recipe()` in `test_recipes_list.py`. | §5 `_make_dataset()` factory description |
| **NIT-3** | NIT | Added parenthetical clarifying that 'array' in F-045 `verification[0]` refers to the `items` key in the `{items, total}` envelope. | §6 Verification Mapping, `verification[0]` first bullet |
| **NIT-4** | NIT | Replaced OQ-1 fallback note with confirmed implementation: `.desc().nulls_last()` is verified on SQLAlchemy 2.0.41 + Postgres; no fallback needed. | §7 OQ-1 |
