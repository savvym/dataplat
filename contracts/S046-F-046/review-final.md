# Mode B Review — S046-F-046 (`GET /api/datasets/{id}`)

**Reviewer**: reviewer (Mode B)  
**Date**: 2026-06-04  
**Commit reviewed**: `41278dd` — `feat(F-046): GET /api/datasets/{id} dataset detail endpoint`  
**Verdict**: **APPROVED**

---

## Evidence read

| Artifact | Status |
|---|---|
| `git show 41278dd --stat` | Read — 5 files changed, +684 lines |
| `git show 41278dd` (full diff) | Read in full |
| `contracts/S046-F-046/agreed.md` (rev 2) | Read §1–§12 in full |
| `contracts/S046-F-046/feedback.md` (M1, NIT-1 through NIT-3) | Read — all findings tracked |
| `apps/api/dataplat_api/schemas/datasets.py` | Read — `DatasetDetailResponse` fields verified |
| `apps/api/dataplat_api/routers/datasets.py` | Read — handler + route order verified |
| `apps/api/tests/test_datasets_get.py` | Read — all 9 tests verified |
| `packages/api-types/openapi.json` (diff + component schema) | Read — path + schema presence verified |
| `apps/api/dataplat_api/db/models.py` Dataset class (lines 249–280) | Read — ORM baseline confirmed |
| `CLAUDE.md` | Invariants #1–#6 applied |

---

## Blocker checks

### B1 — Owner-scope filter present in query

**PASS.**

`routers/datasets.py` lines 113–116 (diff +62–+65):

```python
result = await session.execute(
    select(Dataset)
    .where(Dataset.id == id)
    .where(Dataset.materialized_by == current_user.id)
)
```

Both filters are in a **single** `SELECT` — not a two-step load-then-check. `materialized_by` is the correct owner FK (confirmed against `models.py` line 272: `materialized_by: Mapped[Optional[int]] = mapped_column(sa.BigInteger, sa.ForeignKey("users.id"), nullable=True)`). Wrong-owner and not-found collapse to the same 404, no enumeration leak. Identical pattern to `get_recipe()` in `recipes.py`.

### B2 — `DatasetDetailResponse` fields match §6 and ORM exactly (13 fields, types, nullability)

**PASS.**

Cross-referenced `schemas/datasets.py` (lines 71–109) against `models.py` (lines 259–279):

| Field | ORM type | ORM nullable | Schema type | Match |
|---|---|---|---|---|
| `id` | `Mapped[int]` BigInteger PK | NOT NULL | `int` | ✓ |
| `recipe_id` | `Mapped[Optional[int]]` BigInteger FK | nullable | `int \| None` | ✓ |
| `recipe_snapshot` | `Mapped[dict]` JSONB | **NOT NULL** | `dict` (not `dict\|None`) | ✓ |
| `version_tag` | `Mapped[str]` Text | NOT NULL | `str` | ✓ |
| `hf_repo_uri` | `Mapped[str]` Text | NOT NULL | `str` | ✓ |
| `dataset_card_md` | `Mapped[Optional[str]]` Text | nullable | `str \| None` | ✓ |
| `sample_count` | `Mapped[Optional[int]]` BigInteger | nullable | `int \| None` | ✓ |
| `size_bytes` | `Mapped[Optional[int]]` BigInteger | nullable | `int \| None` | ✓ |
| `stats` | `Mapped[Optional[dict]]` JSONB | nullable | `dict \| None` | ✓ |
| `status` | `Mapped[str]` Text | NOT NULL | `str` | ✓ |
| `materialized_by` | `Mapped[Optional[int]]` BigInteger FK | nullable | `int \| None` | ✓ |
| `materialized_at` | `Mapped[Optional[sa.DateTime]]` DateTime | nullable | `datetime \| None` | ✓ |
| `dagster_run_id` | `Mapped[Optional[str]]` Text | nullable | `str \| None` | ✓ |

**Field count: 13** — exactly matching the ORM. No `created_at` invented (OQ-4 correctly resolved). `recipe_snapshot` is `dict` non-nullable (matches `nullable=False` in ORM). `stats` is `dict | None` (matches `nullable=True`). `model_config = ConfigDict(from_attributes=True)` is present (line 86) — required for `model_validate(row)` on an ORM instance.

### B3 — `openapi.json` regenerated in the **same commit** (Invariant #6)

**PASS.**

`packages/api-types/openapi.json` is among the 5 files in `41278dd` (confirmed in `git show --stat`). The diff adds:
- Path `/api/datasets/{id}` (GET) with `operationId: get_dataset_api_datasets__id__get`, `security: [{"OAuth2PasswordBearer": []}]`, `int` path parameter, 200 → `$ref: DatasetDetailResponse`, 422 → `$ref: HTTPValidationError`.
- Component schema `DatasetDetailResponse` with all 13 properties and the correct `required` array listing all 13 field names.

Nullable fields (`recipe_id`, `sample_count`, `size_bytes`, `stats`, `dataset_card_md`, `materialized_by`, `materialized_at`, `dagster_run_id`) are correctly expressed as `anyOf: [{type: <T>}, {type: null}]` — the OpenAPI 3.1 pattern for nullable. Non-nullable fields (`id`, `version_tag`, `hf_repo_uri`, `recipe_snapshot`, `status`) are simple scalar/object types. All 13 appear in the `required` array despite nullable typing, which is correct OpenAPI 3.1 behaviour (Pydantic emits all fields as required with `anyOf null` for optionals). Invariant #6 is satisfied.

### B4 — `test_get_dataset_invalid_id_returns_422` present and correctly asserts 422 (feedback.md M1)

**PASS.**

Test #9 (`test_get_dataset_invalid_id_returns_422`, lines 395–412) is present. It:
- Overrides `get_current_user` (preventing 401 from masking the 422).
- Issues `client.get("/api/datasets/not-a-number")`.
- Asserts `response.status_code == 422`.
- No session mock call required — FastAPI path-param validation fires before dependency injection; comment explains this correctly.

This is identical in structure to the analogous test in `test_recipes_get.py` that M1 cited as the missing precedent.

### B5 — SQL-structural owner-scope check uses `literal_binds` and asserts `materialized_by` + user id (F-045 M1 lynchpin pattern)

**PASS.**

Test #7 (`test_get_dataset_materialized_by_in_query`, lines 308–352):
- Uses a capturing session that collects the session mock into `captured_session`.
- Asserts `session_mock.execute.call_count == 1` (single query, not two-step).
- Extracts `stmt = session_mock.execute.call_args_list[0].args[0]`.
- Compiles with `str(stmt.compile(compile_kwargs={"literal_binds": True}))`.
- Asserts `"materialized_by" in compiled` — the column name is in the SQL.
- Asserts `str(_MOCK_USER.id) in compiled` — the literal value `9` appears in the compiled SQL (not as a bound `?`/`:param` placeholder).

This is the exact pattern established as the lynchpin in F-045's M1 review. Both the column name and the literal bound value are checked; neither `str(query)` without `literal_binds` nor a regex-free string search is used.

---

## All feedback.md findings checked against the diff

| Finding | Required change | Present in commit? |
|---|---|---|
| **M1** — Missing `test_get_dataset_invalid_id_returns_422` | Add as test #9 with auth override + 422 assertion | ✓ (lines 395–412) |
| **NIT-1** — `F-067` → `F-070` in §3 Out of Scope | Fix feature ID | ✓ (agreed.md §3 has F-070; contract only, no code change required) |
| **NIT-2** — F-045 not a formal `depends_on` | Clarify as practical predecessor | ✓ (agreed.md §1 has the clarified "Depends on" line; contract only) |
| **NIT-3** — Docstring must name `materialized_by` explicitly | Add docstring to handler snippet | ✓ (router docstring lines 99–112 names `materialized_by`, explains no `owner_id`, includes MAINTENANCE NOTE) |

All four findings are addressed. NIT-1 and NIT-2 are contract-doc-only corrections with no code impact. NIT-3 and M1 are both reflected in the implementation.

---

## Additional checks (agreed.md §1–§12 completeness)

**Handler return path (§5 step 3)**: `DatasetDetailResponse.model_validate(row)` — correct; `from_attributes=True` is set. No `return DatasetDetailResponse(**row.__dict__)` anti-pattern.

**404 detail string (§7 V2)**: `detail="Dataset not found"` — matches contract and test 2 assertion `{"detail": "Dataset not found"}`. ✓

**Route ordering (§5 step 4 / OQ-3 / agreed.md §4)**: In `routers/datasets.py` the declaration order is `GET "" (list_datasets, line 55) → GET /{id} (get_dataset, line 93) → POST /{recipe_id}/materialize (materialize_dataset, line 127)`. Read-before-write, fixed-before-parameterized, consistent with `recipes.py`. ✓

**Import (§5 step 3)**: `DatasetDetailResponse` is added to the import block from `dataplat_api.schemas.datasets` (diff lines +34; router line 46). Alphabetical placement within the import block. ✓

**`async` / `await` / no `session.query()` (Invariant #5)**: Handler signature `async def get_dataset(...)`, body `await session.execute(...)`, result is synchronous `scalar_one_or_none()` on the result proxy. No `session.query()`. ✓

**No LLM calls, no storage writes, no lineage events (Invariants #1, #2, #3, #4)**: Read-only endpoint. `DatasetDetailResponse` returns `hf_repo_uri` (the S3 URI pointer), not raw bytes. ✓

**`claude-progress.txt` updated**: 8 new lines appended (diff shows `+11` lines in `claude-progress.txt`), covering sprint start through implementer build entry. The sprint close entry will be appended after this review, consistent with the workflow. ✓

**Module docstring updated**: `routers/datasets.py` module docstring updated to `S042-F-042 + S045-F-045 + S046-F-046` and the `GET /api/datasets/{id}` route listed. `schemas/datasets.py` docstring updated similarly. ✓

**Tests — all 9 present and correctly named**:

| # | Test name | Present | Key assertion |
|---|---|---|---|
| 1 | `test_get_dataset_200_all_fields` | ✓ | 200, all 13 keys, spot-check values |
| 2 | `test_get_dataset_not_found_returns_404` | ✓ | 404, `{"detail": "Dataset not found"}` |
| 3 | `test_get_dataset_wrong_owner_returns_404` | ✓ | 404, same detail, no-enumeration-leak |
| 4 | `test_get_dataset_no_token_returns_401` | ✓ | 401, `WWW-Authenticate: Bearer` |
| 5 | `test_get_dataset_recipe_snapshot_is_dict` | ✓ | `isinstance(body["recipe_snapshot"], dict)` |
| 6 | `test_get_dataset_stats_nullable` | ✓ | `body["stats"] is None` for pending row |
| 7 | `test_get_dataset_materialized_by_in_query` | ✓ | `literal_binds` asserts column + user id |
| 8 | `test_get_dataset_no_extra_fields_leaked` | ✓ | `set(keys) == _EXPECTED_KEYS` (exact 13) |
| 9 | `test_get_dataset_invalid_id_returns_422` | ✓ | `status_code == 422` for non-int path seg |

**`_EXPECTED_KEYS` set (test module line 69–83)**: 13 keys — `id, recipe_id, version_tag, hf_repo_uri, recipe_snapshot, sample_count, size_bytes, stats, dataset_card_md, status, materialized_by, materialized_at, dagster_run_id`. Matches the 13 fields in `DatasetDetailResponse` exactly. ✓

**`MagicMock(spec=Dataset)` factory pattern (NIT-2 precedent)**: `_make_dataset_detail()` uses `MagicMock(spec=Dataset)`, all 13 attributes populated, consistent with `_make_recipe_detail()` in `test_recipes_get.py`. ✓

**Auth override cleanup**: All tests that override dependencies use `try/finally` + `.pop()` — no leaked overrides between tests. ✓

**Pyright host warnings**: Not observed in the diff. Per S037–S045 precedent, host-side pyright warnings on `fastapi`/`dataplat_api`/`AsyncSession.add` are spurious (venv lives in the container). No `uv run mypy` investigation needed — the code structure is identical to the approved S045 pattern.

---

## Findings

No blockers. No actionable changes required.

**(NIT-OBS-1)** — The `openapi.json` `required` array for `DatasetDetailResponse` includes all 13 fields, including nullable ones like `recipe_id`, `stats`, `materialized_by` etc. This is standard Pydantic/FastAPI OpenAPI 3.1 behaviour: nullable fields are `required` (must be present in the JSON body) but their type is `anyOf [{type: T}, {type: null}]`. This is correct; no change needed. Purely observational note for future maintainers.

---

APPROVED

1. All five blocker criteria pass: owner-scope filter present with `materialized_by`; all 13 `DatasetDetailResponse` fields match ORM exactly (types + nullability); `openapi.json` regenerated in `41278dd` same commit (invariant #6); `test_get_dataset_invalid_id_returns_422` present and asserts 422 (M1); SQL-structural test uses `literal_binds` and asserts both `materialized_by` column name and user-id literal (lynchpin pattern).
2. All four feedback.md findings (M1, NIT-1, NIT-2, NIT-3) are reflected in the commit.
3. No scope violations, no sync sessions, no LLM calls, no storage writes.
