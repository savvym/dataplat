# S025-F-025 — Chunking operator: proposed contract

## 1. What

Implement the `chunks` Dagster asset (currently a `raise NotImplementedError` stub from F-024) as a
real fixed-size token chunking operator. The asset reads the canonical DoclingDocument JSON written
by `extract_mineru` (F-019) from `s3://documents/{source_id}/extract_mineru/doc.docling.json`,
exports its text to Markdown, splits the result into ≤512-token chunks (tiktoken `cl100k_base`
encoding), and writes one row per chunk to the Lance global chunks table
(`s3://lance/chunks/chunks.lance/`) using `lancedb`. Each row is populated with `chunk_id =
"{source_id}_{seq}"` (0-indexed), non-null `text` and `token_count`, `source_id`,
`source_collection_id` (fetched from Postgres), `producer_asset = "chunks"`, a pinned
`producer_version`, and `created_at`/`updated_at`; all augmenter and `attr_*` columns are `null`.
The operation is idempotent: existing rows for the same `source_id + producer_asset` are deleted
before insertion. No new API endpoints or Postgres migrations are needed; this is a pure
Dagster-layer change plus infrastructure plumbing (new Python packages in the Dagster image, one
new env var in docker-compose).

---

## 2. Files changed / created

| Path | Action | Description |
|---|---|---|
| `dagster/dagster_platform/chunker.py` | **CREATE** | Pure helper module: `build_lance_storage_options()`, `read_docling_document()`, `extract_text_from_document()`, `fixed_size_chunk()`, `write_chunks_to_lance()`, `lookup_source_collection_id()`. No asset logic here. |
| `dagster/dagster_platform/definitions.py` | **EDIT** | Replace stub body of `chunks` asset (lines 121–133) with real logic that calls `chunker` helpers. Add import of `chunker` symbols. |
| `dagster/tests/test_chunker.py` | **CREATE** | Unit tests for the pure helpers in `chunker.py`: token splitting correctness, edge cases (empty text, single token, exactly-512-token window, text that produces a non-UTF8-safe boundary), chunk_id naming convention, `fixed_size_chunk` return shape. |
| `docker/dagster/Dockerfile` | **EDIT** | Add `lancedb==0.30.2 pyarrow==24.0.0 tiktoken==0.7.0` to the `pip install` line. Versions pinned to match `apps/api/` lock file: pyarrow==24.0.0 is what lancedb 0.30.2 uses in apps/api/; tiktoken 0.7.0 is the latest stable that ships the `cl100k_base` vocab file. Also add `RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"` to bake vocab into image (R4 mitigation). |
| `docker/docker-compose.dev.yml` | **EDIT** | Add `MINIO_LANCE_BUCKET: ${MINIO_LANCE_BUCKET:-lance}` to the `environment:` block of **four** services: `dagster-webserver`, `dagster-daemon`, `dagster-worker-cpu`, `dagster-worker-heavy`. |
| `verify/checks.sh` | **EDIT** | Add a new `chunks)` layer (end-to-end verification of the chunking pipeline). Add `chunks` to the `all)` chain after `extract`. |

---

## 3. Infrastructure changes

### 3-A. `docker/dagster/Dockerfile` — new packages

Current `pip install` line installs:
```
dagster==1.11.16 dagster-webserver==1.11.16 dagster-postgres==0.27.16 \
psycopg2-binary==2.9.10 boto3==1.37.38 docling-core==2.77.0 pytest==8.3.4
```

Three packages are absent and must be added:

| Package | Version | Why |
|---|---|---|
| `lancedb` | `0.30.2` | Write chunk rows to the Lance dataset; same version as in `apps/api/` |
| `pyarrow` | `24.0.0` | Required by lancedb; matches `apps/api/uv.lock` pin (resolves OQ1) |
| `tiktoken` | `0.7.0` | Fixed-size tokenisation; `cl100k_base` vocab |

The dagster bind-mount means code changes to `dagster/dagster_platform/` take effect on `docker
compose restart dagster-webserver dagster-daemon`; the Dockerfile change requires a one-time
`docker compose build dagster-webserver dagster-daemon dagster-worker-cpu dagster-worker-heavy`
followed by `up -d`.

### 3-B. `docker/docker-compose.dev.yml` — new env var

The env var `MINIO_LANCE_BUCKET` is read by `chunker.py`'s `build_lance_storage_options()` to
form the S3 URI `s3://{MINIO_LANCE_BUCKET}/chunks`. It is already present on the `fastapi` service
(set during F-023) and in `.env` (`MINIO_LANCE_BUCKET=lance`). It is **missing** from all four
Dagster services.

Add to each of `dagster-webserver`, `dagster-daemon`, `dagster-worker-cpu`, `dagster-worker-heavy`:

```yaml
MINIO_LANCE_BUCKET: ${MINIO_LANCE_BUCKET:-lance}
```

The default `lance` is safe: in production the `.env` override controls the value; in dev the
default matches the bucket created by the MinIO init container.

---

## 4. Design decisions

**D1 — Token budget and encoding.**
Fixed window = 512 tokens; encoding = `cl100k_base` (tiktoken). This matches design doc §4.2
("512-token fixed-size chunks"). `cl100k_base` is the GPT-3.5/GPT-4 encoding and is the practical
standard for embedding pipelines. The encoder is loaded once at module import time
(`tiktoken.get_encoding("cl100k_base")`) so that the BPE vocab file is fetched once per worker
process, not per chunk.

**D2 — Text extraction strategy.**
Call `doc.export_to_markdown()` on the `DoclingDocument` object (parsed via
`DoclingDocument.model_validate_json(json_str)`). This produces a linearised plain-text
representation that includes all body text nodes. If the result is empty (which may happen for
F-019's minimal extractor stub because it produces a near-empty document), fall back to `doc.name`
as the single chunk's text. This guarantees ≥1 chunk is written per source, which is required for
the `COUNT > 0` verification criterion to pass. The fallback text is clearly deterministic and
traceable via `producer_version`.

**D3 — Lance write mechanism.**
Use `lancedb` directly in `chunker.py` (no Dagster IO manager). Pattern mirrors `lance.py` in
`apps/api/` but reads from `os.environ` instead of `settings`:

```python
db = lancedb.connect(f"s3://{MINIO_LANCE_BUCKET}/chunks", storage_options=build_lance_storage_options())
table = db.create_table("chunks", schema=CHUNKS_SCHEMA, exist_ok=True)
```

`CHUNKS_SCHEMA` is duplicated into `chunker.py` (copy of the 24-field schema from
`apps/api/dataplat_api/storage/lance.py`). Reason: the Dagster container cannot import
`dataplat_api` (different package, different virtualenv). The schema is a pure constant with no
logic — duplication is safe; any future schema change touches both files together.

**D4 — chunk_id naming.**
`chunk_id = f"{source_id}_{seq}"` where `seq` is a 0-indexed integer incremented for each chunk
produced from the same source. Example: source_id=7 produces `"7_0"`, `"7_1"`, `"7_2"`. This
format matches the verification criterion regex and is stable across re-runs (same text → same
sequence → same IDs).

**D5 — Idempotency.**
Before `table.add(rows)`, call:
```python
table.delete(f"source_id = {source_id} AND producer_asset = 'chunks'")
```
This removes any previously written rows for this source+producer combination, then re-inserts
fresh rows. Lance `delete` is a safe operation even when no matching rows exist (no-op). This
makes the asset re-runnable without duplication.

**D6 — producer_version.**
Hard-coded constant `CHUNKER_VERSION = "0.1.0"` in `chunker.py`, assigned to `producer_version`
on every row. Future schema or algorithm changes bump this string. `producer_asset = "chunks"` (the
Dagster asset name).

**D7 — source_collection_id.**
Lance schema field `source_collection_id` is `NOT NULL` (int64). The value must come from the
`source` table in Postgres: `SELECT collection_id FROM source WHERE id = %s`. Use raw `psycopg2`
(same approach as `insert_document_variant` in `extractor.py`) with `PLATFORM_DB_URL` from env.
Failure to resolve the collection_id raises an exception (no silent default), since a wrong
collection_id would break downstream cross-collection queries.

**D8 — Lance query container in checks.sh.**
The new `chunks)` verification layer runs Lance queries from the `fastapi` container, which has
`lancedb` installed (since F-023). After F-025 adds `lancedb` to the Dagster image, the Dagster
container could also be used, but using `fastapi` is more reliable and consistent with the
existing `lance)` layer in `checks.sh`.

**D9 — Upstream dependency in Dagster asset graph.**
Do NOT add `deps=[extract_mineru]` to the `chunks` asset decorator. Keep the assets independent,
consistent with F-024's D3 decision (agreed.md §Design decisions D3). The caller (FastAPI
`POST /api/runs?asset=chunks`) is responsible for ensuring `extract_mineru` ran first. Adding an
explicit dependency would force Dagster to re-run `extract_mineru` whenever `chunks` is backfilled,
which is undesirable for re-chunk scenarios.

**D10 — Null handling for optional Lance fields.**
All `attr_*` columns (`attr_quality_score`, `attr_lang_code`, etc.) and augmenter columns
(`augmented_from`, `augmenter_id`, `augmenter_config_hash`) are set to `None` (Python) which
serialises to `null` in pyarrow. `docling_refs` and `source_refs` are set to empty string `""`
(not null) because the schema type is `pa.string()` (non-nullable in the schema definition) and
pyarrow will reject null for a non-nullable field. `attr_pii_categories` and
`attr_minhash_signature` are list types and are set to `None`.

---

## 5. Verification plan

The four verification criteria from `spec/feature_list.json` F-025:

### V1 — `COUNT > 0` in Lance for the test source_id

**How checked:**  
In the `chunks)` layer of `checks.sh`:
1. Mint a JWT, upload a test PDF, capture `source_id`.
2. POST `/api/runs?asset=chunks&partition=src_{source_id}`.
3. Poll until the Dagster run reaches `SUCCESS` status.
4. In the `fastapi` container, run a Python one-liner:
   ```bash
   docker compose exec -T fastapi python - <<'EOF'
   import lancedb, os, sys
   db = lancedb.connect(f"s3://{os.environ['MINIO_LANCE_BUCKET']}/chunks",
       storage_options={...})
   t = db.open_table("chunks")
   n = t.to_lance().count_rows(f"source_id = {SOURCE_ID} AND producer_asset = 'chunks'")
   assert n > 0, f"expected >0 rows, got {n}"
   print(f"V1 PASS: {n} rows")
   EOF
   ```

### V2 — chunk_id matches `{source_id}_{seq}` pattern

**How checked:**  
Extend the V1 query: retrieve the `chunk_id` column for the test source, assert all values match
the regex `^{source_id}_\d+$` and that the sequence is contiguous starting at 0. Python
`re.fullmatch` is sufficient.

### V3 — `text` non-null, `token_count` non-null and > 0

**How checked:**  
In the same query session, retrieve `text` and `token_count` columns for the test source. Assert:
- All `text` values are non-null and non-empty strings.
- All `token_count` values are non-null integers > 0.
- No single row has `token_count > 512` (window boundary respected).

### V4 — `augmented_from` is null; `attr_*` columns are null; `producer_asset = 'chunks'`

**How checked:**  
From the same rows:
- Assert `augmented_from` is `None` / null for every row.
- Assert `attr_quality_score`, `attr_lang_code`, `attr_embed_vector` are null for every row (spot-
  check three attr columns; the others follow the same code path).
- Assert `producer_asset == "chunks"` for every row.

### Unit tests (local, inside dagster-webserver container)

```bash
docker compose exec -T dagster-webserver python -m pytest dagster/tests/test_chunker.py -v
```

Tests cover:
- `test_fixed_size_chunk_empty_text` — fallback produces exactly 1 chunk.
- `test_fixed_size_chunk_single_window` — text ≤512 tokens produces 1 chunk.
- `test_fixed_size_chunk_two_windows` — text spanning two windows produces 2 chunks.
- `test_fixed_size_chunk_ids` — chunk_ids are `"{source_id}_0"`, `"{source_id}_1"`, etc.
- `test_fixed_size_chunk_token_count` — each chunk's `token_count` == len(tiktoken encode(text)).
- `test_fixed_size_chunk_max_tokens` — no chunk exceeds 512 tokens.

---

## 6. Invariant compliance

| Invariant | Requirement | This feature |
|---|---|---|
| **#1 Lineage** | Every commit records `parents[]`, processor identity, config hash, input refs | Every Lance row sets `producer_asset = "chunks"`, `producer_version = CHUNKER_VERSION`, `augmented_from = None` (original chunk, no parent), `source_id` (input ref). `docling_refs` and `source_refs` are set to `""` for now (not null; real values deferred to F-026 per design doc §4.2 note on progressive enrichment). The Dagster `run_id` is logged to the asset materialisation metadata. |
| **#2 Storage separation + CAS** | Metadata in Postgres; content in MinIO/S3. No blob bytes in Postgres. | Chunk text is stored in Lance (MinIO-backed S3). No new Postgres columns. The `source_collection_id` lookup is a read-only SELECT on existing Postgres data. |
| **#3 Schema frozen post-publish** | No in-place schema edits after publish | `CHUNKS_SCHEMA` in `chunker.py` is a copy of the agreed schema from F-023 (`lance.py`). No schema change. If schema must change in future, a new version is required. |
| **#4 LLM calls via gateway** | Never call LLM SDKs directly | F-025 performs no LLM calls. Not applicable. |
| **#5 Async SQLAlchemy from day one** | Every DB session in `apps/api/` is async | The `lookup_source_collection_id()` helper uses `psycopg2` (sync). This is in `dagster/dagster_platform/chunker.py`, outside `apps/api/`. The invariant explicitly scopes to `apps/api/`. Consistent with existing `insert_document_variant` in `extractor.py`. |
| **#6 OpenAPI ↔ TS type sync** | API schema change → `make codegen` | F-025 adds no new API endpoints and changes no existing request/response schemas. `make codegen` is not needed. |

---

## 7. Risks / open questions

**R1 — lancedb version conflict.**  
`lancedb==0.30.2` is pinned in `apps/api/requirements.txt` (or equivalent). The Dagster image will
install the same version. If `pyarrow` version bundled with lancedb 0.30.2 differs from what the
Dagster image currently has (none), there is no conflict. If in future `apps/api/` upgrades
lancedb, the Dockerfile must be updated in the same commit. *Mitigation*: pin both to `0.30.2` and
`14.0.2` respectively; CI will catch any future drift via `docker compose build`.

**R2 — Empty DoclingDocument from F-019 stub.**  
F-019's `build_docling_document()` produces a *minimal* valid DoclingDocument (agreed.md §3 for
F-019). `export_to_markdown()` on a near-empty document may return an empty string or only
whitespace. D2 (fallback to `doc.name`) ensures ≥1 chunk is written; `doc.name` is set to
`f"source_{source_id}"` in F-019. *Residual risk*: if `doc.name` is also empty, the fallback chunk
has empty text. Add an assertion in `chunker.py`: if `text.strip() == ""`, set text to
`f"source_{source_id}"` as a final fallback. This is purely a dev-mode safety net; real extraction
(F-026) will replace the stub.

**R3 — Lance `delete()` on non-existent rows.**  
Calling `table.delete(predicate)` when no matching rows exist is documented as a no-op in lancedb.
This was verified empirically during F-023. No risk.

**R4 — tiktoken network access in air-gapped environments.**  
tiktoken downloads the `cl100k_base` BPE vocabulary on first use. In CI/CD or air-gapped builds
the download may fail. *Mitigation*: call `tiktoken.get_encoding("cl100k_base")` in the Dockerfile
`RUN` step (via `python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"`) so the vocab
file is baked into the image layer. Add this to the Dockerfile.

**R5 — Postgres connection string.**  
`lookup_source_collection_id()` reads `PLATFORM_DB_URL` from env (same as `extractor.py`). If the
source_id does not exist in Postgres (e.g. data inconsistency), the SELECT returns no rows. The
helper should raise a descriptive `ValueError(f"source {source_id} not found in Postgres")` rather
than returning `None` silently, to surface data pipeline issues early.

**R6 — Lance table schema mismatch between containers.**  
After F-025, `CHUNKS_SCHEMA` exists in two places: `apps/api/dataplat_api/storage/lance.py` and
`dagster/dagster_platform/chunker.py`. A reviewer in a future sprint could update one and forget
the other. *Mitigation*: add a comment to both files referencing the other; note this as a known
duplication in `chunker.py`'s module docstring. A future refactor could extract the schema to a
shared PyPI package, but that is out of scope for MVP.

**OQ1 — pyarrow version: RESOLVED.**  
`apps/api/uv.lock` pins `pyarrow==24.0.0`. The Dagster Dockerfile will use the same version.
`lancedb==0.30.2` requires `pyarrow>=12`, so 24.0.0 is compatible (already proven in `apps/api/`
since F-023).
