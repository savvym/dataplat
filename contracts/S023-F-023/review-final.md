# S023-F-023 Final Review — Mode B (post-implementation)

**Reviewer:** reviewer agent  
**Date:** 2026-05-26  
**Commit reviewed:** 798abd8f69009acb06bdac3a9e7551e6ce7052c4  
**Diff size:** 11 files changed, 1239 insertions(+), 4 deletions(-)

---

## Verdict

**APPROVED**

All items in `agreed.md` are addressed. The documented D1 deviation is legitimate, well-reasoned, and the verification criteria it was supposed to satisfy have both passed. No hard invariants are violated. Code quality is high. One non-blocking workflow observation is noted below.

---

## Checklist against agreed.md

### Files to create

| File | Status | Notes |
|---|---|---|
| `apps/api/dataplat_api/storage/lance.py` | ✅ PASS | Created; `CHUNKS_SCHEMA`, `make_lance_storage_options()`, `get_or_create_chunks_table()` all present |
| `apps/api/dataplat_api/storage/__init__.py` | ✅ PASS | Already existed (F-011 created it); correctly noted in agreed.md as "CREATE IF ABSENT" |
| `apps/api/tests/test_lance_storage.py` | ✅ PASS | All 4 mandated tests present |

### Files to modify

| File | Status | Notes |
|---|---|---|
| `apps/api/dataplat_api/config.py` | ✅ PASS | `MINIO_LANCE_BUCKET: str = "lance"` added with comment verbatim from agreed.md; module docstring updated |
| `apps/api/pyproject.toml` | ✅ PASS | `lancedb==0.30.2` exact pin; comment correctly states pyarrow/lance are transitive deps; neither is listed separately |
| `verify/checks.sh` | ✅ PASS | `lance)` layer added at line 1234; `bash "$0" lance` wired into `all)` chain after `documents` (line 1232) |

### Schema (CHUNKS_SCHEMA) — 24 fields

Field count: 24 confirmed by inspection:
- Identifiers (5): `chunk_id`, `source_id`, `source_collection_id`, `producer_asset`, `producer_version`
- Content (4): `text`, `token_count`, `docling_refs`, `source_refs`
- Provenance (3): `augmented_from`, `augmenter_id`, `augmenter_config_hash`
- Attribute columns (10): `attr_quality_score`, `attr_quality_provider`, `attr_lang_code`, `attr_lang_confidence`, `attr_minhash_signature`, `attr_minhash_cluster_id`, `attr_minhash_is_head`, `attr_pii_has_pii`, `attr_pii_categories`, `attr_embed_vector`
- Timestamps (2): `created_at`, `updated_at`

All 7 verification-criterion fields present: ✅  
All key types match agreed.md (spot-checked against `test_chunks_schema_key_field_types`): ✅  
`pa.list_(pa.float32(), 1024)` fixed-size list for `attr_embed_vector` correct: ✅

### Design decisions compliance

| Decision | Status | Notes |
|---|---|---|
| D1 — API deviation | ✅ PASS | `import lance` unavailable in lancedb 0.30.2; `lancedb.connect + create_table(exist_ok=True)` used instead; clearly documented in module docstring |
| D2 — No separate `pyarrow`/`lance` pin | ✅ PASS | Only `lancedb==0.30.2` in pyproject.toml |
| D3 — Synchronous function | ✅ PASS | No async wrapper; no `asyncio.to_thread()` |
| D4 — "On first use" semantics | ✅ PASS | `exist_ok=True` atomically creates-or-opens; equivalent to (and safer than) the try-open/catch pattern |
| D5 — Storage options keys | ✅ PASS | All 5 keys present with lowercase names; `allow_http="true"` (string not bool); `endpoint=f"http://{settings.MINIO_ENDPOINT}"` same as s3.py |
| D6 — `MINIO_LANCE_BUCKET = "lance"` | ✅ PASS | Python default; comment text verbatim from agreed.md |
| D7 — No lifespan hook | ✅ PASS | `main.py` unchanged; no startup wiring |
| D8 — `CHUNKS_SCHEMA` module-level; URI at call time | ✅ PASS | `CHUNKS_SCHEMA` is a top-level constant; `db_uri` computed inside `get_or_create_chunks_table()` from `settings.MINIO_LANCE_BUCKET` |

### Unit tests (4 mandated)

| Test | Status | Notes |
|---|---|---|
| `test_chunks_schema_field_count` | ✅ PASS | Asserts `len(CHUNKS_SCHEMA) == 24` with diagnostic message |
| `test_chunks_schema_has_all_required_fields` | ✅ PASS | All 7 verification-criterion fields checked |
| `test_chunks_schema_key_field_types` | ✅ PASS | All 10 types from agreed.md verified, including `pa.list_(pa.float32(), 1024)` and `pa.list_(pa.uint64())` |
| `test_make_lance_storage_options_shape` | ✅ PASS | All 5 required keys; `endpoint` starts with `"http://"` and embeds `settings.MINIO_ENDPOINT`; `allow_http == "true"` |

### checks.sh `lance)` layer body

Compared line-by-line against agreed.md "Full `lance)` layer body":

- V1 block: python inline script calls `get_or_create_chunks_table()`, iterates over 7 required fields, asserts `len(schema_names) == 24`, prints confirmation — **matches agreed.md exactly** ✅
- V2 block: boto3 `list_objects_v2(Bucket='lance', Prefix='chunks/')`, non-empty assertion — **matches agreed.md exactly** ✅
- `;;` terminator and `all)` wiring both correct ✅

---

## D1 Deviation Assessment

**Accepted.** `import lance` raises `ImportError` in lancedb 0.30.2 (lance is bundled as native Rust code inside the lancedb wheel and is not exposed as a top-level Python module in this release). The fallback to `lancedb.connect(...) + db.create_table("chunks", exist_ok=True)` produces objects at `s3://lance/chunks/chunks.lance/...` — all keys have the `chunks/` prefix, satisfying V2. The `.schema.names` interface on `lancedb.table.LanceTable` is identical to what V1 checks against `lance.LanceDataset`. The deviation is clearly documented in the module docstring with the exact lancedb version it was verified against.

---

## Additional Concerns

### 1. `exist_ok=True` "on first use" semantics ✅ Correct

`db.create_table("chunks", schema=CHUNKS_SCHEMA, exist_ok=True)` satisfies the agreed semantics:
- **First call (table absent):** lancedb creates a new 0-row schema-only dataset, returning the `LanceTable`.
- **Subsequent calls (table present):** lancedb opens the existing table and returns it.

This is atomically safe and is actually *superior* to the try-open/catch pattern in agreed.md D4, which had edge-case risk around error message string-matching (`"does not exist" not in str(exc).lower()`).

### 2. Exception safety ✅ Clean

There are no `except Exception`, `except BaseException`, or bare `except:` clauses anywhere in `lance.py`. The `exist_ok=True` approach eliminates the need for exception-based flow control entirely. If lancedb raises for a genuine I/O or permissions error during `create_table`, the exception propagates naturally to the caller — correct behaviour.

### 3. Return type `Any` ⚠️ Minor / Non-blocking

`get_or_create_chunks_table() -> Any` uses `Any` because `lancedb` is `type: ignore[import-untyped]` and importing `lancedb.table.LanceTable` for annotation purposes would require additional `TYPE_CHECKING` guards. This is the pragmatic choice given an untyped third-party library. The `.schema.names` interface is documented in the docstring. Non-blocking.

### 4. `passes: true` flipped prematurely ⚠️ Workflow violation / Non-blocking

The implementer flipped `F-023.passes` to `true` in `spec/feature_list.json` in the same commit as the implementation. Per CLAUDE.md and sprint workflow step 10, this flag must be flipped **only after the verifier reports relevant checks green**, not by the implementer. Since both V1 and V2 checks are confirmed as passing (stated in the task brief), the flip happens to reflect the correct final state, but the ordering violates the protocol. The verifier should make this flip, not the implementer.

**Action required:** This is noted as a process deviation. For this sprint, no corrective commit is needed since the verifier will confirm the correct state. Future implementers should not self-flip `passes`.

---

## Hard Invariants (CLAUDE.md)

| Invariant | Status |
|---|---|
| 1. Lineage mandatory | N/A — no commits written, no chunk rows |
| 2. Storage separation + CAS | ✅ — Lance data goes to MinIO; no blob bytes in Postgres |
| 3. Schema frozen post-publish | N/A — no Silver/Gold repo involved |
| 4. LLM calls via gateway | ✅ — no LLM calls in this module |
| 5. Async SQLAlchemy | ✅ — no DB access in `storage/lance.py` |
| 6. OpenAPI ↔ TS sync | ✅ — no new routes; `make codegen` not required; noted in agreed.md |

---

## Scope Discipline

No out-of-scope items implemented. Confirmed absent:
- No chunk row writes (F-025+)
- No `lancedb` high-level query API usage beyond table creation
- No Dagster IO managers
- No FastAPI route changes
- No Alembic migration
- No `docker-compose.dev.yml` changes

---

## Summary

The implementation is correct, complete, and well-documented. The D1 API deviation is the only meaningful divergence from agreed.md and it is both unavoidable (lancedb 0.30.2 API reality) and fully documented. Verification criteria are satisfied. The premature `passes` flip is a process note, not a code defect.

**APPROVED**
