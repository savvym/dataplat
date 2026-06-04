# Sprint S046-F-046 — Proposed Contract

**Feature**: F-046 — Get dataset detail: `GET /api/datasets/{id}` returns the full dataset record including `recipe_snapshot` and `stats`  
**Depends on**: F-044 (`passes: true`) — formal dependency per `feature_list.json`. F-045 (`passes: true`) — practical predecessor (same router file); not a formal spec dependency.  
**Sprint directory**: `contracts/S046-F-046/`  
**Author**: leader (inline)  
**Date**: 2026-06-04  
**Revision**: 2

---

## 1. Goal

Add a `GET /api/datasets/{id}` endpoint to the existing datasets router that returns the complete dataset record for the authenticated caller. The endpoint exposes all fields that the slim `DatasetListItem` (F-045) intentionally omitted — specifically `recipe_snapshot` (the frozen recipe JSON captured at materialize time), `stats` (JSONB analytics blob written by the IO manager at status=done), and `hf_repo_uri` (the S3 URI assigned during materialize) — plus the full set of lifecycle fields (`dagster_run_id`, `materialized_by`, `created_at`-equivalent fields). A new Pydantic schema `DatasetDetailResponse` is added to the existing `schemas/datasets.py`. Owner-scoping collapses wrong-owner and not-found into a single 404, following the identical pattern established by `GET /api/recipes/{id}` (F-039). Because this endpoint extends the public OpenAPI surface, `packages/api-types/openapi.json` must be regenerated and the diff committed in the same commit per hard invariant #6.

---

## 2. Scope

- Add `GET /api/datasets/{id}` route handler `get_dataset()` to `apps/api/dataplat_api/routers/datasets.py`.
- Add `DatasetDetailResponse` Pydantic schema to `apps/api/dataplat_api/schemas/datasets.py`.
- Import and wire `DatasetDetailResponse` in the router.
- Write unit tests in `apps/api/tests/test_datasets_get.py` (9 tests; see §8).
- Regenerate `packages/api-types/openapi.json` to include the new path `/api/datasets/{id}` and the `DatasetDetailResponse` schema; commit the diff in the same commit.
- Register the route before `POST /{recipe_id}/materialize` in route-declaration order (read route before write route, per existing convention in `sources.py` and `recipes.py`).

---

## 3. Out of Scope

- **F-047** — Dataset download: `GET /api/datasets/{id}/download` is a separate sprint.
- **F-070** — Dataset detail page (web UI): frontend component is a separate sprint.
- Filtering, sorting, or pagination of the detail endpoint.
- Cross-user / shared visibility — MVP uses owner-scoping only (§11.6 deferred).
- Admin or service-account bypass of the owner-scope filter.
- Returning `dataset_card_md` as a rendered or formatted field (return as-is from the ORM column, nullable `str | None`).
- Shaping or validating the contents of `stats` or `recipe_snapshot` beyond passing through the raw JSONB dict.
- Any write, state mutation, or Dagster interaction.
- New Alembic migration — all columns already exist on the `dataset` table as of F-042/F-044.

---

## 4. Files to Add / Modify

| File | Status | Reason |
|---|---|---|
| `apps/api/dataplat_api/schemas/datasets.py` | **edit** | Add `DatasetDetailResponse` (13 fields); keep `MaterializeResponse`, `DatasetListItem`, `DatasetListResponse` unchanged. |
| `apps/api/dataplat_api/routers/datasets.py` | **edit** | Add `GET /{id}` route `get_dataset()` between the existing `GET ""` and `POST /{recipe_id}/materialize` routes. Import `DatasetDetailResponse` from `dataplat_api.schemas.datasets`. |
| `apps/api/tests/test_datasets_get.py` | **create** | New test module — 9 unit tests (see §8). |
| `packages/api-types/openapi.json` | **generated** | Updated by regenerating the OpenAPI spec after adding the new path and schema; committed in the same commit per invariant #6. |

No other files are touched. No Alembic migration is required.

---

## 5. Implementation Steps

1. **Confirm Dataset ORM column inventory** (already done for this contract; see §6): 13 mapped columns — `id`, `recipe_id`, `recipe_snapshot`, `version_tag`, `hf_repo_uri`, `dataset_card_md`, `sample_count`, `size_bytes`, `stats`, `status`, `materialized_by`, `materialized_at`, `dagster_run_id`. No `created_at` column exists on `dataset` (confirmed from `apps/api/dataplat_api/db/models.py`; Dataset has no `created_at`, unlike `Recipe` and `SourceCollection`).

2. **Edit `schemas/datasets.py`** — append `DatasetDetailResponse` after the existing `DatasetListResponse` class. All 13 ORM columns are represented; see §6 for exact field names, types, and sources.

3. **Edit `routers/datasets.py`** — add `DatasetDetailResponse` to the import from `dataplat_api.schemas.datasets`. Register the new route:
   ```python
   @router.get("/{id}", response_model=DatasetDetailResponse)
   async def get_dataset(
       id: int,
       current_user: User = Depends(get_current_user),
       session: AsyncSession = Depends(get_session),
   ) -> DatasetDetailResponse:
       """Return the full dataset record for the authenticated owner.

       Owner-scoping: combines ``id == ?`` AND ``materialized_by == ?`` in one
       query so that a non-existent id and an id owned by another user both
       return 404 (no-enumeration-leak, mirrors get_recipe).
       ``materialized_by`` is the owner FK on Dataset (analogous to
       ``owner_id`` on Recipe).
       """
   ```
   Handler body:
   - Execute `select(Dataset).where(Dataset.id == id).where(Dataset.materialized_by == current_user.id)` — single query combining both filters (owner-scope collapse, see §9).
   - `result.scalar_one_or_none()` — if `None`, raise `HTTPException(status_code=404, detail="Dataset not found")`.
   - Return `DatasetDetailResponse.model_validate(row)`.

4. **Route ordering**: the `GET /{id}` route MUST be declared after `GET ""` and before `POST /{recipe_id}/materialize` in the file. FastAPI resolves routes in declaration order; `GET ""` and `GET /{id}` operate on distinct paths (no collision), but consistent ordering (read-before-write, fixed-path-before-parameterized) matches the pattern in `recipes.py`.

5. **Write tests** in `apps/api/tests/test_datasets_get.py` following the `test_recipes_get.py` structure (see §8 for the full list).

6. **Regenerate OpenAPI spec**: since no `Makefile` exists at the repo root (confirmed during contract drafting), the implementer MUST regenerate `packages/api-types/openapi.json` manually by running:
   ```
   cd apps/api && uv run python -c "
   import json
   from dataplat_api.main import app
   from fastapi.openapi.utils import get_openapi
   spec = get_openapi(title=app.title, version=app.version, routes=app.routes)
   with open('../../packages/api-types/openapi.json', 'w') as f:
       json.dump(spec, f, indent=2)
   "
   ```
   (or whatever the established regeneration command is for this repo — see how S045 implementer produced the `DatasetListItem` + `DatasetListResponse` diff). The resulting `packages/api-types/openapi.json` diff MUST be staged and committed in the **same** commit as the Python source changes. This is a hard requirement, not advisory (invariant #6).

7. **Commit** all four changed/created files (`schemas/datasets.py`, `routers/datasets.py`, `tests/test_datasets_get.py`, `packages/api-types/openapi.json`) in a single commit with a descriptive message.

---

## 6. Schema (Pydantic) — `DatasetDetailResponse`

Sourced from `apps/api/dataplat_api/db/models.py` `Dataset` class (lines 249–280). All 13 ORM-mapped columns are included. The `Dataset` model has **no** `created_at` column; do not invent one.

```python
class DatasetDetailResponse(BaseModel):
    """Full dataset record for GET /api/datasets/{id} (F-046).

    Exposes all 13 ORM-mapped columns of the ``dataset`` table.
    ``recipe_snapshot`` is the frozen deep-copy of ``recipe.definition``
    captured at materialize time — always a dict (JSONB NOT NULL).
    ``stats`` is nullable JSONB written by the IO manager on status='done'.
    ``hf_repo_uri`` is the S3 URI assigned during materialize (never null
    for any row that survived the insert; set to '__pending__' briefly
    in-transaction then replaced before commit — so always a non-null str).
    ``dataset_card_md`` is nullable text; not populated in MVP.
    ``materialized_at`` is None until status='done' (set by F-044 IO manager).
    ``dagster_run_id`` is None on status='failed' rows where Step 9 was not reached.
    """

    model_config = ConfigDict(from_attributes=True)

    # ── Identity ──────────────────────────────────────────────────────────
    id: int                        # Dataset.id              BigInteger PK
    recipe_id: int | None          # Dataset.recipe_id       BigInteger FK nullable

    # ── Version / routing ─────────────────────────────────────────────────
    version_tag: str               # Dataset.version_tag     Text NOT NULL
    hf_repo_uri: str               # Dataset.hf_repo_uri     Text NOT NULL

    # ── Frozen recipe contract ────────────────────────────────────────────
    recipe_snapshot: dict          # Dataset.recipe_snapshot JSONB NOT NULL

    # ── Materialization outputs ───────────────────────────────────────────
    sample_count: int | None       # Dataset.sample_count    BigInteger nullable
    size_bytes: int | None         # Dataset.size_bytes      BigInteger nullable
    stats: dict | None             # Dataset.stats           JSONB nullable
    dataset_card_md: str | None    # Dataset.dataset_card_md Text nullable

    # ── Lifecycle ─────────────────────────────────────────────────────────
    status: str                    # Dataset.status          Text NOT NULL
    materialized_by: int | None    # Dataset.materialized_by BigInteger FK nullable
    materialized_at: datetime | None  # Dataset.materialized_at DateTime nullable
    dagster_run_id: str | None     # Dataset.dagster_run_id  Text nullable
```

**Field count**: 13 — matching all 13 `Mapped[...]` columns in the ORM model exactly. No column is omitted; no synthetic field is added.

**`recipe_snapshot` type note**: the ORM column is `Mapped[dict]` (`JSONB NOT NULL`). The response type is `dict` (not `dict | None`). Every Dataset row has a non-null `recipe_snapshot` (enforced by the `NOT NULL` DB constraint and the `materialize_dataset` handler which always passes `copy.deepcopy(recipe.definition)`).

**`stats` type note**: the ORM column is `Mapped[Optional[dict]]` (`JSONB nullable`). For status='pending', status='running', and status='failed' rows this is `None`. For status='done' rows it is populated by the F-044 IO manager. The response exposes it as-is — no reshaping.

**`hf_repo_uri` type note**: ORM column is `Mapped[str]` (`Text NOT NULL`). Always present; never null. For status='failed' rows where Dagster calls failed after commit, this may retain the value `s3://datasets/{id}_{version_tag}` (assigned in-transaction in Step 5 of materialize).

---

## 7. Verification Plan

### V1 — `GET /api/datasets/{id}` returns 200 with all required fields

Checked by unit test `test_get_dataset_200_all_fields` (see §8 test 1):
- Mock session returns a complete `_make_dataset()` row with all 13 attributes set.
- Assert response status is 200.
- Assert all 13 field keys are present in the JSON body.
- Assert `recipe_snapshot` is a `dict` (not a string, not null).
- Assert `hf_repo_uri` is a non-empty string.
- Assert `stats` is the passed-through value (may be null — tested separately).

### V2 — `GET /api/datasets/99999` returns 404

Checked by unit test `test_get_dataset_not_found_returns_404` (see §8 test 2):
- Mock session returns `scalar_one_or_none() == None` (simulates a non-existent id).
- Assert response status is 404.
- Assert `response.json() == {"detail": "Dataset not found"}`.

---

## 8. Test List

File: `apps/api/tests/test_datasets_get.py`

All tests follow the `test_recipes_get.py` structure: `TestClient(app)`, `MagicMock(spec=Dataset)` row factory, `AsyncMock` session with `scalar_one_or_none()` on a synchronous `MagicMock` result proxy. The mock factory `_make_dataset_detail()` populates all 13 ORM attributes (same approach as `_make_dataset()` in `test_datasets_list.py`, `_make_recipe_detail()` in `test_recipes_get.py`).

| # | Test name | What it checks |
|---|---|---|
| 1 | `test_get_dataset_200_all_fields` | Status='done' row → 200, all 13 keys present, correct values for `id`, `recipe_snapshot`, `hf_repo_uri`, `status`. Maps to **V1**. |
| 2 | `test_get_dataset_not_found_returns_404` | Session returns `None` for id=99999 → 404 `{"detail": "Dataset not found"}`. Maps to **V2**. |
| 3 | `test_get_dataset_wrong_owner_returns_404` | Session returns `None` (simulates row exists for user id=99, not the mock user id=9) → same 404. Confirms no-enumeration-leak. |
| 4 | `test_get_dataset_no_token_returns_401` | No `Authorization` header; no dependency override → real `oauth2_scheme` raises 401 with `WWW-Authenticate: Bearer`. |
| 5 | `test_get_dataset_recipe_snapshot_is_dict` | `recipe_snapshot` in response is `dict`, not a JSON-encoded string. Guard against accidental double-serialization. |
| 6 | `test_get_dataset_stats_nullable` | Row with `stats=None` → 200, `response.json()["stats"] is None`. Confirms nullable field passes through correctly. |
| 7 | `test_get_dataset_materialized_by_in_query` | Structural (SQL-capture): single `execute()` call; compile with `literal_binds=True`; assert `"materialized_by"` and the mock user's id both appear in the compiled SQL. Mirrors `test_get_recipe_owner_id_in_query`. |
| 8 | `test_get_dataset_no_extra_fields_leaked` | `dataset_card_md` is a nullable field — confirm it IS present in detail response (contrast with list endpoint where it is excluded). Confirm no unexpected extra keys beyond the 13 defined in `DatasetDetailResponse`. Achieved by asserting `set(response.json().keys()) == {expected_13_keys}`. |
| 9 | `test_get_dataset_invalid_id_returns_422` | Non-integer path segment (`/api/datasets/not-a-number`) → 422 before handler body executes. Auth dependency override is set (so 401 does not interfere). Asserts `response.status_code == 422`. No session mock call is required — FastAPI path-param validation fires before dependency injection. |

**Session mock pattern** (same as `test_recipes_get.py` — single `execute()` call):
```python
result_mock = MagicMock()
result_mock.scalar_one_or_none.return_value = dataset_row_or_none
session = AsyncMock()
session.execute = AsyncMock(return_value=result_mock)
```

---

## 9. Owner-Scope Policy

**Rule**: `GET /api/datasets/{id}` MUST be owner-scoped. The handler combines `Dataset.id == id` AND `Dataset.materialized_by == current_user.id` in a **single** `SELECT` query. A dataset row that exists but is owned by a different user produces the same 404 as a non-existent id. This collapses wrong-owner → 404, preventing existence leak.

**Rationale and precedent**: F-039's `get_recipe()` uses the identical pattern (`Recipe.id == id AND Recipe.owner_id == current_user.id`), and the code comment in `recipes.py` line 131 explicitly states: *"Owner-scoping: combines `id == ?` AND `owner_id == ?` in one query so that a non-existent id and an id owned by another user both return 404 (no-enumeration-leak)"*. F-045's `list_datasets()` filters `Dataset.materialized_by == current_user.id` (not `owner_id` — the Dataset model uses `materialized_by` for the ownership FK). `GET /api/datasets/{id}` follows the same field.

**The filter field is `materialized_by`**, not `owner_id`. The `Dataset` ORM model has no `owner_id` column; the owner FK is `Dataset.materialized_by` (`BigInteger FK users.id`), set by `materialize_dataset()` as `materialized_by=current_user.id`.

**Wrong-owner test (test 3)** simulates this by returning `None` from the mock session — which is the correct mock for a query that combined both filters and found no matching row — and asserts a 404 with `{"detail": "Dataset not found"}`.

---

## 10. Risks / Open Questions

**OQ-1 — Should `stats` be returned as-is (raw JSONB) or reshaped?**

Decision: return as-is. The `stats` column is `JSONB nullable` with no defined schema in the design doc or any existing code. Its structure depends on what the F-044 IO manager writes, which is outside this sprint's scope. Passing through the raw dict is the safe choice; a typed `StatsShape` schema can be added later without a breaking change (nullable dict → typed model is backward-compatible from a client perspective). **Resolution: `stats: dict | None`, passed through unchanged.**

**OQ-2 — What does a status='failed' row look like in the response?**

A failed row is a valid, fully returned record. Fields will be:
- `status`: `"failed"`
- `recipe_snapshot`: populated (set before the commit in Step 4/5 of `materialize_dataset`)
- `hf_repo_uri`: populated with `s3://datasets/{id}_{version_tag}` (set in-transaction Step 5)
- `dagster_run_id`: `None` if Dagster call failed before Step 9 could write it back, or the backfillId if Step 9 succeeded before a later failure (not relevant for current tombstone path)
- `sample_count`, `size_bytes`, `stats`, `materialized_at`: all `None` (never set by F-044 IO manager if status='failed')
- `dataset_card_md`: `None`

The endpoint returns status='failed' rows without modification — they are audit tombstones. The caller decides what to display. No special-casing or error response for failed rows. **Resolution: return as-is; all nullable fields are null; status='failed' is explicit.**

**OQ-3 — Route ordering: does `GET /{id}` conflict with `GET ""` or `POST /{recipe_id}/materialize`?**

No conflict. `GET ""` matches `GET /api/datasets` (empty suffix); `GET /{id}` matches `GET /api/datasets/<integer>`. FastAPI will route them independently. `POST /{recipe_id}/materialize` uses a different method (POST) and a two-segment path (`/{recipe_id}/materialize`), so there is no ambiguity with `GET /{id}`. Declaration order `GET "" → GET /{id} → POST /{recipe_id}/materialize` is safe and consistent with `recipes.py` (`GET "" → POST "" → GET /{id} → PUT /{id} → POST /{id}/preview`).

**OQ-4 — No `created_at` on `Dataset`.**

Confirmed from `db/models.py`: the `Dataset` class has no `created_at` column. The task description listed it as a field to include ("plus ... `created_at`"), but inspection of the actual ORM model shows it does not exist. `DatasetDetailResponse` does NOT include `created_at`. If a display timestamp is needed, `materialized_at` (set on status='done') serves as the closest equivalent. This is explicitly noted in the schema comment.

**OQ-5 — `dataset_card_md` inclusion.**

The `DatasetListItem` docstring (F-045) explicitly states it omits `dataset_card_md` as a "detail-level field deferred to F-046". Therefore `DatasetDetailResponse` MUST include `dataset_card_md: str | None`. Even though it is `None` for all current rows (F-044 IO manager writes it to MinIO as a file, not to the Postgres column in MVP), the column exists on the model and is part of the detail contract.

---

## 11. Hard Invariants Check

| # | Invariant (CLAUDE.md §) | Status | How handled |
|---|---|---|---|
| 1 | **Lineage is mandatory** — any Commit MUST record `parents[]` + processor identity + config hash + input refs | **N/A** | This sprint creates no Commit objects in the data lineage sense. `GET /api/datasets/{id}` is a read-only endpoint; no new `dataset` or `run` rows are written. |
| 2 | **Storage separation + CAS** — metadata in Postgres; content in MinIO/S3 by `sha256(content)`; no blob bytes in Postgres | **✓** | `DatasetDetailResponse` returns `hf_repo_uri` (the S3 URI pointer), never the raw Parquet bytes. `recipe_snapshot` and `stats` are metadata (JSON), not content blobs. All consistent with existing model. |
| 3 | **Schema frozen post-publish** — once Silver/Gold repo publishes a commit, its schema MUST NOT be edited in place | **N/A** | No schema mutations. Read-only endpoint. |
| 4 | **LLM calls go through the gateway** | **N/A** | No LLM calls in this endpoint. |
| 5 | **Async SQLAlchemy from day one** — every DB session is async; no `session.query()`; no sync sessions | **✓** | Handler uses `AsyncSession = Depends(get_session)`, `await session.execute(select(...).where(...))`, and `scalar_one_or_none()` on the result proxy. No `session.query()`, no sync session anywhere. |
| 6 | **OpenAPI ↔ TS type sync** — any API schema change MUST be followed by `make codegen`; `packages/api-types/` diff committed in the SAME commit | **Required — hard requirement** | `DatasetDetailResponse` and the new `/api/datasets/{id}` path extend the OpenAPI surface. No `Makefile` exists at the repo root (confirmed during contract drafting — consistent with S045 precedent). Implementer MUST regenerate `packages/api-types/openapi.json` manually (e.g., via `uv run python -c "..."` as in step 6 of §5, or whatever method the S045 implementer used) and **commit the diff in the same commit** as the Python changes. CI will reject mismatches. This is a hard requirement, not advisory. |

---

## 12. Round-1 Review Addenda

Changes folded in response to `feedback.md` (reviewer Mode A, 2026-06-04, CHANGES_REQUESTED). No implementation logic, schema, owner-scope policy, or OpenAPI requirements were altered.

| Finding | How resolved |
|---|---|
| **MEDIUM-1** — Missing `test_get_dataset_invalid_id_returns_422` | Added as test #9 in §8. Test count updated from 8 → 9 everywhere it appeared in the contract (§2 scope line, §4 file table, §8 heading). The test asserts `status_code == 422` for a non-integer path segment with auth dependency overridden; no session mock required (FastAPI path-param validation fires before handler). |
| **NIT-1** — Wrong feature ID `F-067` in §3 Out of Scope | Replaced `F-067` with `F-070` ("Dataset detail page"). `F-067` is "Recipe editor auto-generated config form", which is unrelated to this sprint. |
| **NIT-2** — `depends_on` claim includes F-045 as a formal dependency | The "Depends on" header line in §1 now distinguishes the formal spec dependency (`F-044` only, per `feature_list.json`) from the practical predecessor (`F-045` — same router file; not in `depends_on`). |
| **NIT-3** — Handler docstring did not name `materialized_by` explicitly | Added a full docstring to the handler snippet in §5 step 3, naming `materialized_by` as the owner FK and stating the no-enumeration-leak policy explicitly (mirrors the comment on `get_recipe()` in `recipes.py` line 131). Future maintainers are warned not to substitute `owner_id`, which is the column name on `Recipe`, not `Dataset`. |
