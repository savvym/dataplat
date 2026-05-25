# S014-F-014 — Mode B Code Review

**Commit:** b4692aa  
**Reviewer:** Claude Sonnet 4.6 (reviewer role)  
**Date:** 2026-05-25

---

## Calibration checks (verify/reviewer-calibration.md)

- **CAL-1 (Async session):** PASS — Every DB call in `routers/sources.py` uses `await session.execute(select(...))`. Lines 170, 183, 193 all use async form. No `session.query()`, no sync `.commit()`. Verified against the full handler diff.
- **CAL-2 (LLM gateway):** N/A — No LLM calls in this diff. Read-only endpoint.
- **CAL-3 (OpenAPI sync):** PASS — `packages/api-types/openapi.json` appears in the same commit b4692aa. The diff adds the `/api/sources/collections/{id}/sources` path block (+71 lines) and the `SourceListResponse` component schema (+22 lines). The regen is not stale: it includes the correct `operationId`, security requirement, all three parameters (id, limit, offset), and the `$ref` to `SourceListResponse`.
- **CAL-4 (Lineage completeness):** N/A — No Commit object created; this is a read-only endpoint.
- **CAL-5 (CAS path discipline):** N/A — No blob storage writes.
- **CAL-6 (Schema freeze post-publish):** N/A — No Silver/Gold schema modification.
- **CAL-7 (Bronze faithfulness):** N/A — No adapter code.
- **CAL-8 (MVP scope discipline):** PASS — No deferred features introduced.
- **CAL-9 (Plugin isolation):** N/A — No plugin code.
- **CAL-10 (Test coverage):** PASS — 11 unit tests in `test_sources_list_by_collection.py`. At least one success case and multiple failure modes (404 ×2, 401, 422 ×3) are covered.
- **CAL-11 (Bias check):** Applied. Concrete file:line evidence cited for every criterion below.

---

## Contract criteria (agreed.md §3)

**Criterion 1 — 3-query pattern with no JOIN collapse:**  
PASS. `routers/sources.py:170-175` — Query 1 selects `SourceCollection` filtered by both `id` and `owner_id == current_user.id`; short-circuits to 404 if `None`. `routers/sources.py:183-190` — Query 2 is a separate `select(Source)` (not a JOIN). `routers/sources.py:193-198` — Query 3 is a `select(func.count()).select_from(Source)` scoped to the same `collection_id`. No JOIN collapse — an unowned collection cannot silently return 200.

**Criterion 2 — Route placement:**  
PASS. `grep -n "@router\."` output: line 53 GET /collections, line 95 POST /collections, line 136 GET /collections/{id}/sources (new), line 204 POST /upload, line 333 GET /{id}. The new route is inserted at the exact position required (after POST /collections, before POST /upload), and GET /{id} remains last.

**Criterion 3 — Schema:**  
PASS. `schemas/sources.py` adds `SourceListResponse` with `items: list[SourceRead]` and `total: int`. `SourceRead` is unchanged. The OpenAPI component confirms both fields are `required`.

**Criterion 4 — Pagination:**  
PASS. `routers/sources.py:143-144`: `limit: int = Query(default=20, ge=1, le=200)`, `offset: int = Query(default=0, ge=0)`. `total` is set from the count query (no limit/offset applied to it). Ordering is `Source.id.asc()` at line 186.

**Criterion 5 — Invariant #5 (async SQLAlchemy):**  
PASS — see CAL-1.

**Criterion 6 — Invariant #6 / CAL-3 (OpenAPI regen same commit):**  
PASS — see CAL-3.

**Criterion 7 — Tests (all 11 listed in agreed.md §5.4):**  
PASS, verified by reading assertions directly:

- `test_list_sources_by_collection_returns_200_with_items`: asserts `status_code==200`, `total==3`, `len(items)==3`. Contract requires `total==3 exact` in unit tests — SATISFIED.
- `test_list_sources_by_collection_items_have_required_fields`: loops over `["id","original_name","storage_uri","sha256","uploaded_at"]` and asserts `field in item` for each. Also spot-checks `item["id"]==7`, `storage_uri`, and `sha256`. SATISFIED.
- `test_list_sources_by_collection_total_is_full_count_not_page`: `limit=2`, mock returns 2 rows but count=3; asserts `len(items)==2` and `total==3`. SATISFIED.
- `test_list_sources_by_collection_offset_works`: `offset=2`, mock returns 1 row with count=3; asserts `len(items)==1`, `total==3`. SATISFIED.
- `test_list_sources_by_collection_collection_not_found_returns_404`: asserts 404, `detail=="Collection not found"`. SATISFIED.
- `test_list_sources_by_collection_other_owners_collection_returns_404`: same mock path as above (session returns None), asserts 404. SATISFIED. Note: this test and the not-found test use identical mock setup — the distinction is narrative only, which is acceptable since the handler is correct and the mock correctly exercises the code path.
- `test_list_sources_by_collection_empty_collection_returns_zero`: asserts `body == {"items": [], "total": 0}`. SATISFIED.
- `test_list_sources_by_collection_no_token_returns_401`: no auth header, no dependency override — asserts 401. SATISFIED.
- `test_list_sources_by_collection_invalid_limit_zero_returns_422`: asserts 422. SATISFIED.
- `test_list_sources_by_collection_invalid_limit_over_cap_returns_422`: asserts 422. SATISFIED.
- `test_list_sources_by_collection_invalid_offset_negative_returns_422`: asserts 422. SATISFIED.

**Criterion 8 — checks.sh F-014 V1/V2 inside `sources)` case:**  
PASS. The diff shows F014-V1 and F014-V2 blocks appended after the F013-V2 block, before the `rm -f "$PDF_FILE"` cleanup line and the `;;`. The `sources)` case statement is not broken. Each of the 3 unique PDFs has a distinct sha256 via the `b' ' * $i` suffix — avoids the sha256 uniqueness issue that would cause upload failures on rerun.

---

## Additional findings

None. No unhandled errors, no dead code, no scope creep, no hardcoded paths, no migration needed, no existing handler auth dependency altered.

One minor observation (NIT, not blocking): the `test_list_sources_by_collection_other_owners_collection_returns_404` test exercises the same code path and mock setup as `test_list_sources_by_collection_collection_not_found_returns_404` — it cannot distinguish "collection exists but wrong owner" from "collection does not exist" at the mock level. This is an inherent limitation of mocking that is explicitly acknowledged in agreed.md §3.5 and the test docstring. The handler implementation is correct; the test documents intent clearly. No action needed.

---

APPROVED

All 8 contract criteria satisfied. CAL-1 through CAL-11 checked; no violations found. Route ordering correct, 3-query ownership pattern implemented without JOIN collapse, OpenAPI regenerated in the same commit, all 11 required unit tests present with concrete assertions matching the agreed spec.
