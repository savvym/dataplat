# S010-F-010 — Mode B Code Review

**Reviewer:** Independent reviewer (Claude)
**Date:** 2026-05-25
**Commit under review:** `2d7d93f` (parent `3dc6275`)
**Contract:** `contracts/S010-F-010/agreed.md`

---

## Calibration Sweep (verify/reviewer-calibration.md CAL-1..CAL-11)

- **CAL-1 (Async session enforcement):** PASS — `apps/api/dataplat_api/routers/sources.py:51,61` both use `await session.execute(select(...))`. `.scalars().all()` and `.scalar_one()` are synchronous result-proxy calls (correct). No `session.query()` anywhere in the diff.
- **CAL-2 (LLM gateway enforcement):** N/A — no LLM calls introduced in this diff.
- **CAL-3 (OpenAPI sync):** PASS — `packages/api-types/openapi.json` is modified in the same commit as `schemas/collections.py` and `routers/sources.py`. The diff at line 1477-1480 shows `CollectionListResponse.items` now `$ref`s `SourceCollectionOut` instead of the prior `"items": {}`. Query parameters (`limit`, `offset`) and the `422` response are also added. The regen is consistent with the schema change.
- **CAL-4 (Lineage completeness):** N/A — no Commit object created in this diff.
- **CAL-5 (CAS path discipline):** N/A — no blob storage paths touched.
- **CAL-6 (Schema freeze post-publish):** N/A — no Silver/Gold schema modified.
- **CAL-7 (Bronze faithfulness):** N/A — no adapter plugin touched.
- **CAL-8 (MVP scope discipline):** PASS — no deferred MVP features introduced. Pagination is `limit`/`offset` (correct, not Kafka, Celery, or cursor). No ACL, OAuth, or self-registration.
- **CAL-9 (Plugin isolation):** N/A — no plugin code in this diff.
- **CAL-10 (Test coverage):** PASS — 12 new unit tests present: happy paths (empty, 3-row, limit, offset, shape, defaults), one auth failure (401), four 422 validation failures (limit=0, limit=-1, limit=201, offset=-1), and one owner-filter structural test. Both happy and failure paths covered.
- **CAL-11 (Bias check):** Actively suppressed. Evidence cited at `file:line` for every finding below.

---

## Contract Criteria (agreed.md §2–§7)

### §2 Files changed

- `apps/api/dataplat_api/routers/sources.py` MODIFIED: PASS — stub body replaced with two-query implementation; `limit`, `offset` Query params added; `Depends(get_session)` added; F-009 POST handler byte-for-byte unchanged (verified at lines 72-110 of the current file — `create_collection` is identical).
- `apps/api/dataplat_api/schemas/collections.py` MODIFIED: PASS — `CollectionListResponse.items` narrowed from `list[Any]` to `list[SourceCollectionOut]`; `Any` removed from the `typing` import (line 15 of the current file shows `from typing import Annotated` only).
- `apps/api/tests/test_sources_collections_list.py` NEW: PASS — 332-line file present.
- `verify/checks.sh` MODIFIED: PASS — LIST-V1/V2 steps appended after the existing F-009 V3 step (diff line 1510-1562). No existing `all)` chain modified.
- `packages/api-types/openapi.json` MODIFIED: PASS — regenerated in the same commit (CAL-3 above).
- **Files NOT touched confirmed:** `db/models.py`, `db/session.py`, `auth/dependencies.py`, `test_sources_collections_create.py`, `conftest.py` — none appear in the diff.

### §3 D1 — Pagination parameters

PASS — `apps/api/dataplat_api/routers/sources.py:32-33`: `limit: int = Query(default=20, ge=1, le=200)`, `offset: int = Query(default=0, ge=0)`. Matches the agreed contract exactly.

### §3 D2 / D3 — Owner filter on BOTH queries, total semantics

PASS — Query 1 (line 53): `.where(SourceCollection.owner_id == current_user.id)`. Query 2 (line 64): `.where(SourceCollection.owner_id == current_user.id)`. The COUNT query has no `.limit()` or `.offset()` — `total` is the full owner count, independent of pagination. This is the crux of V2's proof.

### §3 D4 — Ordering `ORDER BY id ASC`

PASS — `sources.py:54`: `.order_by(SourceCollection.id.asc())`. COUNT query correctly has no ORDER BY.

### §3 D5 — items narrowing + Any removal

PASS — `schemas/collections.py:61`: `items: list[SourceCollectionOut]`. Line 15: `from typing import Annotated` (Any removed).

### §3 D6 — Two async queries

PASS — Two separate `await session.execute()` calls at `sources.py:51` and `sources.py:61`. No sync session or `.query()`.

### §3 D7 — Response serialization

PASS — `sources.py:68`: `items = [SourceCollectionOut.model_validate(row) for row in rows]`. `SourceCollectionOut` has `model_config = ConfigDict(from_attributes=True)` (verified at `schemas/collections.py:55`).

### §3 D8 — get_session added to GET handler

PASS — `sources.py:35`: `session: AsyncSession = Depends(get_session)`.

### §5 — checks.sh LIST-V1 assertion

PASS — `verify/checks.sh` line 1541: `assert body['total'] == len(body['items'])`. This is the corrected (non-tautological) assertion from the Mode A iter-1 fix. The `total >= 3` guard is also present. LIST-V2 (`limit=2, total >= 3`) is correctly present.

### §6 — 12 tests present and meaningful

Cross-checking against agreed.md §6 test table:

| Test | Present | Asserts what contract says |
|---|---|---|
| `test_list_collections_empty` | YES (line 366) | `body == {"items": [], "total": 0}`, status 200 |
| `test_list_collections_total_matches_owner_count` | YES (line 379) | 3 rows → `total==3`, `len(items)==3` |
| `test_list_collections_limit_param` | YES (line 398) | 2 rows, count=3 → `len(items)==2`, `total==3` |
| `test_list_collections_offset_param` | YES (line 416) | 1 row, count=3 → `len(items)==1`, `total==3` |
| `test_list_collections_items_shape` | YES (line 431) | All 6 keys present; id/name/owner_id/dataset_card_md values checked |
| `test_list_collections_owner_filter` | YES (line 450) | `call_args_list[0].args[0].compile(literal_binds)` asserts "owner_id" and `str(_MOCK_USER.id)` in SQL |
| `test_list_collections_no_token_returns_401` | YES (line 495) | 401, `WWW-Authenticate: Bearer` |
| `test_list_collections_invalid_limit_zero_returns_422` | YES (line 506) | 422 |
| `test_list_collections_invalid_limit_negative_returns_422` | YES (line 517) | 422 |
| `test_list_collections_invalid_limit_over_cap_returns_422` | YES (line 528) | 422 |
| `test_list_collections_invalid_offset_negative_returns_422` | YES (line 539) | 422 |
| `test_list_collections_default_params_accepted` | YES (line 550) | 200 |

All 12 tests present and assert what the contract specifies. PASS.

### §7 — Hard invariants #5 and #6

- Invariant #5 (Async SQLAlchemy): PASS — verified under CAL-1.
- Invariant #6 (OpenAPI sync): PASS — verified under CAL-3.

---

## Implementer-Declared Deviations

### D1 — MagicMock(spec=SourceCollection) instead of real ORM instance

**Decision: ACCEPT.**

The implementer's justification is technically correct. SQLAlchemy's ORM instrumentation requires `_sa_instance_state` (set by `__init__` via the mapper event) — constructing a `SourceCollection` with bare `__new__` or with field assignments outside a mapper context raises `AttributeError`. Using `MagicMock(spec=SourceCollection)` sidesteps this correctly.

The key question is whether the test weakens serialization coverage. `SourceCollectionOut.model_validate(obj)` with `from_attributes=True` reads attributes by name from `obj`. With `MagicMock(spec=SourceCollection)`, the `spec` constrains attribute access to only the real class's attributes — so a renamed field on `SourceCollectionOut` (e.g., `owner` instead of `owner_id`) that doesn't exist on `SourceCollection` would cause `model_validate` to raise a `ValidationError` or produce `None` for required fields, which would surface in the shape test (`test_list_collections_items_shape` at line 431 asserts `owner_id` is present). The spec constraint does catch a field-name mismatch between the ORM model and the schema, though it would NOT catch the reverse case (field renamed on the ORM side but not the schema, where Pydantic would simply read `None` from the MagicMock for that attribute since MagicMock auto-generates attribute access). This residual gap is a pre-existing limitation of the test pattern; it is not introduced by F-010 and does not affect runtime correctness. The `test_list_collections_items_shape` test provides the concrete field-existence check that partially compensates. Accept with no action required.

### D2 — Two existing test_auth.py tests modified to add get_session override

**Decision: ACCEPT.**

The modification is necessary and correct. The GET handler now calls `session.execute()` twice, so tests that exercise the real JWT decode path (`test_collections_jwt_decode_path`) or the valid-token path (`test_collections_valid_token_returns_200`) must supply a session mock that handles those two additional calls.

Verification of correctness for each test:

**`test_collections_valid_token_returns_200` (diff line 368-382):** `get_current_user` is bypassed (overridden), so the session receives exactly 2 execute calls: page SELECT and COUNT. The mock at line 368 uses `side_effect=[page_result, count_result]` which correctly serves both. Auth assertion (`response.status_code == 200`) is unchanged — the test still proves that a valid token + user override grants access.

**`test_collections_jwt_decode_path` (diff line 443-445):** `get_current_user` runs for real (not overridden), calling `session.execute()` once for the user lookup. The list handler then calls `session.execute()` twice more. The mock at line 445 uses `side_effect=[auth_result, page_result, count_result]` — three calls in order, correctly matching `scalar_one_or_none()` (auth), `.scalars().all()` (page), and `.scalar_one()` (count). The critical auth assertion remains: the test proves the full JWT decode path (`jwt.decode → sub → DB lookup → User`) and that the returned 200 proves the whole chain works. Adding the two list-handler mocks does not weaken this — they are post-auth calls that the handler now requires.

The existing `test_collections_user_not_found_returns_401` test was NOT modified. It overrides `get_session` via `_make_session_dependency_for_user(user=None)`. That helper makes `scalar_one_or_none()` return None, which causes `get_current_user` to raise 401 before the list handler's execute calls are reached — so no change needed there. This is correct and unchanged.

---

## Additional Findings

No additional findings. Specifically verified:

- **POST handler untouched:** `sources.py:72-110` is byte-for-byte identical to F-009. Confirmed by reading the current file and cross-checking the diff (the POST section shows no `+` or `-` lines in the handler body).
- **No stray openapi.json changes:** The diff adds only the expected query params (`limit`, `offset`), 422 response, narrowed `items`, security block reordering (cosmetic), and updated descriptions. No unrelated schema components were modified.
- **422-only tests without get_session override:** The four 422 tests (`test_list_collections_invalid_limit_*`, `test_list_collections_invalid_offset_*`) override only `get_current_user`. FastAPI validates `Query()` constraints before executing any handler code or resolving `Depends(get_session)` — the session is never reached on a 422 path. These tests are correct.
- **Scope discipline:** No MVP-deferred features (Celery, OAuth, ACL, etc.) appear in the diff.
- **Dead code:** None introduced.
- **Missing migration:** No ORM model changes — no migration needed.

---

## Contract Criterion Summary

| Criterion | Verdict | Evidence |
|---|---|---|
| Two async queries, owner-filtered, both queries | PASS | `sources.py:51-66` |
| total is full count, not page count | PASS | COUNT query has no `.limit()/.offset()` at `sources.py:61-66` |
| items narrowed to list[SourceCollectionOut] | PASS | `schemas/collections.py:61` |
| Any import removed | PASS | `schemas/collections.py:15` |
| openapi.json regenerated same commit | PASS | diff includes `packages/api-types/openapi.json` with $ref narrowing |
| 12 unit tests present + correct assertions | PASS | all 12 verified by name and assertion content |
| checks.sh LIST-V1 uses `total == len(items)` | PASS | `checks.sh` line 1541 |
| checks.sh LIST-V2 uses `len(items)==2, total>=3` | PASS | `checks.sh` line 1558-1559 |
| POST handler F-009 untouched | PASS | `sources.py:72-110` unchanged |
| Invariant #5 async SQLAlchemy | PASS | `sources.py:51,61` both `await session.execute` |
| Invariant #6 OpenAPI sync | PASS | CAL-3 |

---

APPROVED
