# S020-F-020 — Reviewer Feedback (Mode A)

**Reviewer:** Claude (reviewer role)
**Date:** 2026-05-26
**Status:** APPROVED

---

## Verdict

**APPROVED** — the contract is technically sound, complete, correctly scoped, and will pass the V1/V2 verification criteria as written. No changes are required before implementation begins.

---

## Check-by-check findings

### 1. Endpoint matches verification criteria exactly ✅

F-020 requires:
- `GET /api/sources/{id}/documents` returning an array with `extractor_name`, `extractor_version`, `storage_prefix`, `is_canonical`, `materialized_at` after extraction.
- `GET /api/sources/99999/documents` returning 404.

The proposed `GET /{source_id}/documents` satisfies both. `DocumentVariantRead` (§3) is a strict superset of the 5 required fields — all 5 are present and typed correctly against the `DocumentVariant` model columns.

### 2. Owner-scoping correct ✅

The 2-step logic mirrors `GET /{id}` exactly in semantics:
- LEFT JOIN `source` → `source_collection`, filter on `collection_id IS NULL OR owner_id = :user_id`.
- Both "source does not exist" and "source belongs to another user" resolve to a single 404, preventing enumeration leaks.

The existing `GET /{id}` (line 353–364 in `sources.py`) uses `select(Source).join(…isouter=True).where(or_(SourceCollection.owner_id == …, Source.collection_id.is_(None)))`. The proposed Step 1 is logically identical. ✅

### 3. All required fields in `DocumentVariantRead` present ✅

Cross-checked every field against `DocumentVariant` in `models.py` (lines 111–151):

| Proposed field | Model column | Nullability match |
|---|---|---|
| `id: int` | `Mapped[int]` PK | ✅ |
| `extractor_name: str` | `Mapped[str]` NOT NULL | ✅ |
| `extractor_version: str` | `Mapped[str]` NOT NULL | ✅ |
| `config_hash: str` | `Mapped[str]` NOT NULL | ✅ |
| `storage_prefix: str` | `Mapped[str]` NOT NULL | ✅ |
| `page_count: int \| None` | `Mapped[Optional[int]]` | ✅ |
| `image_count: int \| None` | `Mapped[Optional[int]]` | ✅ |
| `is_canonical: bool \| None` | `Mapped[Optional[bool]]` | ✅ |
| `materialized_at: datetime \| None` | `Mapped[Optional[DateTime]]` | ✅ |
| `dagster_run_id: str \| None` | `Mapped[Optional[str]]` | ✅ |

`model_config = ConfigDict(from_attributes=True)` is correctly required (same as `SourceRead`).

### 4. Route ordering correct — no collision with `/{id}` ✅

`/{source_id}/documents` (2 segments) can never match against `/{id}` (1 segment). FastAPI dispatches on full path. The analysis in §4.3 is correct. Explicit placement before `/{id}` preserves the stated router invariant ("catch-all last") and is consistent with the docstring at the top of `sources.py`.

One edge note the contract handles correctly: if someone GETs `/upload/documents`, `source_id: int` type coercion will reject the string "upload" with a 422 before the handler body runs. No ambiguity. ✅

### 5. Hard invariants — none violated ✅

| Invariant | Assessment |
|---|---|
| #1 Lineage mandatory | N/A — read-only; no commit. |
| #2 Storage separation + CAS | N/A — reads Postgres only; no MinIO access. |
| #3 Schema frozen post-publish | N/A — `DocumentVariantRead` is a response DTO, not a repo schema. |
| #4 LLM calls via gateway | N/A — no LLM calls. |
| #5 Async SQLAlchemy | Explicitly required: `async def`, `AsyncSession`, `await session.execute()`, no `session.query()`. ✅ |
| #6 OpenAPI ↔ TS type sync | `make codegen` + `packages/api-types/` diff committed in same commit. Explicitly required in §2 and §5. ✅ |

### 6. Verification plan adequate to prove V1 and V2 ✅

**V1 unit test** covers: 200 status, length-1 array, 5 required fields by name, specific `extractor_name`/`extractor_version` values. ✅

**V2 unit test** covers: 404 status, and — notably — asserts the second `session.execute` is *never called*, proving the short-circuit on missing source. This is a particularly good test. ✅

**Integration checks** for both V1 (field-level `jq` assertions on a real extraction result) and V2 (status-code-only assertion on `source_id=99999`) are complete and self-contained. ✅

**Additional unit cases** (`empty_list`, `no_token_401`, `other_owner_404`, `full_field_presence`) cover the relevant edge cases without being redundant. ✅

### 7. Edge cases and security concerns ✅

All meaningful edge cases are handled:
- Source exists, no variants → `200 + []` (extraction not yet run).
- Source does not exist → `404`.
- Source exists but owned by another user → `404` (same code, no enumeration leak).
- No auth token → `401` (handled by `get_current_user` dependency before handler body runs).

### 8. Response shape matches verification ✅

F-020 says "returns **array** with 1 item". The proposed `response_model=list[DocumentVariantRead]` is a flat JSON array. ✅

---

## Minor observations (non-blocking — do not require changes)

**SQL pseudocode vs mock test minor inconsistency (§4.2 vs §6):** The SQL in §4.2 writes `SELECT source.id`, but the V1 unit test mock expects `.scalar_one_or_none()` to return a `Source` stub (object). In the actual SQLAlchemy implementation the query will use `select(Source)` following the established `GET /{id}` pattern (line 353 of `sources.py`), which returns a `Source` ORM object — consistent with what the mock expects. The SQL in §4.2 is pseudocode describing the logical filter, not the exact column projection. Implementer should follow `select(Source)`, not `select(Source.id)`.

**F-019 dependency note:** F-019 (`passes=true`) includes the criterion "GET /api/sources/{source_id}/documents returns 1 variant with extractor_name='mineru'", which is exactly the endpoint F-020 implements. Before writing any code, the implementer should check whether a partial implementation already exists in `sources.py`. If it does, F-020 work may be reduced to ownership-scoping polish + codegen rather than a full new handler.

**`source_id` field not in `DocumentVariantRead`:** This is intentional and correct — the client already knows `source_id` from the URL. Verified the omission is deliberate, not an oversight.

---

## Summary

The contract is solid on every dimension that matters: correctness, security, invariant compliance, test coverage, and alignment with the existing router patterns. Proceed to implementation.
