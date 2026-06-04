# Sprint S048-F-048 — Proposed Contract

**Feature**: F-048 — Run status endpoint: `GET /api/runs/{id}` returns the run record including `dagster_run_id`, `kind`, `status`, `started_at`, `ended_at`, and `config`
**Depends on**: F-018 (`passes: true`)
**Sprint directory**: `contracts/S048-F-048/`
**Author**: implementer (inline)
**Date**: 2026-06-04
**Revision**: 2

---

## §1 Goal

Add a `GET /api/runs/{id}` handler to the existing `runs_router` in `apps/api/dataplat_api/routers/runs.py` that returns the full Postgres `run` row for a given **integer** primary key — owner-scoped to the authenticated caller.

**Critically**: the existing `GET /{run_id}` route in `runs.py` (shipped by S005-F-005, currently active) is a *Dagster-proxy* endpoint: it accepts a **string** Dagster run UUID, calls `gateway.get_run_status()`, and returns a 2-field `RunStatusResponse`. F-048 is a **different endpoint** with the same path pattern but different semantics — it accepts an **integer** Postgres `run.id`, reads from the Postgres `run` table, and returns the full persisted run record. These two routes must **not** coexist on the same path; the F-048 handler replaces the Dagster-proxy behaviour at `GET /{id}` for the integer-PK use case.

**Resolution of this naming collision**: the existing `GET /{run_id}` handler parameter is typed as `str`. If we add a new `GET /{id}` handler typed as `int`, FastAPI route matching fires in declaration order and the first match wins — a numeric segment like `42` would match the `str` route first, preventing the int route from ever firing. The resolution is:

- **Rename** the existing Dagster-proxy `GET /{run_id}: str` route to `GET /dagster/{dagster_run_id}` (a new sub-path), updating `RunStatusResponse` wiring accordingly. This keeps the Dagster-proxy functionality accessible at `GET /api/runs/dagster/{dagster_run_id}`.
- **Add** the new `GET /{id}: int` route at `GET /api/runs/{id}` — the canonical F-048 path.

> **Open Question OQ-1** (for reviewer resolution): The spec says `GET /api/runs/{id}` but the existing Dagster-proxy currently also occupies a path shaped like `GET /api/runs/{run_id}`. The renaming approach above is the cleanest solution, but it changes an existing route's public path. A simpler alternative is to keep both routes and rely on FastAPI's per-route type coercion (a numeric string hits the `int` typed route first if declared before the `str` route). See §10 for full analysis. **Implementer recommendation: move the Dagster-proxy to `GET /dagster/{dagster_run_id}` — it is not part of F-048's verification criteria, and the route is otherwise unused by the integration tests in this sprint.**

### Verification criteria (verbatim from spec)

| Criterion | ID |
|---|---|
| `GET /api/runs/{id}` returns 200 with all expected fields | V1 |
| `GET /api/runs/99999` returns 404 | V2 |

---

## §2 Owner-Scope Policy

**The `Run` ORM model HAS an owner FK: `triggered_by` (BigInteger FK → `users.id`, nullable).**

Confirmed from `apps/api/dataplat_api/db/models.py` lines 322–325:
```python
triggered_by: Mapped[Optional[int]] = mapped_column(
    sa.BigInteger, sa.ForeignKey("users.id"), nullable=True
)
```

In `trigger_extract_run` (the F-018 handler), this is set as `triggered_by=current_user.id` (line 203 of `runs.py`). Every run row created through the POST `/api/runs` surface is owned by the triggering user. The `triggered_by` column is nullable in the schema, but always populated for application-created runs (a `None` value would only arise from direct SQL inserts).

**Owner-scope rule**: `GET /api/runs/{id}` MUST combine `Run.id == id` AND `Run.triggered_by == current_user.id` in a **single SELECT**. A run that exists but is owned by a different user returns the same 404 as a non-existent id — no information leak. This follows the identical pattern established by:
- `GET /api/datasets/{id}` (F-046) — `Dataset.materialized_by == current_user.id`
- `GET /api/recipes/{id}` (F-039) — `Recipe.owner_id == current_user.id`

**The filter column is `triggered_by`**, not `owner_id`. The `Run` ORM model has no `owner_id` column. Using `owner_id` would cause `AttributeError` at runtime (same class of bug documented in datasets.py MAINTENANCE NOTE).

---

## §3 Response Schema — `RunDetailResponse`

Sourced directly from `apps/api/dataplat_api/db/models.py` `Run` class (lines 285–327). The `Run` model has **14 Mapped columns**. All 14 are included in the response; no column is omitted and no synthetic field is invented.

### Full column inventory (verbatim from ORM)

| Column | ORM type | Nullable | Notes |
|---|---|---|---|
| `id` | `Mapped[int]` (BigInteger PK) | No | Postgres identity |
| `dagster_run_id` | `Mapped[str]` (Text UNIQUE NOT NULL) | No | Dagster backfill/run UUID |
| `kind` | `Mapped[str]` (Text NOT NULL) | No | e.g. `"extract"`, `"chunk"`, `"attr_quality"`, `"attr_lang"`, `"attr_minhash"` |
| `asset_keys` | `Mapped[List[str]]` (ARRAY(Text) NOT NULL) | No | e.g. `["extract_mineru"]` |
| `partition_keys` | `Mapped[Optional[List[str]]]` (ARRAY(Text)) | Yes | e.g. `["src_1", "src_2"]` |
| `source_collection_id` | `Mapped[Optional[int]]` (BigInteger FK) | Yes | FK → source_collection.id |
| `dataset_id` | `Mapped[Optional[int]]` (BigInteger FK) | Yes | FK → dataset.id |
| `recipe_id` | `Mapped[Optional[int]]` (BigInteger FK) | Yes | FK → recipe.id |
| `config` | `Mapped[Optional[dict]]` (JSONB) | Yes | Run configuration dict; always `None` in current F-018 trigger path |
| `status` | `Mapped[str]` (Text NOT NULL) | No | e.g. `"pending"`, `"running"`, `"success"`, `"failure"` |
| `started_at` | `Mapped[Optional[sa.DateTime]]` (DateTime tz) | Yes | Null until run starts |
| `ended_at` | `Mapped[Optional[sa.DateTime]]` (DateTime tz) | Yes | Null until run completes |
| `triggered_by` | `Mapped[Optional[int]]` (BigInteger FK → users.id) | Yes | Owner FK |
| `trigger_context` | `Mapped[Optional[dict]]` (JSONB) | Yes | Always `None` in current code |

**Total: 14 Mapped columns** — not 13. The spec lists 6 named fields (`dagster_run_id`, `kind`, `status`, `started_at`, `ended_at`, `config`) as the minimum required. All 14 columns are returned to future-proof the schema and remain consistent with the F-046 / F-047 pattern of exposing the full ORM row.

### Pydantic schema skeleton

```python
class RunDetailResponse(BaseModel):
    """Full run record for GET /api/runs/{id} (F-048).

    Exposes all 14 ORM-mapped columns of the ``run`` table.
    ``dagster_run_id`` is the Dagster backfill UUID (TEXT UNIQUE NOT NULL).
    ``kind`` is the run type string set by the trigger handler.
    ``config`` is a nullable JSONB dict; currently None for all trigger paths.
    ``started_at`` / ``ended_at`` are nullable datetimes (None until state
    transitions fire in Dagster sensor callbacks, if any).
    ``triggered_by`` is the owner FK; doubles as the owner-scope filter.
    ``trigger_context`` is nullable JSONB; currently None for all trigger paths.
    ``asset_keys`` / ``partition_keys`` are Postgres ARRAY(Text) columns.
    """

    model_config = ConfigDict(from_attributes=True)

    # ── Identity ──────────────────────────────────────────────────────────────
    id: int                                  # Run.id              BigInteger PK
    dagster_run_id: str                      # Run.dagster_run_id  Text NOT NULL UNIQUE

    # ── Run classification ────────────────────────────────────────────────────
    kind: str                                # Run.kind            Text NOT NULL
    asset_keys: list[str]                    # Run.asset_keys      ARRAY(Text) NOT NULL
    partition_keys: list[str] | None         # Run.partition_keys  ARRAY(Text) nullable

    # ── FK context ────────────────────────────────────────────────────────────
    source_collection_id: int | None         # Run.source_collection_id FK nullable
    dataset_id: int | None                   # Run.dataset_id           FK nullable
    recipe_id: int | None                    # Run.recipe_id            FK nullable

    # ── Configuration ─────────────────────────────────────────────────────────
    config: dict | None                      # Run.config          JSONB nullable

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    status: str                              # Run.status          Text NOT NULL
    started_at: datetime | None             # Run.started_at      DateTime tz nullable
    ended_at: datetime | None               # Run.ended_at        DateTime tz nullable
    triggered_by: int | None                # Run.triggered_by    FK → users.id nullable
    trigger_context: dict | None            # Run.trigger_context JSONB nullable
```

**`config` type note**: ORM column is `Mapped[Optional[dict]]` (JSONB nullable). Surfaced as `dict | None`. The current F-018 handler always passes `config=None`; future trigger paths may populate it. Pass through as-is — no reshaping.

**`asset_keys` / `partition_keys` type note**: Postgres ARRAY(Text). SQLAlchemy returns these as Python `list[str]`. Pydantic v2 serialises them as JSON arrays. `partition_keys` is nullable (server_default `'{}'` but nullable=True); typed as `list[str] | None`.

---

## §4 Files to Add / Modify

| File | Status | Reason |
|---|---|---|
| `apps/api/dataplat_api/schemas/runs.py` | **edit** | (1) Add `RunDetailResponse` (14 fields) after `RunCreateResponse`. Keep `LaunchHelloWorldResponse`, `RunStatusResponse`, `RunCreate`, `RunCreateResponse` unchanged. (2) Update module-level docstring (line 5): change `GET  /api/runs/{run_id}           → RunStatusResponse` to `GET  /api/runs/dagster/{dagster_run_id} → RunStatusResponse`. (3) Update `RunStatusResponse` class docstring (line 34): change `"""Response body for GET /api/runs/{run_id} (HTTP 200 OK).` to `"""Response body for GET /api/runs/dagster/{dagster_run_id} (HTTP 200 OK).` |
| `apps/api/dataplat_api/routers/runs.py` | **edit** | (1) Rename existing `GET /{run_id}` Dagster-proxy route to `GET /dagster/{dagster_run_id}`. (2) Add `GET /{id}` Postgres-row route `get_run_detail()`. (3) Add `RunDetailResponse` to the existing `from dataplat_api.schemas.runs import …` line. **`AsyncSession` (line 25) and `get_session` (line 35) are already imported — do not duplicate.** |
| `apps/api/tests/test_runs_hello_world.py` | **edit** | Update three `client.get(f"/api/runs/{fake_run_id}")` calls (lines 110, 132, 148) to `client.get(f"/api/runs/dagster/{fake_run_id}")` due to Dagster-proxy rename. |
| `verify/checks.sh` | **edit** | Update `GET /api/runs/${RUN_ID}` at line 455 to `GET /api/runs/dagster/${RUN_ID}` (runs-layer smoke test polls Dagster-proxy by UUID string, not Postgres int). |
| `apps/api/tests/test_runs_get.py` | **create** | New test module — 9 unit tests (see §8). |
| `packages/api-types/openapi.json` | **generated** | Regenerated after schema and router additions; committed in the **same** commit per hard invariant #6. |

No Alembic migration is required — all columns already exist on the `run` table (created in F-002 baseline migration).

**Codegen hard requirement (invariant #6):**
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
The diff MUST be staged and committed in the **same** commit as all Python source changes.

---

## §5 Implementation Steps

1. **Confirm Run ORM column inventory** (already done for this contract; see §3): 14 Mapped columns. Spec's 6 named fields are a strict subset; all 14 are exposed. No `created_at` column exists on `Run` — do not invent one.

2. **Edit `schemas/runs.py`**:
   - Append `RunDetailResponse` after the existing `RunCreateResponse` class. Import `datetime` from `datetime` and `ConfigDict` from `pydantic`. All 14 ORM columns represented per §3 skeleton.
   - Update module-level docstring (line 5): change `GET  /api/runs/{run_id}           → RunStatusResponse` to `GET  /api/runs/dagster/{dagster_run_id} → RunStatusResponse`.
   - Update `RunStatusResponse` class docstring (line 34): change opening line `"""Response body for GET /api/runs/{run_id} (HTTP 200 OK).` to `"""Response body for GET /api/runs/dagster/{dagster_run_id} (HTTP 200 OK).`

3. **Edit `routers/runs.py`**:
   - Rename `@runs_router.get("/{run_id}", ...)` to `@runs_router.get("/dagster/{dagster_run_id}", ...)` and update the function parameter name from `run_id: str` to `dagster_run_id: str`. Update the docstring to reflect the new path. No logic changes to the Dagster-proxy body.
   - Add `RunDetailResponse` to the existing `from dataplat_api.schemas.runs import …` line at the top of the file. **`AsyncSession` is already imported at line 25 and `get_session` at line 35 — do NOT add duplicate import lines.**
   - Register the new route **before** the Dagster-proxy in declaration order (to avoid any future ambiguity if both coexist on the router):

     ```python
     @runs_router.get(
         "/{id}",
         response_model=RunDetailResponse,
         summary="Get run record by Postgres id",
         description=(
             "Return the full Postgres run record for the authenticated owner. "
             "Owner-scoping: combines ``id == ?`` AND ``triggered_by == ?`` so "
             "that a non-existent id and an id owned by another user both return 404. "
             "Requires a valid Bearer JWT (F-008)."
         ),
     )
     async def get_run_detail(
         id: int,
         current_user: User = Depends(get_current_user),
         session: AsyncSession = Depends(get_session),
     ) -> RunDetailResponse:
     ```
     Handler body:
     - `select(Run).where(Run.id == id).where(Run.triggered_by == current_user.id)` — single query combining both filters.
     - `result.scalar_one_or_none()` — if `None`, raise `HTTPException(status_code=404, detail="Run not found")`.
     - Return `RunDetailResponse.model_validate(row)`.

4. **Route declaration order** within `runs_router` (after step 3):
   - `POST ""` (existing — trigger extract run, F-018)
   - `GET /{id}` (**new** — F-048 Postgres row)
   - `GET /dagster/{dagster_run_id}` (**renamed** — existing Dagster-proxy, F-005)

   This ordering is safe: `GET /{id}` and `GET /dagster/{dagster_run_id}` share the `GET` method but differ in path pattern (one-segment parameterised vs. two-segment fixed-prefix-plus-param). FastAPI resolves these independently.

5. **Write tests** in `apps/api/tests/test_runs_get.py` (see §8 for full list).

6. **Update `apps/api/tests/test_runs_hello_world.py`**: the three call sites at lines 110, 132, and 148 each call `client.get(f"/api/runs/{fake_run_id}")` where `fake_run_id` is a Dagster UUID string. Update all three to `client.get(f"/api/runs/dagster/{fake_run_id}")`. No logic changes — only the URL string.

7. **Update `verify/checks.sh`**: at line 455, the runs-layer smoke test polls `GET /api/runs/${RUN_ID}` where `RUN_ID` is a Dagster UUID extracted from `POST /api/admin/runs/hello-world`. Update to `GET /api/runs/dagster/${RUN_ID}`.

8. **Regenerate OpenAPI spec** using the snippet in §4. The diff must show:
   - New `/api/runs/{id}` path entry with `GET` operation.
   - Old `GET /api/runs/{run_id}` entry renamed to `GET /api/runs/dagster/{dagster_run_id}`.
   - New `RunDetailResponse` entry under `components/schemas`.
   - No removals of existing schemas.

9. **Commit** all six changed/created files in a single commit.

---

## §6 Route Collision Analysis

### Existing route (F-005 / S005)

```
GET /api/runs/{run_id}     run_id: str    → RunStatusResponse (Dagster proxy)
```

### Proposed F-048 route

```
GET /api/runs/{id}         id: int        → RunDetailResponse (Postgres row)
```

**Problem**: if both are declared on `runs_router`, FastAPI matches them in declaration order. A request to `GET /api/runs/42` will match whichever `GET /{...}` route is declared first, regardless of the parameter type annotation (FastAPI does not do type-based route dispatch for path parameters — type coercion happens inside the matched handler, not during route selection).

**Resolution (baked into §5)**: rename the Dagster-proxy path to `GET /dagster/{dagster_run_id}`. The two routes are then:
```
GET /api/runs/{id}                    → RunDetailResponse  (F-048)
GET /api/runs/dagster/{dagster_run_id} → RunStatusResponse (F-005, renamed)
```
These are distinct paths with no ambiguity. Existing tests in `test_runs_hello_world.py` that call `GET /api/runs/{run_id}` (the Dagster-proxy) will need their URLs updated to `GET /api/runs/dagster/{run_id}`.

**Impact on checks.sh runs layer**: the runs) layer in `verify/checks.sh` (line 455) currently polls `GET /api/runs/${RUN_ID}` where `RUN_ID` is a Dagster UUID string. After the rename, this check must be updated to `GET /api/runs/dagster/${RUN_ID}`. This is a **required change** — not optional.

---

## §7 Test Plan

File: `apps/api/tests/test_runs_get.py`

All tests follow the `test_datasets_get.py` pattern: `TestClient(app)`, `MagicMock(spec=Run)` row factory, `AsyncMock` session with `scalar_one_or_none()` on a synchronous `MagicMock` result proxy. The `conftest.py` autouse `_patch_engine_begin` and `_patch_httpx_no_ssl` fixtures apply automatically.

**Mock factory** `_make_run_detail()` populates all 14 ORM attributes (same discipline as `_make_dataset_detail()` in `test_datasets_get.py`).

**Session mock pattern** (single `execute()` call):
```python
result_mock = MagicMock()
result_mock.scalar_one_or_none.return_value = run_row_or_none
session = AsyncMock()
session.execute = AsyncMock(return_value=result_mock)
```

**Expected 14-key set** (constant `_EXPECTED_KEYS`):
```python
_EXPECTED_KEYS = {
    "id", "dagster_run_id", "kind", "asset_keys", "partition_keys",
    "source_collection_id", "dataset_id", "recipe_id",
    "config", "status", "started_at", "ended_at",
    "triggered_by", "trigger_context",
}
```

| # | Test name | What it checks | Maps to |
|---|---|---|---|
| 1 | `test_get_run_200_all_fields` | status='pending' row with all 14 fields populated → 200; assert all 14 keys in body; spot-check `dagster_run_id`, `kind`, `status`, `config`, `started_at` | **V1** |
| 2 | `test_get_run_not_found_returns_404` | Session returns `None` for id=99999 → 404 `{"detail": "Run not found"}` | **V2** |
| 3 | `test_get_run_wrong_owner_returns_404` | Session returns `None` (simulates run owned by user id=99, not mock user id=9) → same 404; no enumeration leak | V2 pattern, owner-scope |
| 4 | `test_get_run_no_token_returns_401` | No `Authorization` header; no dep override → real `oauth2_scheme` → 401 with `WWW-Authenticate: Bearer` | auth gate |
| 5 | `test_get_run_invalid_id_returns_422` | Non-integer path segment `/api/runs/not-a-number` → 422; auth dep overridden (so 401 doesn't fire first); path-param validation fires before handler | FastAPI path-param validation |
| 6 | `test_get_run_triggered_by_in_query` | SQL-structural: capture `session.execute.call_args_list[0].args[0]`; compile with `literal_binds=True`; assert `"triggered_by"` and mock user's id both appear in compiled SQL — mirrors `test_get_dataset_materialized_by_in_query` | owner-scope SQL guard |
| 7 | `test_get_run_no_extra_fields_leaked` | `set(response.json().keys()) == _EXPECTED_KEYS` — exact 14-key set; no extra fields; `trigger_context` IS present (null for pending run) | schema guard |
| 8 | `test_get_run_config_is_dict_or_null` | Row with `config={"batch_size": 100}` → 200, `response.json()["config"] == {"batch_size": 100}` (dict, not string); also test `config=None` → `response.json()["config"] is None` | `config` JSONB pass-through |
| 9 | `test_get_run_nullable_timestamps` | Row with `started_at=None, ended_at=None` (pending run) → 200; `body["started_at"] is None` and `body["ended_at"] is None` | nullable datetime fields |

**Test count: 9** — matching the F-046 / F-047 pattern. Tests 1 and 7 together cover V1; test 2 covers V2; test 3 adds the no-enumeration-leak; tests 8 and 9 are run-specific field guards.

---

## §8 Verification Commands (for verifier)

```bash
# Unit tests (backend layer — no compose stack needed)
cd apps/api && uv run pytest tests/test_runs_get.py -v

# Full backend layer
cd apps/api && uv run pytest -v

# All checks layers
bash verify/checks.sh all

# Spot-check V1 live (stack must be up, seed admin must exist)
TOKEN=$(curl -s -X POST http://localhost:18000/api/auth/token \
  -d "username=admin@example.com&password=testpassword123" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")

# First create a run via POST /api/runs (F-018) to get a real run_id
RUN_ID=$(curl -s -X POST http://localhost:18000/api/runs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"asset":"extract_mineru","source_ids":[<REAL_SOURCE_ID>]}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['run_id'])")

# V1: GET /api/runs/{id} → 200 with all expected fields
curl -s http://localhost:18000/api/runs/$RUN_ID \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# V2: GET /api/runs/99999 → 404
curl -s -o /dev/null -w "%{http_code}" \
  http://localhost:18000/api/runs/99999 \
  -H "Authorization: Bearer $TOKEN"
# Expected: 404

# Confirm Dagster-proxy still works at new path
DAGSTER_RUN_ID=<UUID from trigger>
curl -s http://localhost:18000/api/runs/dagster/$DAGSTER_RUN_ID \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

---

## §9 Open Questions

### OQ-1 — Route collision: rename Dagster-proxy vs. dual-route coexistence

**Implementer recommendation: rename Dagster-proxy to `GET /dagster/{dagster_run_id}`.**

Analysis of alternatives:

| Option | Approach | Risk |
|---|---|---|
| **A (recommended)** | Rename existing Dagster-proxy to `GET /dagster/{dagster_run_id}`; add F-048 at `GET /{id}: int` | Clean separation; existing test URLs need update; checks.sh runs-layer update required |
| B | Keep both `GET /{run_id}: str` and add `GET /{id}: int`; declare int route first | FastAPI does NOT dispatch based on type; first-declared `GET /{...}` catches all. Both routes would collide — this **does not work** |
| C | Single unified handler; inspect whether param is UUID (string) or integer; dispatch internally | Violates SRP; conflates two concerns; reviewer will reject |

Option A is the only clean solution. The sane default is to proceed with Option A. **If reviewer disagrees, explicit instruction is needed.**

**Impact on existing code**:
- `apps/api/tests/test_runs_hello_world.py`: 1 test (`test_get_run_status_success`) calls `GET /api/runs/{run_id}` — update URL to `GET /api/runs/dagster/{run_id}`.
- `verify/checks.sh` runs) layer (line 455): `GET /api/runs/${RUN_ID}` — update to `GET /api/runs/dagster/${RUN_ID}`.
- `runs.py` module docstring header comment (line 11): update `GET /{run_id}` to `GET /dagster/{dagster_run_id}`.
- `openapi.json` will gain the new path and lose the old one (rename).

### OQ-2 — `triggered_by=NULL` rows: what happens?

If a `Run` row has `triggered_by=NULL` (e.g. inserted by a Dagster sensor directly, or by a migration seed), then `GET /api/runs/{id}` with ANY authenticated user will return 404, because the query condition `triggered_by == current_user.id` will never match NULL (SQL `NULL != anything`). This is acceptable for MVP: all application-created rows have `triggered_by` set. If admin visibility of NULL-triggered-by rows is ever needed, a separate admin route can be added. **Resolution: accept. No special-casing needed.**

### OQ-3 — Should `triggered_by` be excluded from the response to prevent user id leakage?

`triggered_by` is an integer FK (user id). It is the same user id that appears in the JWT `sub` claim for the authenticated caller. Returning it is fine — the caller already knows their own id. **Resolution: include `triggered_by` in `RunDetailResponse`.**

---

## §10 Risks / Scope Notes

1. **Dagster-proxy rename is a breaking change** for any external caller that uses `GET /api/runs/{uuid-string}`. In MVP (no external consumers yet), this is low-risk. The rename is contained to `runs.py`, two test files, and `checks.sh`.

2. **No new Alembic migration** — all 14 columns exist since F-002. No DB state change.

3. **`asset_keys` and `partition_keys` are Postgres ARRAY(Text)**. Pydantic v2 with `from_attributes=True` will serialise these as JSON arrays (`list[str]`). Confirmed by F-018 implementation which inserts `asset_keys=["extract_mineru"]` — round-trips fine.

4. **`config` is currently always `None`** for F-018 trigger paths (line 204 of `runs.py`). The field is included for completeness and future use. Test 8 guards against double-serialisation by asserting a non-null config dict passes through as a dict, not a string.

5. **Scope boundary**: F-049 is "list all runs (paginated)". F-048 is only the single-record detail. Do NOT add pagination, filtering, or list semantics in this sprint.

---

## §11 Hard Invariants Audit

| # | Invariant (CLAUDE.md) | Status | One-line reason |
|---|---|---|---|
| 1 | **Lineage mandatory** — any Commit MUST record `parents[]` + processor identity + config hash + input refs | **N/A** | `GET /api/runs/{id}` is a read-only endpoint. No `Commit` objects are created. No lineage event fires. |
| 2 | **Storage separation + CAS** — metadata in Postgres; content in MinIO/S3 by `sha256(content)`; no blob bytes in Postgres | **✓ Respected** | The endpoint returns Postgres metadata fields only. No MinIO/S3 interaction. `config` and `trigger_context` are metadata JSONB, not content blobs. |
| 3 | **Schema frozen post-publish** — Silver/Gold schema changes require new commit | **N/A** | No schema mutations. Read-only endpoint. |
| 4 | **LLM calls go through the gateway** | **N/A** | No LLM calls. The renamed Dagster-proxy (`GET /dagster/{dagster_run_id}`) continues to call `gateway.get_run_status()` — unchanged, already compliant. |
| 5 | **Async SQLAlchemy from day one** — every DB session is async; no `session.query()`; no sync sessions | **✓ Required** | New handler uses `AsyncSession = Depends(get_session)`, `await session.execute(select(Run).where(...).where(...))`, `scalar_one_or_none()`. No `session.query()`. Same pattern as `get_dataset()` (F-046) and `get_recipe()` (F-039). |
| 6 | **OpenAPI ↔ TS type sync** — API schema change MUST be followed by `make codegen`; `packages/api-types/` diff in same commit | **Required — hard requirement** | `RunDetailResponse` (14 fields) and the new `/api/runs/{id}` path (plus renamed `/api/runs/dagster/{dagster_run_id}`) extend/modify the OpenAPI surface. No `Makefile` at repo root (confirmed S045/S046/S047 precedent). Implementer MUST regenerate `packages/api-types/openapi.json` manually (see §4 snippet) and commit the diff in the **same** commit as Python source changes. CI will reject mismatches. |

---

## §12 Out-of-Scope Deferrals

- **F-049 — `GET /api/runs` (list, paginated)**: not in this sprint. The docstring in `runs.py` already notes: "GET /api/runs (list, paginated): F-049 (requires business run table from F-018)". F-048 is the single-record detail only.
- **Run log proxy** (`GET /api/runs/{id}/logs`): deferred beyond F-049 per runs.py docstring.
- **WebSocket run-status events** (F-051): deferred.
- **Admin bypass of owner-scope filter**: MVP uses `triggered_by` scope only. An admin route that can see all runs regardless of owner is a post-MVP concern (§11.6).
- **Status enum validation**: `status` is stored as free-text in Postgres. F-048 returns it as-is. A typed `Literal["pending", "running", "success", "failure"]` enum on the response schema would be a future hardening step — not required by the spec verification criteria.
- **`trigger_context` shape validation**: returned as `dict | None` unchanged, no reshaping.
- **`asset_keys` / `partition_keys` shape validation**: returned as `list[str] | None` unchanged.
- **Filtering / querying by status, kind, or date range**: list-level concerns, F-049.

---

## §13 Definition of Done

A sprint is `done` iff **all** of the following hold:

- [ ] `contracts/S048-F-048/agreed.md` exists with every item addressed.
- [ ] `apps/api/dataplat_api/schemas/runs.py` contains `RunDetailResponse` with all 14 fields; module-level docstring and `RunStatusResponse` class docstring updated to reference `GET /api/runs/dagster/{dagster_run_id}`.
- [ ] `apps/api/dataplat_api/routers/runs.py` contains `GET /{id}` handler with owner-scope (`triggered_by == current_user.id`); Dagster-proxy renamed to `GET /dagster/{dagster_run_id}`.
- [ ] `apps/api/tests/test_runs_get.py` contains all 9 tests; all pass.
- [ ] `test_runs_hello_world.py` updated for the renamed Dagster-proxy URL.
- [ ] `verify/checks.sh` runs) layer updated for the renamed Dagster-proxy URL; `bash verify/checks.sh runs` exits 0.
- [ ] `packages/api-types/openapi.json` regenerated and committed in the **same** commit.
- [ ] `bash verify/checks.sh backend` exits 0 (pytest all-pass).
- [ ] `bash verify/checks.sh all` exits 0.
- [ ] `contracts/S048-F-048/review-final.md` ends with `APPROVED`.
- [ ] `spec/feature_list.json` F-048 `passes` flipped to `true`.
- [ ] `claude-progress.txt` closing entry appended.

---

## §14 Round-1 Addenda

*(Addresses reviewer Mode A findings from feedback.md, Rev 1 → Rev 2.)*

| Finding ID | Severity | RESOLVED: how |
|---|---|---|
| M1 | MEDIUM | Added `apps/api/tests/test_runs_hello_world.py` and `verify/checks.sh` to the §4 file table with the exact edits required (3 URL call-site updates in the test file; line-455 URL update in checks.sh). Added steps 6 and 7 in §5 implementation steps to cover both changes explicitly. |
| L1 | LOW | Added `schemas/runs.py` to the §4 file table with the exact docstring edits listed (module-level line 5 and `RunStatusResponse` class docstring line 34, both updated to reference `GET /api/runs/dagster/{dagster_run_id}`). Added corresponding checklist item to §13 DoD. |
| NIT-1 | NIT | §3 opening sentence now says "14 Mapped columns" from the start; the self-correcting "not 13" at the end of the table section retained for clarity but is no longer contradicted by the opening. |
| NIT-2 | NIT | §4 router entry and §5 step 3 now clarify: only `RunDetailResponse` is a new import; `AsyncSession` (line 25) and `get_session` (line 35) are already present in `runs.py` — implementer must NOT add duplicate import lines. |
