# Sprint S054-F-054 — Proposed Contract
# DoclingDocIOManager: MinIO path layout, manifest.json, atomic failure semantics

**Sprint ID:** S054-F-054
**Feature:** F-054 (category: infra, P1)
**Author:** implementer
**Date:** 2026-06-05
**Revision:** 2
**Depends on:** F-019 ✓ (passes: true)

---

## §1 Goal

Introduce `DoclingDocIOManager` — a Dagster `IOManager` that owns all MinIO writes for the
`extract_mineru` asset — and wire it into `definitions.py`. Specifically, the IOManager must:

1. Write `doc.docling.json` and `manifest.json` (and, when applicable, image files under
   `images/`) to `s3://documents/{source_id}/extract_{name}/` in the correct path layout
   (design doc §4.3).
2. Guarantee that if any MinIO write fails mid-way, **no `document_variant` row is left in
   Postgres** (Option A atomic semantics — see §3.2 for the chosen approach and rationale).
3. Produce a `manifest.json` that satisfies invariant #1 (Lineage) by recording `source_refs`,
   processor identity, config hash, run ID, and version metadata (see §3.3 for the full schema).
4. Replace the existing `write_document_json` helper as the sole owner of MinIO writes for
   `extract_mineru`. The `insert_document_variant` Postgres call remains in the asset body but
   must execute **after** `handle_output()` returns.

This sprint does NOT change any `apps/api/` code.

---

## §2 Spec References

| Reference | Location | Summary |
|---|---|---|
| §4.3 MinIO layout | design doc lines 422–460 | Path layout, manifest.json, images/ subdirectory |
| §8.1 IOManager table | design doc line 751 | `DoclingDocIOManager` serves `extract_*`, `document`, `document_vlm_enriched` |
| CLAUDE.md invariant #1 | Lineage is mandatory | manifest.json must record `source_refs` + processor identity + config hash |
| CLAUDE.md invariant #2 | Storage separation + CAS | Blob bytes in MinIO; metadata in Postgres |
| CLAUDE.md invariant #5 | Async SQLAlchemy | Scoped to `apps/api/dataplat_api/`; Dagster uses sync psycopg2 — OK |
| F-019 contracts | `contracts/S019-*/agreed.md` | Established extractor helpers, CONFIG_HASH, DB insert pattern |

---

## §3 Hard Invariants & Decisions

### 3.1 OQ-1: Does mineru produce image bytes today?

**Answer: NO.**

Reading `dagster/dagster_platform/extractor.py` in full: `build_docling_document()` constructs a
minimal `DoclingDocument` with only `PageItem` entries — no pictures, no `ImageRef`, no image
blobs. The current extractor does not call into any real MinerU engine; it is a stub that
produces a schema-valid document skeleton. The `images/` directory is always empty for MVP.

**Consequence for this sprint:**
- `manifest.json` MUST include the `images` field but its value is `[]` for MVP.
- The IOManager MUST accept a `list[ImageBlob]` parameter in `DoclingDocOutput` and handle
  N > 0 images when later extractors supply them — but for MVP the list is always empty.
- The `images/` prefix under `s3://documents/{source_id}/extract_mineru/` is NOT created
  (empty directories do not exist in S3/MinIO). This is correct and expected.

### 3.2 OQ-4 / Atomic failure semantics — Option A chosen

**Decision: Option A — write-order with best-effort cleanup.**

Rationale for choosing Option A over Option B:

| Criterion | Option A (write-order + cleanup) | Option B (staging prefix + rename) |
|---|---|---|
| Simplicity | Simple — one code path | Requires N copies + N deletes |
| AtomicGuarantee | Soft (best-effort cleanup on failure) | Equally soft (S3 has no atomic rename) |
| "manifest LAST" idiom | Natural fit — manifest written last | Artificial staging adds complexity |
| Write amplification | 0 overhead on success | 2× writes on success |
| Alignment with F-019 | Extends existing pattern | Requires staging bucket/prefix logic |

**Write order within `handle_output()`:**

```
1. put_object: {source_id}/extract_{name}/doc.docling.json   ← first
2. put_object: {source_id}/extract_{name}/images/{i}.{ext}   ← zero iterations in MVP
3. put_object: {source_id}/extract_{name}/manifest.json      ← LAST
```

Manifest written LAST is the integrity sentinel: if a consumer sees manifest.json, all prior
objects are guaranteed to exist (barring a partial cleanup race, see §3.2.1 below).

**Partial cleanup mechanism:**

The IOManager maintains a local `list[str]` of keys successfully PUT in the current
`handle_output()` call (named `_written_keys` in the implementation). The MinIO write phase
(doc.docling.json → images/* → manifest.json) is wrapped in a single `try/except`. On any
exception during that phase, the handler iterates `_written_keys` and calls
`s3.delete_object` for each, then re-raises the original exception. The Postgres write
(`insert_document_variant`) is placed **outside and after** this try/except block — see §4.1
for the exact structure — so a Postgres failure cannot trigger MinIO cleanup.

`_written_keys` tracks only keys PUT in the current `handle_output()` call; cleanup cannot
affect objects written by a concurrent run on the same `source_id`. `manifest.json` is written
last, so a concurrent run's manifest is always unreachable by an earlier-failed run's cleanup.

**OQ-4 — Order of Postgres insert vs IOManager write (CLOSED):**

The asset body sequence is:

```python
# Step 1: build output object
output = DoclingDocOutput(doc_json=..., images=[], source_refs=[...])

# Step 2: return output — Dagster calls handle_output() HERE before any further
#          asset-body code executes.
#
# Because extract_mineru now has io_manager_key="docling_io", and returns
# DoclingDocOutput, the Dagster framework calls DoclingDocIOManager.handle_output()
# during yield/return processing. The line below (insert_document_variant) only
# executes AFTER handle_output() returns without raising.
```

Wait — the existing `extract_mineru` asset uses `return MaterializeResult(...)`. With an
`io_manager_key`, the asset must return the typed output value, and the framework calls
`handle_output()` at `return` time before proceeding further. There is no code after the
`return` statement in `extract_mineru`, so `insert_document_variant` cannot be placed there.

**Resolution:** The `insert_document_variant` call must move INTO `handle_output()` itself
(at the end, after all MinIO writes succeed), OR be called via a helper passed into
`DoclingDocOutput`. The cleanest approach is: the `DoclingDocOutput` dataclass carries all the
information needed to write both MinIO and Postgres, and `handle_output()` owns both operations
in sequence:

```
handle_output() sequence:
  1. Write doc.docling.json to MinIO
  2. Write images/* to MinIO (zero iterations for MVP)
  3. Write manifest.json to MinIO (LAST)
  4. Call insert_document_variant()  ← Postgres write, AFTER all MinIO writes succeed
  5. Emit add_output_metadata()
```

On any exception in steps 1–3: best-effort cleanup of _written_keys, re-raise.
Step 4 is never reached if step 3 raised. Postgres row is never written on MinIO failure.

On exception in step 4 (Postgres): MinIO objects already written stay written. A re-run
(idempotent via `ON CONFLICT DO NOTHING`) will overwrite them and re-attempt the DB insert.
This is acceptable: storage without a Postgres row is non-canonical but not corrupt.

**This means `insert_document_variant` is REMOVED from the asset body in `definitions.py`
and its logic is called from inside `handle_output()` instead.**

### 3.3 OQ-2: How does the IOManager get the source's sha256 for `source_refs`?

**Answer:** The asset body queries Postgres for `source.sha256` before constructing
`DoclingDocOutput`. The `sha256` value is passed in via `DoclingDocOutput.source_refs`.

The `Source` model (`apps/api/dataplat_api/db/models.py:93`) has `sha256: Mapped[str]`. In
the Dagster layer, a new helper `fetch_source_sha256(source_id: int) -> str` uses psycopg2
(same as `insert_document_variant`) to `SELECT sha256 FROM source WHERE id = %s`. This
follows the established pattern in `extractor.py`.

The `source_refs` entry is thus:

```python
{
    "bucket": "sources",
    "key": f"sources/{source_id}/original.pdf",
    "sha256": sha256_from_db   # hex string from source.sha256
}
```

Note: the key includes the `sources/` path prefix (verified against F-011 pattern in
`extractor.py:read_pdf_bytes` — Key is `sources/{source_id}/original.pdf`).

### 3.4 OQ-3: Asset output pattern

**Decision:** The asset returns a typed `DoclingDocOutput` dataclass and `handle_output()`
performs ALL writes (both MinIO and Postgres). This is the cleanest atomic-failure boundary.

The `DoclingDocOutput` dataclass:

```python
@dataclass
class DoclingDocOutput:
    doc_json: str                    # DoclingDocument JSON string
    images: list[ImageBlob]          # Empty list for MVP
    source_refs: list[SourceRef]     # [{bucket, key, sha256}] — one entry for MVP
    source_id: int                   # For the Postgres insert
    page_count: int                  # For the Postgres insert
    extractor_name: str              # e.g. "mineru"
    dagster_run_id: str              # context.run_id
```

Helper types (pure dataclasses, no external deps):

```python
@dataclass
class ImageBlob:
    filename: str    # e.g. "0.png"
    data: bytes

@dataclass
class SourceRef:
    bucket: str      # e.g. "sources"
    key: str         # e.g. "sources/42/original.pdf"
    sha256: str      # hex string from source table
```

The asset body (extract_mineru) no longer calls `write_document_json` or
`insert_document_variant` directly. It constructs and returns `DoclingDocOutput`.

### 3.5 manifest.json schema (proposed, version 1)

```json
{
  "schema_version": 1,
  "extractor_name": "mineru",
  "extractor_version": "0.1.0",
  "config_hash": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
  "dagster_run_id": "<UUID>",
  "created_at": "2026-06-05T12:34:56.789Z",
  "source_refs": [
    {
      "bucket": "sources",
      "key": "sources/42/original.pdf",
      "sha256": "<hex>"
    }
  ],
  "images": []
}
```

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `int` | Always `1` for MVP; enables future evolution |
| `extractor_name` | `str` | Mirrors `EXTRACTOR_NAME` constant |
| `extractor_version` | `str` | Mirrors `EXTRACTOR_VERSION` constant |
| `config_hash` | `str` | sha256 of canonical operator config JSON; mirrors `CONFIG_HASH` constant |
| `dagster_run_id` | `str` | `context.run_id` at materialization time |
| `created_at` | `str` | ISO-8601 UTC timestamp, `datetime.now(timezone.utc).isoformat()` |
| `source_refs` | `list[obj]` | Each entry: `{bucket, key, sha256?}`; one entry for MVP |
| `images` | `list[str]` | Relative paths of image files written; `[]` for MVP |

**Lineage compliance (invariant #1):**
- `source_refs` records the input object (content addressed by sha256 + S3 URI).
- `extractor_name` + `extractor_version` + `config_hash` = processor identity.
- `dagster_run_id` ties the materialization to the Dagster run.

**Cross-table split:**
- `manifest.json` carries: `source_refs`, `extractor_name`, `extractor_version`, `config_hash`,
  `dagster_run_id`, `created_at`, `schema_version`, `images`.
- `document_variant` (Postgres) carries: `source_id`, `extractor_name`, `extractor_version`,
  `config_hash`, `storage_prefix`, `page_count`, `image_count`, `is_canonical`, `dagster_run_id`.
- Both records exist post-success; they are cross-referenceable via `dagster_run_id`.

---

## §4 Files Changed

| File | Action | Notes |
|---|---|---|
| `dagster/dagster_platform/docling_io_manager.py` | **CREATE** | New IOManager; `DoclingDocOutput`, `ImageBlob`, `SourceRef` dataclasses; `DoclingDocIOManager` class |
| `dagster/dagster_platform/extractor.py` | **MODIFY** | Add `fetch_source_sha256(source_id)` helper; retain `write_document_json` as an **internal** helper (still called by the IOManager internally; deprecated from asset body use); remove `insert_document_variant` from its current import path — it moves to be called from IOManager only |
| `dagster/dagster_platform/definitions.py` | **MODIFY** | Import `DoclingDocIOManager` and `DoclingDocOutput`; remove `write_document_json` and `insert_document_variant` from the `extract_mineru` asset body; asset returns `DoclingDocOutput`; add `io_manager_key="docling_io"` to `@asset`; register `docling_io` in `Definitions(resources=...)` |
| `dagster/tests/test_docling_io_manager.py` | **CREATE** | Unit tests T1–T8; no live MinIO/Postgres |

**No changes to `apps/api/`.**

### §4.1 Detailed change notes per file

#### `docling_io_manager.py` (new)

Top-level contents:
- `@dataclass class SourceRef`
- `@dataclass class ImageBlob`
- `@dataclass class DoclingDocOutput`
- `def _build_s3_client() -> Any` (mirrors `hf_dataset_io_manager._build_s3_client()`)
- `def _build_manifest(output: DoclingDocOutput, created_at: str) -> bytes` — pure function,
  builds the JSON manifest bytes from `DoclingDocOutput`. Kept separate so it is unit-testable
  without any S3/IOManager machinery.
- `class DoclingDocIOManager(IOManager)`:
  - `handle_output(context: OutputContext, obj: DoclingDocOutput) -> None`
  - `load_input(context: InputContext) -> None` — raises `NotImplementedError`

`handle_output()` algorithm:

```
1. Extract source_id and extractor_name from obj.
2. Build prefix = f"{obj.source_id}/extract_{obj.extractor_name}"
3. Build s3 client from MINIO_* env.
4. _written_keys: list[str] = []

5. MinIO write phase — inside cleanup try/except:
   try:
       a. key = f"{prefix}/doc.docling.json"
          s3.put_object(Bucket=DOCUMENTS_BUCKET, Key=key, Body=obj.doc_json.encode("utf-8"),
                        ContentType="application/json")
          _written_keys.append(key)
       b. For (i, img) in enumerate(obj.images):   ← zero iterations for MVP
          key = f"{prefix}/images/{img.filename}"
          s3.put_object(Bucket=DOCUMENTS_BUCKET, Key=key, Body=img.data)
          _written_keys.append(key)
       c. manifest_bytes = _build_manifest(obj, datetime.now(timezone.utc).isoformat())
          key = f"{prefix}/manifest.json"           ← LAST
          s3.put_object(Bucket=DOCUMENTS_BUCKET, Key=key, Body=manifest_bytes,
                        ContentType="application/json")
          _written_keys.append(key)
   except Exception:
       for key in _written_keys:
           try: s3.delete_object(Bucket=DOCUMENTS_BUCKET, Key=key)
           except Exception as del_exc: logger.warning("cleanup delete failed key=%r: %s", key, del_exc)
       raise   # re-raise original; no Postgres write has happened

6. Postgres write — OUTSIDE the MinIO cleanup block; propagates naturally:
   insert_document_variant(
       source_id=obj.source_id,
       page_count=obj.page_count,
       run_id=obj.dagster_run_id,
   )

7. context.add_output_metadata(...)
```

The cleanup handler fires **only** for exceptions in the MinIO write phase (steps 5a–5c).
`insert_document_variant` (step 6) is structurally outside the try/except, so a Postgres failure
cannot trigger MinIO cleanup. MinIO objects written in step 5 remain; on retry, `put_object`
overwrites them idempotently and `ON CONFLICT DO NOTHING` in `insert_document_variant` handles
the row idempotency. This is the intentional retry story described in §3.2.

#### `extractor.py` (modify)

- **ADD:** `fetch_source_sha256(source_id: int) -> str` — psycopg2 helper, same pattern as
  `insert_document_variant`. Executes `SELECT sha256 FROM source WHERE id = %s`. Raises
  `RuntimeError` if no row found.
- **KEEP:** `write_document_json` — retained as a module-level function but no longer called
  from the asset body. It is now an internal helper; `DoclingDocIOManager.handle_output()`
  calls the equivalent inline logic directly (does NOT call this wrapper, to avoid the circular
  import that would arise from `docling_io_manager.py` importing `extractor.py`). The function
  is left in place to avoid breaking any other callers and to maintain backwards-compat.
- **KEEP:** `insert_document_variant` — called from `handle_output()` in the IOManager.
  It is still imported by `definitions.py` indirectly (via the IOManager), but the **asset
  body no longer calls it directly**. The import in `definitions.py` of `insert_document_variant`
  is removed.
- **NO OTHER CHANGES** to extractor.py helpers.

#### `definitions.py` (modify)

- Remove `write_document_json` and `insert_document_variant` from the `from dagster_platform.extractor import ...` block.
- Add `from dagster_platform.docling_io_manager import DoclingDocIOManager, DoclingDocOutput`.
- Add `from dagster_platform.extractor import fetch_source_sha256` to the extractor import.
- Modify `@asset(...)` for `extract_mineru`: add `io_manager_key="docling_io"`, change return
  type annotation to `DoclingDocOutput`.
- Rewrite `extract_mineru` asset body:
  1. Parse source_id.
  2. Build s3 client.
  3. Read PDF bytes.
  4. Estimate page count.
  5. Build DoclingDocument JSON.
  6. Fetch source sha256 from Postgres.
  7. Construct and **return** `DoclingDocOutput(...)`.
  (Steps 6–end of materialization [MinIO writes + Postgres insert] happen in `handle_output()`.)
- Add `"docling_io": DoclingDocIOManager()` to `Definitions(resources=...)`.
- Update asset description docstring to reference F-054.

---

## §5 Design — IOManager Class Shape

### 5.1 Class skeleton

```python
# dagster/dagster_platform/docling_io_manager.py

from __future__ import annotations
import hashlib, json, logging, os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import boto3
from dagster import InputContext, IOManager, MetadataValue, OutputContext

from dagster_platform.extractor import (
    CONFIG_HASH, DOCUMENTS_BUCKET, EXTRACTOR_NAME, EXTRACTOR_VERSION,
    insert_document_variant,
)

logger = logging.getLogger(__name__)


@dataclass
class SourceRef:
    bucket: str
    key: str
    sha256: str

@dataclass
class ImageBlob:
    filename: str
    data: bytes

@dataclass
class DoclingDocOutput:
    doc_json: str
    images: list[ImageBlob]
    source_refs: list[SourceRef]
    source_id: int
    page_count: int
    extractor_name: str
    dagster_run_id: str


def _build_s3_client() -> Any:
    endpoint = os.environ["MINIO_ENDPOINT"]
    return boto3.client(
        "s3",
        endpoint_url=f"http://{endpoint}",
        aws_access_key_id=os.environ["MINIO_ROOT_USER"],
        aws_secret_access_key=os.environ["MINIO_ROOT_PASSWORD"],
    )


def _build_manifest(obj: DoclingDocOutput, created_at: str) -> bytes:
    manifest = {
        "schema_version": 1,
        "extractor_name": obj.extractor_name,
        "extractor_version": EXTRACTOR_VERSION,
        "config_hash": CONFIG_HASH,
        "dagster_run_id": obj.dagster_run_id,
        "created_at": created_at,
        "source_refs": [
            {"bucket": ref.bucket, "key": ref.key, "sha256": ref.sha256}
            for ref in obj.source_refs
        ],
        "images": [img.filename for img in obj.images],
    }
    return json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")


class DoclingDocIOManager(IOManager):
    def handle_output(self, context: OutputContext, obj: DoclingDocOutput) -> None:
        ...  # see §3.2 / §4.1 for the full algorithm

    def load_input(self, context: InputContext) -> None:
        raise NotImplementedError(
            "DoclingDocIOManager.load_input() is not implemented. "
            "Downstream processors read documents from MinIO directly."
        )
```

### 5.2 Key layout produced by IOManager

For `source_id=42`, `extractor_name="mineru"`:

```
s3://documents/
  42/
    extract_mineru/
      doc.docling.json      ← written first
      images/               ← only if len(obj.images) > 0 (empty in MVP)
        0.png
        1.jpg
      manifest.json         ← written LAST
```

Keys:
- `42/extract_mineru/doc.docling.json`
- `42/extract_mineru/images/0.png`  (zero occurrences in MVP)
- `42/extract_mineru/manifest.json`

### 5.3 manifest.json example (MVP, source_id=42)

```json
{
  "schema_version": 1,
  "extractor_name": "mineru",
  "extractor_version": "0.1.0",
  "config_hash": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
  "dagster_run_id": "abc123-deadbeef-...",
  "created_at": "2026-06-05T12:34:56.789012+00:00",
  "source_refs": [
    {
      "bucket": "sources",
      "key": "sources/42/original.pdf",
      "sha256": "e3b0c44298fc1c149afb..."
    }
  ],
  "images": []
}
```

---

## §6 Test Matrix

All tests live in `dagster/tests/test_docling_io_manager.py`. Run inside `dagster-webserver`
container:

```
docker compose exec -T dagster-webserver python -m pytest /app/dagster/tests/test_docling_io_manager.py -v
```

| Test ID | Spec Verification | Description |
|---|---|---|
| **T1** | V1 (spec #1) | Happy path: `handle_output()` with mocked S3; assert `put_object` called for `doc.docling.json` AND `manifest.json` at the correct keys; assert the `Body` argument of the `doc.docling.json` `put_object` call equals `obj.doc_json.encode('utf-8')` (catches body-swap bugs; mirrors `_get_put_object_bodies()` pattern from `test_hf_dataset_io_manager.py`) |
| **T2** | V1 (spec #1) | Verify manifest.json key is `{source_id}/extract_mineru/manifest.json` (not under any other prefix) |
| **T3** | V2 (spec #2) | Inject S3 failure on 2nd call (manifest.json write fails); assert `delete_object` called for `doc.docling.json` (cleanup) AND `insert_document_variant` NOT called |
| **T4** | V2 (spec #2) | Inject S3 failure on 1st call (doc.docling.json write fails); assert no `delete_object` needed (nothing was written), `insert_document_variant` NOT called |
| **T5** | V3 (spec #3) + V4 (invariant #1) | Read back manifest bytes; assert valid JSON; assert all required keys present with correct types and values (`extractor_name`, `extractor_version`, `config_hash`, `dagster_run_id`, `source_refs[0].sha256`, `schema_version==1`, `images==[]`, `created_at` parseable as ISO-8601) |
| **T6** | V5 (no cross-source bleed) | Call `handle_output()` twice with two different `source_ids`; assert the two sets of `put_object` keys are disjoint and each is namespaced to the correct `source_id` prefix |
| **T7** | V6 (N images support) | Construct `DoclingDocOutput` with 2 `ImageBlob` entries using non-empty synthetic bytes (e.g. `data=b'\x89PNG\r\n'`); assert `put_object` called for both image keys; assert that the `Body` argument of each image `put_object` call matches `img.data` (catches "correct key, wrong body" bugs); assert manifest `images` list has 2 entries; assert manifest written AFTER both image writes |
| **T8** | V7 (re-materialization idempotency) | Call `handle_output()` twice for same source_id; assert `put_object` called **4 times total** (2 files × 2 invocations: doc.docling.json + manifest.json per call); assert no error raised; assert `insert_document_variant` called twice (idempotent via `ON CONFLICT DO NOTHING`). Note: if/when MVP is extended with N > 0 images, T7 will cover the image-path count; T8 stays at 4 to anchor the zero-image contract. |

### 6.1 Test helper patterns

Follow `test_hf_dataset_io_manager.py` patterns:

```python
def _make_output() -> DoclingDocOutput:
    return DoclingDocOutput(
        doc_json='{"schema_name": "DoclingDocument", "name": "source_42"}',
        images=[],
        source_refs=[SourceRef(bucket="sources", key="sources/42/original.pdf",
                               sha256="abc123")],
        source_id=42,
        page_count=1,
        extractor_name="mineru",
        dagster_run_id="test-run-uuid",
    )

def _mock_output_context(partition_key: str = "src_42") -> MagicMock:
    ctx = MagicMock()
    ctx.log = MagicMock()
    ctx.add_output_metadata = MagicMock()
    ctx.has_partition_key = True
    ctx.partition_key = partition_key
    return ctx
```

Mock env vars:
```python
monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
monkeypatch.setenv("MINIO_ROOT_USER", "testuser")
monkeypatch.setenv("MINIO_ROOT_PASSWORD", "testpass")
monkeypatch.setenv("PLATFORM_DB_URL", "postgresql://test:test@db/test")
```

Patch `boto3.client` and `dagster_platform.docling_io_manager.insert_document_variant`.

---

## §7 Verification Commands

```bash
# 1. Smoke — must stay green (no regression)
bash verify/checks.sh smoke

# 2. Unit tests — new file passes
docker compose -f docker/docker-compose.dev.yml exec -T dagster-webserver \
  python -m pytest /app/dagster/tests/test_docling_io_manager.py -v

# 3. Existing extractor tests — must stay green
docker compose -f docker/docker-compose.dev.yml exec -T dagster-webserver \
  python -m pytest /app/dagster/tests/test_extractor.py -q

# 4. Linting — no new ruff errors in dagster/
docker compose -f docker/docker-compose.dev.yml exec -T dagster-webserver \
  python -m ruff check /app/dagster/

# 5. Type checking — no new mypy errors
docker compose -f docker/docker-compose.dev.yml exec -T dagster-webserver \
  python -m mypy /app/dagster/dagster_platform/ --ignore-missing-imports

# 6. Invariant grep — no direct SDK imports outside allowed paths
# (T-INV in test_llm_gateway_invariant.py covers this; re-run via backend layer)
bash verify/checks.sh backend

# 7. End-to-end extract layer — spec verification #1 via live stack
bash verify/checks.sh extract
# After the above completes successfully:
# V1: manually verify MinIO has both doc.docling.json AND manifest.json at
#     s3://documents/{source_id}/extract_mineru/
# V3: manually read manifest.json and assert required keys present

# 8. No OpenAPI diff from this sprint (no apps/api changes)
```

---

## §8 Migration / Backwards-Compat Notes

**No database migration is required.** The `document_variant` table schema is unchanged.
`insert_document_variant` continues to write the same 9 columns with the same logic.

**Backwards compat with existing MinIO objects:** Any `doc.docling.json` already written by
F-019 (before this sprint) has no corresponding `manifest.json`. Those objects are not corrupt;
they simply predate the IOManager. On re-materialization, `handle_output()` overwrites
`doc.docling.json` and writes `manifest.json` for the first time. The `ON CONFLICT DO NOTHING`
in `insert_document_variant` ensures the Postgres row is not duplicated.

**Existing import compat:** `write_document_json` remains in `extractor.py` (not removed).
Any future code that imports it still works. The asset body in `definitions.py` simply no
longer calls it.

**Breaking change:** `extract_mineru` in `definitions.py` changes its return type from
`MaterializeResult` to `DoclingDocOutput` and gains `io_manager_key="docling_io"`. This is an
internal Dagster wiring change — no external API surface is affected. The `extract_mineru` asset
key, partition definition, and sensor names are all preserved unchanged.

---

## §9 Risk Register

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | Partial cleanup leaves orphaned MinIO objects if `delete_object` raises | LOW | Logged as WARNING; orphans are non-canonical (no Postgres row); future compaction job can clean them. Best-effort is correct for MVP. |
| R2 | `insert_document_variant` inside IOManager breaks the "IOManager should not own DB writes" principle | LOW | Design doc §8.1 explicitly assigns DB row writing to this IOManager's scope. `HFDatasetIOManager` already sets the precedent (`update_dataset_row` is called from inside `handle_output()`). |
| R3 | `definitions.py` Definitions block (lines 789–808 of current file) appears syntactically incomplete — `return SkipReason(...)` from `chunks_notification_sensor` is followed directly by `jobs=[...]` which is not inside a `Definitions(...)` call. There may be a truncation artifact in the current file. | HIGH | Implementer MUST read `definitions.py` fully and reconstruct the correct `Definitions(...)` block before adding the new resource. Do not assume the file as-read is complete. |
| R4 | `context.add_output_metadata` outside the Dagster test harness raises | MEDIUM | Use `MagicMock()` for `context` in all unit tests (established pattern in `test_hf_dataset_io_manager.py`). |
| R5 | `fetch_source_sha256` fails at test time because `PLATFORM_DB_URL` is not available | LOW | Patch `dagster_platform.docling_io_manager.insert_document_variant` AND provide a `DoclingDocOutput` with `source_refs` already populated (sha256 passed in from asset body, not looked up in IOManager). |
| R6 | `manifest.json` `created_at` timezone representation — some consumers may expect UTC `Z` suffix, others `+00:00` | LOW | Use `datetime.now(timezone.utc).isoformat()` which produces `+00:00`; document that this is ISO-8601 compliant. If `Z` is preferred, use `.replace("+00:00", "Z")`. State explicitly in implementation. |
| R7 | `EXTRACTOR_VERSION` imported from `extractor.py` into `docling_io_manager.py` — circular import risk if `extractor.py` imports from `docling_io_manager.py` | LOW | `extractor.py` does NOT import from `docling_io_manager.py`. The import direction is one-way: `docling_io_manager` → `extractor`. No circular dependency. |
| R8 | The `DOCUMENTS_BUCKET` constant is defined in `extractor.py` as `"documents"`. Redeclaring it in `docling_io_manager.py` creates a silent shadow that would ignore future changes to the value in `extractor.py`. | LOW | **Resolved in §5.1 skeleton:** the local redeclaration has been removed. `DOCUMENTS_BUCKET` is imported from `extractor.py` only; no local override. |
| R9 | Process crash (OOM, SIGKILL, container restart) between MinIO writes leaves orphaned partial objects | LOW | No cleanup possible — `_written_keys` exception handler never fires on SIGKILL. Acceptable for MVP — same exposure as R1 (partial cleanup failure). Future compaction job handles both. |
| R10 | Concurrent Dagster runs for the same `source_id` | LOW | Safe by construction: `_written_keys` tracks only keys PUT in the current `handle_output()` call; cleanup cannot affect objects written by a concurrent run. `manifest.json` written last means a concurrent run's manifest is always unreachable by an earlier-failed run's cleanup. MinIO `put_object` is last-writer-wins; `ON CONFLICT DO NOTHING` ensures only one Postgres row survives. |

---

## §10 DoD Checklist

- [ ] `contracts/S054-F-054/agreed.md` exists and every item in it is addressed.
- [ ] `dagster/dagster_platform/docling_io_manager.py` created; passes `ruff check` and `mypy`.
- [ ] `dagster/dagster_platform/extractor.py` modified: `fetch_source_sha256` added; no existing tests broken.
- [ ] `dagster/dagster_platform/definitions.py` modified: `docling_io` registered as resource; `extract_mineru` returns `DoclingDocOutput`.
- [ ] `dagster/tests/test_docling_io_manager.py` created; T1–T8 pass inside `dagster-webserver` container.
- [ ] `dagster/tests/test_extractor.py` passes unchanged.
- [ ] `bash verify/checks.sh smoke` exits 0.
- [ ] `bash verify/checks.sh extract` exits 0.
- [ ] `bash verify/checks.sh backend` exits 0 (no ruff/mypy regressions in apps/api).
- [ ] MinIO at `s3://documents/{source_id}/extract_mineru/` contains both `doc.docling.json` AND `manifest.json` after a successful extraction.
- [ ] `manifest.json` parses as valid JSON and contains all keys listed in §3.5.
- [ ] `manifest.json` is absent (or cleaned up) when the MinIO write fails mid-way.
- [ ] No `document_variant` row is written when `handle_output()` raises.
- [ ] `feature_list.json` F-054 `passes` flipped to `true`.
- [ ] `claude-progress.txt` closing entry appended.
- [ ] Git commit pushed with descriptive message referencing S054-F-054.

---

## §11 Open Questions

All open questions from the sprint brief have been resolved above. No residual OQs.

**Summary of resolutions:**

| OQ | Resolution |
|---|---|
| OQ-1: Does mineru emit image bytes today? | No. `images: []` for MVP. IOManager supports N images via `ImageBlob` list. |
| OQ-2: How does IOManager get source sha256? | Asset body calls `fetch_source_sha256(source_id)` (new helper in `extractor.py`) and passes it in `DoclingDocOutput.source_refs`. |
| OQ-3: Asset output pattern? | Asset returns `DoclingDocOutput`; IOManager does all MinIO + Postgres writes. |
| OQ-4: Order of Postgres insert vs IOManager write? | `insert_document_variant` called from `handle_output()` AFTER all MinIO writes succeed, and OUTSIDE the MinIO cleanup try/except (step 6 in revised §4.1 algorithm). Asset body no longer calls it. |

---

## Round-1 round-trip

Rev-2 folds in all three required changes and all four NITs from `feedback.md`.

| Finding | Disposition | Location in rev-2 |
|---|---|---|
| **b1** (blocking) | Folded | §4.1 restructured: steps 5a–5c inside `try/except`; `insert_document_variant` moved to step 6 **outside** the cleanup block; `context.add_output_metadata` becomes step 7. Item 7 from rev-1 deleted (now self-evident from structure). §3.2 cleanup note rewritten to reference §4.1 structure rather than re-stating it inline. |
| **M1** (major) | Folded | §5.1 skeleton: local `DOCUMENTS_BUCKET = "documents"` redeclaration removed; import-only. R8 in §9 updated to describe the resolution rather than just the problem. |
| **M2** (major) | Folded | §6 T8: "6 times total" → "4 times total"; clarifying note added: "T7 will cover image-path count; T8 stays at 4 to anchor the zero-image contract." |
| **NIT-1** | Folded | §6 T7: non-empty synthetic bytes (`b'\x89PNG\r\n'`) specified; `Body` argument assertion added for each image `put_object` call. |
| **NIT-2** | Folded | §9: R9 added — process crash between MinIO writes leaves orphaned partial objects; LOW; same mitigation note as R1. |
| **NIT-3** | Folded | §3.2 cleanup note (concurrent-run safety explicitly stated); §9 R10 added with the full statement from feedback. |
| **NIT-4** | Folded | §6 T1: `Body` assertion added for `doc.docling.json` `put_object` call (`== obj.doc_json.encode('utf-8')`). |
