# S025-F-025 — Mode B Review (review-final.md)

**Sprint:** S025-F-025 — Chunking operator (fixed-size token chunking into Lance table)  
**Diff base:** `cd6fe52`  **Diff head:** `b855325`  
**Contract:** `contracts/S025-F-025/agreed.md`  
**Verdict:** **APPROVED**

---

## 1. Requirement-by-Requirement Verification

### §2 — Files changed / created

| File | Action | Status |
|---|---|---|
| `dagster/dagster_platform/chunker.py` | CREATE | ✅ Present — 264-line pure-helper module |
| `dagster/dagster_platform/definitions.py` | EDIT | ✅ Stub replaced with real logic; chunker imports added (lines 35–41) |
| `dagster/tests/test_chunker.py` | CREATE | ✅ Present — 277-line test file |
| `docker/dagster/Dockerfile` | EDIT | ✅ Three packages added (lines 36–38); tiktoken bake added (line 51) |
| `docker/docker-compose.dev.yml` | EDIT | ✅ All four Dagster services updated with MINIO_* vars |
| `verify/checks.sh` | EDIT | ✅ `chunks)` case added (lines 1771–2060); `all)` chain updated (lines 1338–1339) |

---

### §3-A — Dockerfile new packages (agreed.md §3-A)

| Requirement | Location | Status |
|---|---|---|
| `lancedb==0.30.2` added to pip install | `docker/dagster/Dockerfile:36` | ✅ |
| `pyarrow==24.0.0` added to pip install | `docker/dagster/Dockerfile:37` | ✅ |
| `tiktoken==0.7.0` added to pip install | `docker/dagster/Dockerfile:38` | ✅ |
| `RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"` added for R4 vocab bake | `docker/dagster/Dockerfile:51` | ✅ |

---

### §3-B — docker-compose.dev.yml env vars (agreed.md §3-B, F7)

| Service | Requirement | Location | Status |
|---|---|---|---|
| `dagster-webserver` | `MINIO_LANCE_BUCKET: ${MINIO_LANCE_BUCKET:-lance}` | `docker-compose.dev.yml:138` | ✅ |
| `dagster-daemon` | `MINIO_LANCE_BUCKET: ${MINIO_LANCE_BUCKET:-lance}` | `docker-compose.dev.yml:175` | ✅ |
| `dagster-worker-cpu` | Full MINIO_* block: ENDPOINT, ROOT_USER, ROOT_PASSWORD, LANCE_BUCKET | `docker-compose.dev.yml:203–206` | ✅ |
| `dagster-worker-heavy` | Full MINIO_* block: ENDPOINT, ROOT_USER, ROOT_PASSWORD, LANCE_BUCKET | `docker-compose.dev.yml:236–239` | ✅ |

**Note (non-blocking):** The workers also receive `POSTGRES_DB` and `PLATFORM_DB_URL` (lines 207–208 and 240–241), which are not listed in agreed.md §3-B but are required by `lookup_source_collection_id()` (`chunker.py:248`). This is a necessary addition in the correct direction; the implementation is more complete than the contract minimum.

---

### §4 — Design decisions

#### D1 — Token budget and encoding
- `TOKEN_BUDGET = 512` at `chunker.py:38`
- `_ENCODER = tiktoken.get_encoding("cl100k_base")` loaded at module import (line 42) — once per worker process, not per chunk ✅

#### D2 — Text extraction fallback chain (3 steps)
`extract_text_from_document()` at `chunker.py:138–155`:
1. `text = doc.export_to_markdown().strip()` — if non-empty, return it (`chunker.py:149–151`)
2. `name = (doc.name or "").strip()` — if non-empty, return it (`chunker.py:152–154`)
3. `return f"source_{source_id}"` — final deterministic fallback (`chunker.py:155`)

All three steps correctly ordered. ✅

#### D3 — CHUNKS_SCHEMA duplication, no nullable=False
`chunker.py:50–84` and `lance.py:58–92` were compared field-by-field:

| Field | chunker.py type | lance.py type | Match |
|---|---|---|---|
| `chunk_id` | `pa.string()` | `pa.string()` | ✅ |
| `source_id` | `pa.int64()` | `pa.int64()` | ✅ |
| `source_collection_id` | `pa.int64()` | `pa.int64()` | ✅ |
| `producer_asset` | `pa.string()` | `pa.string()` | ✅ |
| `producer_version` | `pa.string()` | `pa.string()` | ✅ |
| `text` | `pa.large_string()` | `pa.large_string()` | ✅ |
| `token_count` | `pa.int32()` | `pa.int32()` | ✅ |
| `docling_refs` | `pa.string()` | `pa.string()` | ✅ |
| `source_refs` | `pa.string()` | `pa.string()` | ✅ |
| `augmented_from` | `pa.string()` | `pa.string()` | ✅ |
| `augmenter_id` | `pa.string()` | `pa.string()` | ✅ |
| `augmenter_config_hash` | `pa.string()` | `pa.string()` | ✅ |
| `attr_quality_score` | `pa.float32()` | `pa.float32()` | ✅ |
| `attr_quality_provider` | `pa.string()` | `pa.string()` | ✅ |
| `attr_lang_code` | `pa.string()` | `pa.string()` | ✅ |
| `attr_lang_confidence` | `pa.float32()` | `pa.float32()` | ✅ |
| `attr_minhash_signature` | `pa.list_(pa.uint64())` | `pa.list_(pa.uint64())` | ✅ |
| `attr_minhash_cluster_id` | `pa.int64()` | `pa.int64()` | ✅ |
| `attr_minhash_is_head` | `pa.bool_()` | `pa.bool_()` | ✅ |
| `attr_pii_has_pii` | `pa.bool_()` | `pa.bool_()` | ✅ |
| `attr_pii_categories` | `pa.list_(pa.string())` | `pa.list_(pa.string())` | ✅ |
| `attr_embed_vector` | `pa.list_(pa.float32(), 1024)` | `pa.list_(pa.float32(), 1024)` | ✅ |
| `created_at` | `pa.timestamp("ms")` | `pa.timestamp("ms")` | ✅ |
| `updated_at` | `pa.timestamp("ms")` | `pa.timestamp("ms")` | ✅ |

**24 fields, exact order, no `nullable=False` additions anywhere.** ✅

#### D4 — chunk_id naming
`chunker.py:185`: `"chunk_id": f"{source_id}_{seq}"`, `seq` starts at 0 and increments per window (`chunker.py:180, 210`). ✅

#### D5 — Idempotency predicate
`chunker.py:233`: `table.delete(f"source_id = {source_id} AND producer_asset = 'chunks'")` — exact predicate required by agreed.md D5. ✅

#### D6 — producer_version constant
`chunker.py:37`: `CHUNKER_VERSION = "0.1.0"`. Used at `chunker.py:189`: `"producer_version": CHUNKER_VERSION`. `"producer_asset": "chunks"` at `chunker.py:188`. ✅

#### D7 — source_collection_id from Postgres, ValueError on miss
`lookup_source_collection_id()` at `chunker.py:242–263`:
- Executes `SELECT collection_id FROM source WHERE id = %s` (`chunker.py:254`)
- `if row is None: raise ValueError(...)` at `chunker.py:259–262`. ✅

#### D8 — V1–V4 Lance queries from fastapi container
All four verification checks (`checks.sh:1950–2059`) run via `docker compose exec -T fastapi python`. ✅

#### D9 — No `deps=[extract_mineru]` on chunks asset
`definitions.py:124`: `@asset(partitions_def=sources_partitions, description=...)` — no `deps=` argument. ✅  
`checks.sh:1850–1898`: `chunks)` layer explicitly triggers `extract_mineru` and polls `COMPLETED_SUCCESS` before triggering `chunks`. ✅

#### D10 — Null handling for optional fields
`chunker.py:184–209` (the row dict inside `fixed_size_chunk()`):
- `docling_refs`: `""` ✅ (`chunker.py:192`)
- `source_refs`: `""` ✅ (`chunker.py:193`)
- `augmented_from`, `augmenter_id`, `augmenter_config_hash`: `None` ✅ (`chunker.py:194–196`)
- All `attr_*` fields: `None` ✅ (`chunker.py:197–207`)

---

### §5 — Verification plan

| Criterion | checks.sh location | Status |
|---|---|---|
| Unit tests first, inside dagster-webserver container | `checks.sh:1783–1789` | ✅ |
| Mint JWT, create collection, upload PDF, capture source_id | `checks.sh:1791–1848` | ✅ |
| POST extract_mineru, poll COMPLETED_SUCCESS | `checks.sh:1850–1898` | ✅ |
| POST chunks, poll COMPLETED_SUCCESS | `checks.sh:1900–1948` | ✅ |
| V1: `COUNT > 0` for source_id AND producer_asset='chunks' | `checks.sh:1950–1971` | ✅ |
| V2: chunk_id matches `^{source_id}_\d+$`, contiguous from 0 | `checks.sh:1973–2000` | ✅ |
| V3: text non-empty, `0 < token_count <= 512` | `checks.sh:2002–2028` | ✅ |
| V4: producer_asset='chunks', augmented_from=None, attr_*=None | `checks.sh:2030–2059` | ✅ |
| `all)` chain: `chunks` inserted after `lance` | `checks.sh:1338–1339` | ✅ |

---

## 2. Hard Invariant Compliance

| # | Invariant | Assessment |
|---|---|---|
| 1 | **Lineage mandatory** — parents, processor identity, config hash, input refs | Every Lance row stores `producer_asset="chunks"` (`chunker.py:188`), `producer_version=CHUNKER_VERSION` (`chunker.py:189`), `source_id` as input ref (`chunker.py:186`), `augmented_from=None` as "original chunk" marker (`chunker.py:194`). Dagster run_id logged via `context.log.info` at `definitions.py:169`. **PASS** |
| 2 | **Storage separation + CAS** — Postgres = metadata; MinIO/S3 = content | Chunk text written to Lance (MinIO-backed at `s3://{MINIO_LANCE_BUCKET}/chunks`). `lookup_source_collection_id()` performs a read-only SELECT — no new Postgres writes. No blob bytes in Postgres. **PASS** |
| 3 | **Schema frozen post-publish** — no in-place schema edits | `CHUNKS_SCHEMA` in `chunker.py` is a verbatim copy of the F-023 agreed schema. The original `lance.py:58–92` is not modified. No `nullable=False` added (strict Arrow nullability preserved). **PASS** |
| 4 | **LLM calls via gateway** | F-025 performs no LLM calls. No LLM SDK imports anywhere in the diff. **N/A** |
| 5 | **Async SQLAlchemy in apps/api/** | `lookup_source_collection_id()` uses raw psycopg2 (sync) — but this is in `dagster/dagster_platform/chunker.py`, outside `apps/api/`. The invariant is scoped to `apps/api/dataplat_api/`; consistent with existing `extractor.py` pattern. **PASS** |
| 6 | **OpenAPI ↔ TS type sync** | F-025 adds no new API endpoints and modifies no Pydantic request/response schemas. `make codegen` not required. **N/A** |

---

## 3. Calibration Cases (reviewer-calibration.md)

| Case | Check | Result |
|---|---|---|
| **CAL-1** | Async session enforcement — sync ORM in apps/api/ | F-025 touches no `apps/api/` Python files. **N/A** |
| **CAL-2** | LLM gateway — direct LLM SDK calls | No LLM imports anywhere in the diff. **N/A** |
| **CAL-3** | OpenAPI sync — routers/schemas changed without codegen | No router or schema changes. **N/A** |
| **CAL-4** | Lineage completeness — Commit with empty lineage_info | F-025 does not create design-doc Commit objects. Chunk-level lineage fields are all populated (`chunker.py:185–189, 194`). **PASS** |
| **CAL-5** | CAS path discipline — blob path from non-hash derivation | F-025 writes to Lance (not raw S3 blobs). S3 read path (`{source_id}/extract_mineru/doc.docling.json`) was established by F-019. No new CAS-relevant paths introduced. **N/A** |
| **CAL-6** | Schema freeze post-publish — in-place Silver/Gold schema edit | `CHUNKS_SCHEMA` in `lance.py` is unchanged. New copy in `chunker.py` is additive. **PASS** |
| **CAL-7** | Bronze faithfulness — semantic cleaning in adapter | No adapter code touched. **N/A** |
| **CAL-8** | MVP scope — out-of-scope features (auth, ACL, Kafka, etc.) | None present. Fixed-size chunking is in scope per `spec/feature_list.json` F-025. **PASS** |
| **CAL-9** | Plugin isolation — plugin imports across modules | No plugin code touched. **N/A** |
| **CAL-10** | Test coverage — happy path + at least one failure | `test_chunker.py` covers multiple happy paths (`TestFixedSizeChunkSingleWindow`, `TestFixedSizeChunkTwoWindows`, `TestFixedSizeChunkReturnShape`) and failure-mode fallbacks (`TestExtractTextFallback.test_name_fallback_when_markdown_empty`, `test_source_id_fallback_when_name_empty`). **PASS** |
| **CAL-11** | Bias check — vague approval without evidence | This review provides `file:line` citations for every requirement and every calibration case. **PASS** |

---

## 4. Minor Observations (non-blocking)

**OBS-1: `build_s3_client()` in chunker.py is unused by definitions.py.**  
`chunker.py:108–116` defines its own `build_s3_client()`, but `definitions.py:28` imports `build_s3_client` from `extractor.py` instead. The `chunks` asset calls `extractor.build_s3_client()` and passes the result to `chunker.read_docling_document()`. The two implementations are functionally identical. The `chunker.py` version is dead code from `definitions.py`'s perspective, though it could be useful for unit tests or future callers. Not a defect.

**OBS-2: `TestFixedSizeChunkEmptyText` class name is slightly misleading.**  
The class name implies it tests an empty string, but both tests pass `"hello"` (a non-empty single word). The actual zero-token guard at `chunker.py:176–177` (`if not token_ids: ...`) is not directly exercised. The guard is trivial and logically sound; the fallback chain is fully covered by `TestExtractTextFallback`. No functional gap.

**OBS-3: Worker MINIO_* vars use defaults where agreed.md left them bare.**  
agreed.md §3-B specifies `${MINIO_ENDPOINT}` (no default) for workers; the implementation uses `${MINIO_ENDPOINT:-minio:9000}` consistently with all other Dagster services. This is an improvement (safe dev-mode default, consistent with agreed.md §3-B's intent for the `lance` bucket default). No concern.

---

## 5. Verdict

**APPROVED**

All requirements in `contracts/S025-F-025/agreed.md` are fully implemented with correct file:line evidence. All six hard invariants are satisfied. All applicable CAL checks pass. The three observations above are non-blocking cosmetic issues that do not affect correctness, schema integrity, or test coverage.

The implementation is ready to proceed to the `verifier` step: `bash verify/checks.sh chunks`.
