# S025-F-025 — Mode A Review — feedback.md

**Verdict: CHANGES_REQUESTED**

One BLOCKER (internal version contradiction that would cause a bad Dockerfile pin), two HIGH
findings (factually incorrect schema claim that could propagate into a schema-breaking
implementation, and a missing prerequisite step that would cause verification to fail in a
clean environment), two MEDIUM findings (pattern inconsistency and ambiguous ordering), and
two NITs.

---

## Ground-truth verification results

Ran in running containers before writing this feedback.

| Check | Command | Result |
|---|---|---|
| `export_to_markdown()` exists in docling-core==2.77.0 | `dagster-webserver` container | ✅ Present — listed in `dir(DoclingDocument(...))` |
| `LanceTable.delete()` exists in lancedb 0.30.2 | `fastapi` container | ✅ Present — signature: `(self, where: str) -> DeleteResult` |
| `delete()` is a no-op when no rows match | live fastapi container against real table | ✅ `DeleteResult(num_deleted_rows=0, version=2)` |
| `pa.string()` nullable default | `fastapi` container (pyarrow==24.0.0) | ✅ `nullable=True` — **contradicts D10 claim** |
| `MINIO_LANCE_BUCKET` explicitly set on `fastapi` in docker-compose.dev.yml | grep docker-compose.dev.yml | ❌ Not present — works via Python default `str = "lance"` in config.py |

---

## Finding 1 — BLOCKER: Internal pyarrow version contradiction in R1

**Location:** §3-A versus §7 Risk R1.

§3-A correctly states:
> Add `lancedb==0.30.2 pyarrow==24.0.0 tiktoken==0.7.0` to the `pip install` line.

And OQ1 (§7, bottom) is marked RESOLVED:
> `apps/api/uv.lock` pins `pyarrow==24.0.0`. The Dagster Dockerfile will use the same version.

But Risk R1 says:
> *Mitigation: pin both to `0.30.2` and `14.0.2` respectively; CI will catch any future
> drift via `docker compose build`.*

`14.0.2` contradicts `24.0.0` everywhere else in the document. The running fastapi container
confirms `pyarrow.__version__ == '24.0.0'`. An implementer reading only the R1 mitigation
line (the most natural place to look for "what to pin") will write
`pyarrow==14.0.2` in the Dockerfile, which would install a different pyarrow than apps/api/
and likely break lancedb 0.30.2 compatibility.

**Required fix:** Replace `14.0.2` in R1 with `24.0.0` throughout. The final mitigation
sentence should read:
> *Mitigation: pin both to `lancedb==0.30.2` and `pyarrow==24.0.0` respectively; CI will
> catch any future drift via `docker compose build`.*

---

## Finding 2 — HIGH: D10 nullable claim is factually wrong and risks schema breakage

**Location:** §4 Design decision D10.

D10 states:
> `docling_refs` and `source_refs` are set to empty string `""` (not null) because the
> schema type is `pa.string()` (non-nullable in the schema definition) and pyarrow will
> reject null for a non-nullable field.

Ground-truth check in the running fastapi container (pyarrow==24.0.0):
```python
>>> pa.field('docling_refs', pa.string()).nullable
True
```

`pa.string()` is **nullable by default**. pyarrow will NOT reject `None` for this field.
The stated reason is factually incorrect.

The design choice to write `""` rather than `None` may still be acceptable (to avoid null
sentinel ambiguity in downstream SQL filters, for example), but the justification must be
corrected. As written, an implementer who reads D10 carefully might:
1. Add explicit `pa.field("docling_refs", pa.string(), nullable=False)` to CHUNKS_SCHEMA in
   `chunker.py`, making that schema incompatible with the existing Lance table created by
   F-023 (which used the default `nullable=True` schema). Arrow schema comparison is
   strict on nullability — this mismatch causes lancedb to raise on `create_table(..., exist_ok=True)`.
2. Or add runtime assertions rejecting `None` that are unnecessarily restrictive.

**Required fix:** Replace the justification. If using `""` is intentional, the reason should
be stated as a convention choice (e.g., "to maintain consistent non-null sentinel semantics
for string ref fields; `None` is also valid by schema but not used") rather than a false
schema-enforcement claim. The CHUNKS_SCHEMA copy in `chunker.py` must NOT add
`nullable=False` to any field that is `nullable=True` in the F-023 authoritative copy.

---

## Finding 3 — HIGH: Verification plan §5 V1 missing the extract_mineru prerequisite step

**Location:** §5 Verification plan, V1 "How checked" steps 1–4.

The proposed `chunks)` layer in checks.sh uploads a fresh source and immediately launches
the `chunks` asset:
> 1. Mint a JWT, upload a test PDF, capture `source_id`.
> 2. POST `/api/runs?asset=chunks&partition=src_{source_id}`.

But `chunks` reads `s3://documents/{source_id}/extract_mineru/doc.docling.json`.
For a freshly uploaded source, this file does not exist; it is produced by running
`extract_mineru` first. Because D9 explicitly removes the asset graph dependency
(`deps=[extract_mineru]` is not added), Dagster will NOT automatically trigger extraction
before chunking. The `chunks` asset will fail with an S3 NoSuchKey error (or equivalent)
on a source that was never extracted.

The `documents)` layer already demonstrates the correct pattern: upload → trigger
extract_mineru → poll to COMPLETED_SUCCESS → then run the downstream asset. The `chunks)`
layer must follow the same pattern.

**Required fix:** Insert steps 2–3 between the current steps 1 and 2:
```
1. Mint a JWT, upload a test PDF, capture `source_id`.
2. POST `/api/runs?asset=extract_mineru&source_ids=[{source_id}]`.
3. Poll until the extract_mineru backfill reaches `COMPLETED_SUCCESS`.
4. POST `/api/runs?asset=chunks&source_ids=[{source_id}]`.
5. Poll until the chunks backfill reaches `SUCCESS`.
6. [Lance query verification V1–V4]
```

---

## Finding 4 — MEDIUM: `chunks)` layer must include unit test step

**Location:** §5, "Unit tests (local, inside dagster-webserver container)" subsection.

The `extract)` layer in checks.sh runs unit tests as its very first step:
```bash
docker compose -f "$COMPOSE" exec -T dagster-webserver \
  python -m pytest /app/dagster/tests/test_extractor.py -q
```
This ensures the pure helpers are validated every time `bash checks.sh all` runs.

The proposed `chunks)` layer description lists the unit test invocation separately as a
standalone command block outside the layer body. If the tests are not inside `chunks)`,
they will only run when manually invoked and will not be part of `bash checks.sh all`.

**Required fix:** Make the unit test invocation the FIRST step of the `chunks)` case in
checks.sh, mirroring the `extract)` pattern:
```bash
  chunks)
    echo "--- chunks: unit tests for chunker.py helpers ---"
    docker compose -f "$COMPOSE" exec -T dagster-webserver \
      python -m pytest /app/dagster/tests/test_chunker.py -q \
      || { echo "FAIL: chunker unit tests failed"; exit 1; }
    echo "  chunker unit tests: OK"
    # ... E2E steps follow
```

---

## Finding 5 — MEDIUM: `all)` chain insertion point is ambiguous and likely wrong

**Location:** §2, `checks.sh` row; §6 Verification plan.

§2 states: "Add `chunks` to the `all)` chain after `extract`."

The current `all)` chain ends with:
```bash
bash "$0" extract     # F-019
bash "$0" documents   # F-020
bash "$0" lance       # F-023
```

Inserting "after `extract`" would produce `extract → chunks → documents → lance`.
This has two problems:
1. It visually suggests `chunks` precedes the `lance)` layer that verifies the Lance table
   was correctly initialised — a reader would naturally expect the writer (`chunks)`) after
   the table initialisation check (`lance)`).
2. The self-contained `chunks)` layer runs its own extract_mineru step (per Finding 3 fix),
   so position relative to `extract)` is irrelevant for correctness, but position after
   `lance)` makes the dependency chain (`lance` table must exist → `chunks` writes to it)
   legible in the script.

**Required fix:** Specify the insertion point explicitly as **after `lance`**:
```bash
bash "$0" lance       # F-023
bash "$0" chunks      # F-025  ← NEW
```
Update §2 to read: "Add `chunks` to the `all)` chain after `lance`."

---

## Finding 6 — NIT: §3-B premise is misleading — `fastapi` does NOT have `MINIO_LANCE_BUCKET` in docker-compose

**Location:** §3-B, first paragraph.

> The env var `MINIO_LANCE_BUCKET` is read by `chunker.py`'s `build_lance_storage_options()` to
> form the S3 URI `s3://{MINIO_LANCE_BUCKET}/chunks`. **It is already present on the `fastapi`
> service (set during F-023)** and in `.env` (`MINIO_LANCE_BUCKET=lance`).

`MINIO_LANCE_BUCKET` does not appear in the `fastapi` service's `environment:` block in
`docker/docker-compose.dev.yml`. The fastapi service works because Pydantic Settings
defines a Python default: `MINIO_LANCE_BUCKET: str = "lance"` in config.py. The env var
was never injected into docker-compose.

This misleads: a reader might think "why didn't F-023 add it to docker-compose for fastapi?"
and then question whether the Dagster addition is needed. The correct statement is: "The
fastapi service resolves `MINIO_LANCE_BUCKET` via its Pydantic Settings default (`"lance"`).
Dagster's `chunker.py` uses `os.environ` (no default), so explicit injection is required."

**Required fix:** Correct the premise in §3-B. No code change required.

---

## Finding 7 — NIT: Worker containers receive incomplete MINIO_* block

**Location:** §3-B.

The proposal adds `MINIO_LANCE_BUCKET` to `dagster-worker-cpu` and `dagster-worker-heavy`,
but these containers currently have `WORKER_PROFILE` and Postgres vars only — they have no
`MINIO_ENDPOINT`, `MINIO_ROOT_USER`, or `MINIO_ROOT_PASSWORD`. They run `sleep infinity`
and never execute assets (this is documented in docker-compose.dev.yml line 19–22).

Adding `MINIO_LANCE_BUCKET` to the workers is harmless now but creates an inconsistent
state: if a worker is ever activated, `chunker.py`'s `build_lance_storage_options()` would
fail on the missing `MINIO_*` vars before `MINIO_LANCE_BUCKET` matters.

**Required fix (two options acceptable):**
- Option A: Also add `MINIO_ENDPOINT`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` to the
  workers alongside `MINIO_LANCE_BUCKET` — complete parity with dagster-webserver/daemon.
- Option B: Add a comment noting the incomplete state and defer full MINIO_* parity to the
  sprint that activates the workers.

Either option is acceptable; the contract must make a decision.

---

## Non-findings (verified correct)

- **D1 — `cl100k_base` encoding loaded at module import**: Correct pattern; avoids
  per-chunk network fetch. R4 Dockerfile bake step is sound.
- **D2 — `export_to_markdown()` API**: ✅ Exists and works in docling-core==2.77.0 (verified).
- **D2 — Empty-doc fallback `doc.name`**: Correct safety net given F-019 stub behaviour.
- **D3 — `LanceTable.delete()` API**: ✅ Exists in lancedb 0.30.2; signature `(self, where: str) -> DeleteResult`; no-op on non-matching rows (verified).
- **D3 — `db.create_table("chunks", schema=CHUNKS_SCHEMA, exist_ok=True)` pattern**: Matches F-023 lance.py exactly. Safe.
- **D4 — chunk_id format `{source_id}_{seq}`**: Matches F-025 verification regex.
- **D5 — Idempotency via delete-before-insert**: Correct and sufficient. Lance delete is documented/verified as a no-op on empty results.
- **D7 — `lookup_source_collection_id()` raises `ValueError` on missing source**: Correct fail-fast behaviour; matches R5 guidance.
- **D8 — Use `fastapi` container for Lance queries in checks.sh**: Consistent with existing `lance)` layer pattern. Correct.
- **D9 — No `deps=[extract_mineru]`**: Design decision consistent with F-024 agreed.md D3. (Verification plan must account for this explicitly — see Finding 3.)
- **Invariant #1 — Lineage**: All required fields populated (producer_asset, producer_version, augmented_from=None, source_id). Compliant.
- **Invariant #2 — Storage separation**: Chunk text in Lance/MinIO; no blob in Postgres. Compliant.
- **Invariant #3 — Schema frozen**: CHUNKS_SCHEMA is copied unchanged from F-023; no schema edit. Compliant.
- **Invariant #5 — Async SQLAlchemy**: psycopg2 sync usage is in Dagster (not apps/api/); explicitly scoped out by the invariant. Compliant.
- **Invariant #6 — OpenAPI ↔ TS sync**: No new API endpoints. Not applicable.
- **Unit test coverage**: Six named tests covering edge cases adequately. The `test_fixed_size_chunk_max_tokens` test is important for regression safety on the 512-token boundary.
- **Dockerfile R4 bake step**: `RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"` is the correct mitigation for air-gapped builds.
- **Schema duplication R6**: Acknowledged with mitigation (cross-file comments). Acceptable for MVP.

---

## Summary

| # | Severity | Title | Fix |
|---|---|---|---|
| 1 | BLOCKER | pyarrow `14.0.2` in R1 contradicts `24.0.0` everywhere else | Change `14.0.2` → `24.0.0` in R1 |
| 2 | HIGH | D10 nullable claim is wrong; risks schema incompatibility | Correct justification; prohibit `nullable=False` in CHUNKS_SCHEMA copy |
| 3 | HIGH | Verification plan §5 V1 missing extract_mineru step | Add upload→extract_mineru→poll before chunks trigger |
| 4 | MEDIUM | Unit tests not inside `chunks)` layer body | Move unit test invocation into `chunks)` as first step |
| 5 | MEDIUM | `all)` insertion "after `extract`" should be "after `lance`" | Specify explicit final ordering in §2 |
| 6 | NIT | §3-B "already present on fastapi" is misleading | Correct premise: works via Python default, not docker-compose injection |
| 7 | NIT | Workers receive MINIO_LANCE_BUCKET without other MINIO_* vars | Add full MINIO_* block to workers, or document incomplete state |

Findings 1–3 are mandatory before agreement. Findings 4–5 must be addressed in agreed.md.
Findings 6–7 are recommended but may be deferred to a comment-only fix.
