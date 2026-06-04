# Sprint S045-F-045 — Review Final (Mode B)

**Reviewer**: reviewer (Mode B — post-implementation diff)  
**Commit reviewed**: `539028b`  
**Date**: 2026-06-04  
**Contract**: `contracts/S045-F-045/agreed.md` (revision 2)

---

## 1. Files changed vs. agreed.md §3

| File | Expected | Present in diff | Match |
|---|---|---|---|
| `apps/api/dataplat_api/schemas/datasets.py` | edit — add `DatasetListItem` + `DatasetListResponse` | ✓ | ✓ |
| `apps/api/dataplat_api/routers/datasets.py` | edit — add `GET ""` route before `POST /{recipe_id}/materialize` | ✓ | ✓ |
| `apps/api/tests/test_datasets_list.py` | create — 9 unit tests | ✓ | ✓ |
| `packages/api-types/openapi.json` | generated — same commit | ✓ | ✓ |

No unexpected files were modified.

---

## 2. Schema correctness (`schemas/datasets.py`)

- `DatasetListItem` — 7 fields exactly as contracted: `id: int`, `recipe_id: int | None`, `version_tag: str`, `status: str`, `sample_count: int | None`, `size_bytes: int | None`, `materialized_at: datetime | None`. ✓  
- `model_config = ConfigDict(from_attributes=True)` present. ✓  
- `DatasetListResponse` — `items: list[DatasetListItem]`, `total: int`, no `from_attributes` (not needed). ✓  
- `MaterializeResponse` unchanged. ✓  

---

## 3. Router correctness (`routers/datasets.py`)

- `@router.get("", response_model=DatasetListResponse)` registered **before** `@router.post("/{recipe_id}/materialize", ...)`. ✓ (no route-shadowing risk).  
- Auth gate: `Depends(get_current_user)` present; same pattern as all other protected endpoints. 401 is automatically raised for missing/invalid token. ✓  
- **Query 1 (row list)**: `select(Dataset).where(Dataset.materialized_by == current_user.id).order_by(Dataset.materialized_at.desc().nulls_last(), Dataset.id.desc())` — owner filter present, ordering matches contract (`materialized_at DESC NULLS LAST, id DESC`). ✓  
- **Query 2 (count)**: `select(func.count()).select_from(Dataset).where(Dataset.materialized_by == current_user.id)` — owner filter present on COUNT query. M1 requirement met. ✓  
- `result.scalars().all()` — correct for a `select(Dataset)` (entity load). ✓  
- `count_result.scalar_one()` — correct; raises if no row returned, but `COUNT(*)` always returns exactly one row. ✓  
- `DatasetListItem.model_validate(row)` — correct use of `from_attributes=True` Pydantic API. ✓  
- Return type annotation `-> DatasetListResponse` matches `response_model`. ✓  

**No correctness issues found.**

---

## 4. Hard invariants (CLAUDE.md)

| # | Invariant | Verdict |
|---|---|---|
| 1 | Lineage mandatory | N/A — pure SELECT, no Commit record created. ✓ |
| 2 | Storage separation + CAS | N/A — endpoint reads only Postgres `dataset` rows; no MinIO interaction. ✓ |
| 3 | Schema frozen post-publish | N/A — no schema mutations. ✓ |
| 4 | LLM calls through gateway | N/A — no LLM calls. ✓ |
| 5 | Async SQLAlchemy | `async def list_datasets`, `AsyncSession`, `await session.execute(...)`, no `session.query()`. ✓ |
| 6 | OpenAPI ↔ TS type sync | `packages/api-types/openapi.json` diff present in **same commit** `539028b`. `DatasetListItem`, `DatasetListResponse`, and `GET /api/datasets` (operationId `list_datasets_api_datasets_get`) all appear. M2 requirement met. ✓ |

---

## 5. M1 check — test 6 asserts both `call_args_list[0]` AND `call_args_list[1]`

`test_list_datasets_materialized_by_in_query` (lines 489–542 in `test_datasets_list.py`):

- Captures `session.execute.call_args_list[0].args[0]` (row-list query), compiles with `literal_binds=True`, asserts `"materialized_by"` and `str(_MOCK_USER.id)` in compiled SQL. ✓  
- Captures `session.execute.call_args_list[1].args[0]` (COUNT query), compiles with `literal_binds=True`, asserts `"materialized_by"` and `str(_MOCK_USER.id)` in compiled SQL. ✓  
- `session.execute.call_count == 2` asserted explicitly (line 522). ✓  

M1 fully satisfied.

---

## 6. M2 check — `packages/api-types/openapi.json` in same commit

Confirmed from `git show 539028b`: the diff includes `packages/api-types/openapi.json` alongside the Python source changes — single commit, no separate codegen commit. M2 fully satisfied.

OpenAPI diff adds:
- `/api/datasets` GET path entry with `$ref: DatasetListResponse`, security `OAuth2PasswordBearer`. ✓  
- `DatasetListItem` component schema — 7 properties, `required` list matches all 7 (OpenAPI marks nullable-but-required fields as `required` + `anyOf [type, null]`, which is correct JSON Schema). ✓  
- `DatasetListResponse` component schema — `items` array + `total` integer, both required. ✓  

---

## 7. Test suite completeness (9 of 9)

| # | Test name | Agreed §5 requirement | Present | Passes contract |
|---|---|---|---|---|
| 1 | `test_list_datasets_returns_200_with_items_and_total` | 200, total==2, len==2 | ✓ | ✓ |
| 2 | `test_list_datasets_items_have_required_fields` | all 7 keys present, type checks | ✓ | ✓ |
| 3 | `test_list_datasets_no_token_returns_401` | 401 + `WWW-Authenticate: Bearer` | ✓ | ✓ |
| 4 | `test_list_datasets_empty_returns_empty_list` | `{"items": [], "total": 0}` | ✓ | ✓ |
| 5 | `test_list_datasets_only_own_datasets` | per-user isolation via separate session mocks | ✓ | ✓ |
| 6 | `test_list_datasets_materialized_by_in_query` | SQL-structural; both call_args_list[0] and [1] | ✓ | ✓ |
| 7 | `test_list_datasets_pending_row_has_null_fields` | pending row → null fields | ✓ | ✓ |
| 8 | `test_list_datasets_done_row_fields_all_present` | F-045 verification[0] | ✓ | ✓ |
| 9 | `test_list_datasets_extra_fields_not_in_items` | schema boundary guard (6 excluded fields) | ✓ | ✓ |

All 9 tests implemented. `_make_dataset()` uses `MagicMock(spec=Dataset)` with all 13 attributes populated. ✓

---

## 8. Calibrated correctness scan (diff-only findings)

The following potential issues were examined; none rise to HIGH or MEDIUM:

- **Filter direction**: `Dataset.materialized_by == current_user.id` — correct attribute and correct user field. No cross-user leakage. ✓  
- **Route ordering**: `GET ""` appears before `POST "/{recipe_id}/materialize"` in source. FastAPI router registration order is preserved; no shadowing. ✓  
- **Response model vs. return type**: `response_model=DatasetListResponse` and `-> DatasetListResponse`; `DatasetListResponse` is not a subclass of `JSONResponse` so FastAPI will serialize through the model. Consistent. ✓  
- **`total` consistency**: both queries share identical `.where(Dataset.materialized_by == current_user.id)` clauses, so `total` correctly reflects the owner-scoped count. No off-by-one or global-count risk. ✓  
- **Nullable `recipe_id` in OpenAPI `required` list**: JSON Schema correctly marks `recipe_id` as `required` (must be present in the JSON object) with `anyOf [integer, null]` (value may be null). This is the correct Pydantic v2 / FastAPI representation for `int | None` with no default. ✓  
- **`DatasetListResponse` missing `from_attributes`**: intentional — it is constructed directly by the handler, not validated from an ORM row. ✓  
- **No 401 in OpenAPI spec for the GET route**: consistent with existing endpoints in this codebase (precedent: recipes list). OAuth2PasswordBearer security scheme signals the auth requirement. Not a blocking issue. ✓  

---

## 9. Summary

All items in `agreed.md` (revision 2) are fully implemented:
- Endpoint exists, owner-scoped, ordered, returns correct envelope.
- Both invariant-critical requirements (M1: COUNT query also filtered; M2: codegen in same commit) verified.
- 9/9 tests present and structurally correct.
- 6 hard invariants satisfied.
- No HIGH or MEDIUM correctness issues found in the diff.

APPROVED
