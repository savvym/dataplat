# Sprint S049-F-049 — Mode B Review (commit d81eb51)

**Reviewer**: reviewer (Mode B — post-implementation)  
**Date**: 2026-06-05  
**Commit**: `d81eb5185ce140a8c67713f224a4ff28a4b3915b`  
**Contract**: `contracts/S049-F-049/agreed.md` (revision 2, incorporating M1/M2/L1/NIT-1 addenda from §13)  
**Files reviewed**: 4 (`schemas/runs.py`, `routers/runs.py`, `tests/test_runs_list.py`, `packages/api-types/openapi.json`)  
**Test run**: 15 collected, **15 passed** (328 total suite — 0 regressions)

---

## Checklist walkthrough

### B1 — Owner-scope on BOTH page + COUNT queries

**PASS.**

`routers/runs.py` lines show the two-query pattern verbatim from agreed.md §5:

```python
stmt_page = (
    select(Run)
    .where(Run.triggered_by == current_user.id)   # ← owner-scope
    .order_by(Run.started_at.desc().nulls_last(), Run.id.desc())
)
if status is not None:
    stmt_page = stmt_page.where(Run.status == status)   # ← status on page query
...
stmt_count = select(func.count()).select_from(Run).where(
    Run.triggered_by == current_user.id             # ← owner-scope
)
if status is not None:
    stmt_count = stmt_count.where(Run.status == status)  # ← status on count query
```

Both `triggered_by` filter and optional `status` filter are applied identically to both queries. No asymmetry.

**Structural confirmation (T6, T7):** Both lynchpin tests compile actual SQLAlchemy statement objects with `literal_binds=True` and assert the relevant literal strings appear in both call indices. All four T7 parametrize variants pass. This is the strongest possible unit-level proof: the SQL itself is inspected, not just that the handler runs.

---

### B2 — Schema: RunListItem (10 fields), from_attributes, nullables; RunListResponse

**PASS.**

`schemas/runs.py` diff:

| Field | Schema type | ORM (`models.py`) | Match? |
|---|---|---|---|
| `id` | `int` | `Mapped[int]` NOT NULL | ✓ |
| `dagster_run_id` | `str` | `Mapped[str]` NOT NULL | ✓ |
| `kind` | `str` | `Mapped[str]` NOT NULL | ✓ |
| `status` | `str` | `Mapped[str]` NOT NULL | ✓ |
| `started_at` | `datetime \| None` | `Mapped[Optional[sa.DateTime]]` nullable | ✓ |
| `ended_at` | `datetime \| None` | `Mapped[Optional[sa.DateTime]]` nullable | ✓ |
| `triggered_by` | `int \| None` | `Mapped[Optional[int]]` nullable | ✓ |
| `dataset_id` | `int \| None` | `Mapped[Optional[int]]` nullable | ✓ |
| `recipe_id` | `int \| None` | `Mapped[Optional[int]]` nullable | ✓ |
| `source_collection_id` | `int \| None` | `Mapped[Optional[int]]` nullable | ✓ |

Field count: exactly 10. All nullables match the ORM. `model_config = ConfigDict(from_attributes=True)` present.  
`RunListResponse` = `items: list[RunListItem]` + `total: int`. Correct envelope.

---

### B3 — Status param typed as Optional[Literal[...]]

**PASS.**

```python
async def list_runs(
    status: Optional[Literal["pending", "running", "success", "failure"]] = None,
    ...
```

Exactly as contracted. All four values present. Default `None`. FastAPI validation fires at route layer → 422 before handler body (confirmed by T10: passes live).

---

### B4 — ORDER BY started_at DESC NULLS LAST, id DESC

**PASS.**

```python
.order_by(Run.started_at.desc().nulls_last(), Run.id.desc())
```

Verbatim match to contract §4. ORDER BY applied only to the page query (not the COUNT), as correct.  
Structural assertion T12 compiles the page query with `literal_binds=True` and asserts `"started_at"`, `"NULLS LAST"`, and `"id"` are all present. Passes live.

---

### B5 — Route registration order: GET "" before GET /{id} before GET /dagster/{dagster_run_id}

**PASS.**

`@runs_router` decorator positions in `routers/runs.py`:

```
line  92: @runs_router.post("")           — trigger_extract_run (F-018)
line 225: @runs_router.get("")            — list_runs           (F-049, NEW)
line 278: @runs_router.get("/{id}")       — get_run_detail      (F-048)
line 310: @runs_router.get("/dagster/...") — get_run_status     (F-005)
```

Correct: `GET ""` is declared first among GETs, before `GET /{id}` and `GET /dagster/{dagster_run_id}`. Matches agreed.md §7 required ordering exactly.

---

### B6 — Tests: 12 tests, T7 parametrized ×4, T6/T7 literal_binds, T12 ORDER BY

**PASS.**

- **Test count:** 12 test functions defined; T7 `@pytest.mark.parametrize("status_value", ["pending", "running", "success", "failure"])` expands to 4 collected items → **15 total collected**. Commit message reports "313 → 328 (+15)" — confirmed by live suite run (328 passed).
- **T6 (owner-scope M1 lynchpin):** Captures both `execute()` call args; compiles each with `literal_binds=True`; asserts `"triggered_by"` and `str(_MOCK_USER.id)` appear in both compiled SQL strings. ✓ passes.
- **T7 (status filter M1 lynchpin extension, parametrized):** Four variants, each independently compiles page and COUNT queries with `literal_binds=True`, asserts `status_value` literal in both. ✓ all 4 pass. Covers V2 (`success`) and V3 (`running`) structural requirements (M1 addendum satisfied).
- **T12 (ORDER BY structural):** Captures page query, `literal_binds=True`, asserts `"started_at"`, `"NULLS LAST"`, `"id"`. ✓ passes. (M2 addendum satisfied).
- **T1–T5, T8–T11:** All pass. Mock session pattern (`side_effect=[page_result, count_result]`) is correct — result proxies are plain `MagicMock` not `AsyncMock`, which is the right choice (`.scalars()`, `.all()`, `.scalar_one()` are synchronous).
- **_make_run_list_item():** All 14 ORM attributes populated. Consistent with `_make_run_detail()` precedent.
- **`_LIST_ITEM_KEYS`:** Exactly the 10 contracted fields. T11 asserts `set(item.keys()) == _LIST_ITEM_KEYS` (exact equality, not subset).

---

### B7 — openapi.json in same commit; contains RunListItem, RunListResponse, GET /api/runs

**PASS.**

- `packages/api-types/openapi.json` is one of the 4 files in commit `d81eb51`.
- `RunListItem` schema present at line 3353 with all 10 fields and correct types/nullability.
- `RunListResponse` schema present at line 3456 with `items` + `total`.
- `GET /api/runs` path present at top-level `paths["/api/runs"]["get"]` (line 84 in openapi.json), including `status` query param as `anyOf: [{enum: [pending, running, success, failure], type: string}, {type: null}]`, response `$ref: RunListResponse`, and `OAuth2PasswordBearer` security. ✓

---

### B8 — Hard invariants

**PASS on all applicable.**

| # | Invariant | Status |
|---|---|---|
| 1 | Lineage mandatory | N/A — pure read endpoint, no `Commit` created |
| 2 | Storage separation + CAS | ✓ — reads Postgres `run` rows only; no MinIO/S3 writes |
| 3 | Schema frozen post-publish | N/A — no schema mutations |
| 4 | LLM via gateway | N/A — no LLM calls |
| 5 | Async SQLAlchemy | ✓ — `async def`, `AsyncSession = Depends(get_session)`, `await session.execute()`, no `session.query()`, no sync sessions anywhere in changed files |
| 6 | OpenAPI ↔ TS type sync | ✓ — `packages/api-types/openapi.json` regenerated and committed in same commit `d81eb51` |

---

### B9 — Scope discipline: no out-of-scope changes

**PASS.**

- F-049 `passes` flag was **not** flipped in `d81eb51`. It was flipped correctly in a separate subsequent commit (`f997a0a`) as required by the sprint workflow (verifier must sign off before leader flips).
- F-048 `get_run_detail` handler: diff shows no modifications (only docstring update noting the new `GET ""` route, and import additions — no handler logic changed).
- No `limit`/`offset` params introduced (deferred per §6).
- No new features beyond F-049 scope.
- No `feature_list.json` restructuring.

---

### V-map — Spec verification criteria

| Criterion | Tests | Status |
|---|---|---|
| V1: 3 runs → `{items:[...], total:3}` | T1 (3 rows + total assertion), T5 (field completeness) | ✓ |
| V2: `?status=success` returns only completed runs | T8 (response content), T7[success] (SQL-structural both queries) | ✓ |
| V3: `?status=running` returns only in-progress runs | T9 (response content), T7[running] (SQL-structural both queries) | ✓ |

All three spec criteria have both a behavioural test and a SQL-structural test. The structural tests (T7 parametrized) are the stronger guard: they prove the filter reaches the SQL layer rather than being silently ignored.

---

### Round-1 addenda resolution (§13)

| Finding | Resolution in code | Status |
|---|---|---|
| M1 — T7 parametrize over all 4 status values | T7 decorated with `@pytest.mark.parametrize("status_value", ["pending", "running", "success", "failure"])`, all 4 variants compile and assert both queries. | ✓ Resolved |
| M2 — T12 ORDER BY structural assertion | T12 `test_list_runs_page_query_has_correct_order_by` added, uses `literal_binds=True`, asserts `"started_at"`, `"NULLS LAST"`, `"id"`. | ✓ Resolved |
| L1 — "9 fields" → "10 fields" in §3 | agreed.md §3 corrected. Code was always correct (10 fields). | ✓ Resolved (doc-only) |
| NIT-1 — §7 shadowing risk overstatement | agreed.md §7 and §1 reworded to "conventional, aids readability" framing. | ✓ Resolved (doc-only) |

---

### Minor observations (non-blocking)

1. **`required` list in OpenAPI `RunListItem` includes all 10 fields** — this is technically over-specified for nullable fields (`started_at`, `ended_at`, etc.), as those fields appear in `required` while also having `anyOf: [{...}, {type: null}]`. This is Pydantic v2's default OpenAPI 3.1 output for `Optional[X]` fields and is conformant — the `null` type in `anyOf` conveys optionality. Not a defect; noted for awareness only.

2. **`asset_keys` ORM column is `nullable=False` (NOT NULL)** — correctly excluded from `RunListItem`. Including it would have been wrong not just because it's a bulky ARRAY, but because it would have required a non-nullable `list[str]` on the schema, which is inconsistent with list-view usage. The exclusion is correct.

3. **T4 (owner isolation) uses separate session mocks** per user rather than a shared session that asserts the SQL filter — this is a behavioural test, not a SQL-structural test. It is not a gap: T6 is the SQL-structural owner-scope guard; T4 tests the broader isolation property at the response level. The combination is appropriate.

---

## Summary

All 10 checklist items (B1–B9 + V-map) pass. Both round-1 medium findings (M1 and M2) are fully resolved in code and confirmed by live test execution. 328 total tests pass with 0 regressions. The implementation exactly matches the agreed.md contract.

---

**VERDICT: APPROVED**
