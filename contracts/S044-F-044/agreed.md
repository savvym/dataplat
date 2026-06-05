# Sprint S044-F-044 — Proposed Contract

**Feature**: F-044 — `HFDatasetIOManager` uploads `dataset_infos.json`, updates dataset row to `status='done'` with `sample_count` and `size_bytes`  
**Depends on**: F-043 (`passes: true`)  
**Sprint directory**: `contracts/S044-F-044/`  
**Author**: leader (inline)  
**Date**: 2026-06-04  
**Revision**: 2 (Round 2 — addresses M1/M2/L1/L2/NIT-1/NIT-2/NIT-3)

---

## Round 2 Changes

Addressing every finding from `contracts/S044-F-044/feedback.md` (reviewer Mode A, CHANGES_REQUESTED):

- **M1** — Added `AND status = 'pending'` to the `UPDATE` predicate in `update_dataset_row()` SQL (§3 Step 5). Corrected the false "no-op" idempotency claim in §3.6 and OQ-6 to accurately describe a 0-row UPDATE on re-run once the row is `'done'`.
- **M2** — Replaced "F-043's existing 14 tests MUST NOT be modified" in §2 (`dagster/tests/test_hf_dataset_io_manager.py` row) with the correct statement: exactly one test (`test_handle_output_total_four_objects`, lines ~220–227) must be renamed `test_handle_output_total_five_objects` and updated to `call_count == 5`; all other 13 F-043 tests in that file remain untouched. Also corrected §5 Note and §2 `test_sft_synthesis_qa.py` row for consistency.
- **L1** — Added an explicit `try/except botocore.exceptions.ClientError` block in §3.5 wrapping all 5 `put_object` calls and the `update_dataset_row()` DB call. On failure, `context.log.error(...)` is called before re-raising. §3.6 failure semantics updated to match: dataset row stays `'pending'`, operator-level retry redoes all 5 uploads (idempotent overwrite to MinIO is fine — keys are deterministic).
- **L2** — Added an explicit one-sentence clarification in §4 (Verification Plan, V1): F-044's `passes: true` is earned by DB-layer assertions (V1a/V1b/V1c + SQL inspection), not by the HTTP endpoint; the HTTP round-trip is deferred to F-046.
- **NIT-1** — Updated §2 `hf_dataset_io_manager.py` table row to explicitly require updating both docstrings (class + method) from "four objects" to **five objects**, with the full enumeration. Updated §3 Step 3 heading prose to consistently say "five objects".
- **NIT-2** — Added explicit `DatasetOutput.dataset_card_md` field specification in §3.4: field name, type annotation (`str | None`), default (`None`), and position (last field in the dataclass to avoid Python `TypeError`).
- **NIT-3** — Added an explicit note in §3.6 and §3 Step 5 that `stats` (the JSONB heavy-stats column) remains `NULL` after F-044 and is deferred to a later sprint.

---

## 1. Goal

Extend the existing `HFDatasetIOManager.handle_output()` (landed in F-043) to complete the two responsibilities that were explicitly deferred:

1. **Upload `dataset_infos.json`** to MinIO under the same `s3://datasets/{dataset_id}_{version_tag}/` prefix, in a shape compatible with the HuggingFace Datasets library convention (a JSON object mapping split name → split info with `num_examples`, `num_bytes`, and derived totals).

2. **Update the Postgres `dataset` row** — after all five MinIO uploads succeed — from `status='pending'` to `status='done'`, setting `sample_count = len(train_rows) + len(val_rows)` and `size_bytes = sum(Parquet buffer sizes for both splits)` and `materialized_at = NOW()`. The UPDATE uses `WHERE id = %s AND status = 'pending'` so it is a true no-op (0-row update) on any re-run after the row is already `'done'`, preserving the original `materialized_at` timestamp.

After this sprint, a `GET /api/datasets/{id}` call (implemented in F-046) will be able to return `{"status": "done", "sample_count": N, "size_bytes": B}` for a completed materialization, and MinIO will contain a fully HF-compatible dataset layout at the expected prefix.

No FastAPI schema changes are required (all new logic lives in the Dagster layer). No new Alembic migration is needed — `sample_count`, `size_bytes`, `status`, and `materialized_at` already exist on the `dataset` table (confirmed from `apps/api/dataplat_api/db/models.py` lines 268-278).

---

## 2. Files Changed

| File | Status | Role |
|---|---|---|
| `dagster/dagster_platform/hf_dataset_io_manager.py` | **edit** | Add `dataset_infos.json` upload (step 3e below); add `update_dataset_row()` helper call after all uploads complete; update module-level docstring and `handle_output` docstring to (a) remove the "Deferred (F-044)" caveats and (b) update the object-count references from "four objects" to **five objects**, explicitly enumerating all five: Parquet train, Parquet validation, recipe.json, README.md, dataset_infos.json. |
| `dagster/dagster_platform/sft_synthesis_qa.py` | **edit** | Add `update_dataset_row(dataset_id, sample_count, size_bytes)` helper function using `psycopg2` (sync, same pattern as `fetch_dataset_row()`). Extend `fetch_dataset_row()` to also `SELECT dataset_card_md`. Add `dataset_card_md: str \| None = None` as the **last** field in the `DatasetOutput` dataclass. |
| `dagster/tests/test_hf_dataset_io_manager.py` | **edit** | Add new test cases for `dataset_infos.json` content (V1/V2), DB update call (V3), `size_bytes` computation, and partial-failure semantics. F-043's existing 14 tests are preserved **except** `test_handle_output_total_four_objects` (file: `dagster/tests/test_hf_dataset_io_manager.py`, lines ~220–227), which must be **renamed** to `test_handle_output_total_five_objects` and updated to assert `call_count == 5`. This is the only F-043 test that requires modification; all other 13 F-043 tests in this file remain untouched. |
| `dagster/tests/test_sft_synthesis_qa.py` | **edit** | Add unit tests for the new `update_dataset_row()` helper (mocked `psycopg2.connect`, happy path + DB failure). F-043's existing 27 tests are preserved unchanged. |

No files under `apps/api/` are touched. `make codegen` is NOT required (no API schema changes).

---

## 3. Implementation Plan

### Step 0 — Pre-condition: asset finishes

`HFDatasetIOManager.handle_output(context, obj: DatasetOutput)` is called by Dagster after the `dataset` asset returns a `DatasetOutput`. At this point `obj` contains:

```python
obj.train_rows        # list[dict]  — "instruction", "output", "chunk_id"
obj.val_rows          # list[dict]
obj.recipe_snapshot   # dict        — frozen at F-042 INSERT time
obj.dataset_id        # int         — primary key of the dataset row
obj.version_tag       # str         — e.g. "v1"
obj.dataset_card_md   # str | None  — NEW in F-044; None for all current datasets (F-042 does not set it)
```

### Step 1 — Serialise Parquet bytes (unchanged from F-043)

```python
train_bytes = _rows_to_parquet_bytes(obj.train_rows)   # len() gives size in bytes
val_bytes   = _rows_to_parquet_bytes(obj.val_rows)
```

Both buffers are materialised in memory **before** any S3 calls. `len(train_bytes)` and `len(val_bytes)` are the authoritative byte counts for `size_bytes` (see §3.3).

### Step 2 — Compute `dataset_infos.json` content

`dataset_infos.json` follows the HuggingFace Datasets library convention for the `DatasetInfo` registry. The minimum viable shape that `datasets.load_dataset()` can consume is:

```json
{
  "default": {
    "description": "",
    "citation": "",
    "homepage": "",
    "license": "",
    "features": {
      "instruction": {"dtype": "string", "_type": "Value"},
      "output":      {"dtype": "string", "_type": "Value"},
      "chunk_id":    {"dtype": "string", "_type": "Value"}
    },
    "splits": {
      "train": {
        "name": "train",
        "num_bytes": <len(train_bytes)>,
        "num_examples": <len(obj.train_rows)>,
        "dataset_name": "default"
      },
      "validation": {
        "name": "validation",
        "num_bytes": <len(val_bytes)>,
        "num_examples": <len(obj.val_rows)>,
        "dataset_name": "default"
      }
    },
    "download_size": <len(train_bytes) + len(val_bytes)>,
    "dataset_size": <len(train_bytes) + len(val_bytes)>
  }
}
```

Key decisions baked in here:
- The top-level key is `"default"` (the HF convention for single-config datasets; `datasets.load_dataset("path")` looks for this key).
- `features` are encoded as `{"dtype": "string", "_type": "Value"}` — the canonical HF schema notation for a plain string column.
- `download_size` and `dataset_size` are both set to the sum of the two Parquet buffer sizes (Parquet-only; see §3.3 for the `size_bytes` definition question raised in OQ-3).
- `num_bytes` per split equals the Parquet buffer size for that split (computed from `len(buffer)` before upload — **not** from the S3 ETag or put_object response; see OQ-3).

A concrete helper function `_build_dataset_infos(train_bytes, val_bytes, train_count, val_count) -> bytes` is added to `hf_dataset_io_manager.py` and returns UTF-8-encoded JSON bytes. This keeps the logic pure and testable without an S3 mock.

### Step 3 — Upload **five** objects to MinIO (ordered; fail-fast with structured logging)

Uploading in this order:

```
3a. {prefix}/data/train-00000.parquet       (already in F-043)
3b. {prefix}/data/validation-00000.parquet  (already in F-043)
3c. {prefix}/recipe.json                    (already in F-043)
3d. {prefix}/README.md                      (already in F-043 — contains dataset_card_md content; see §3.4)
3e. {prefix}/dataset_infos.json             (NEW — F-044)
```

All five calls use `s3.put_object(Bucket=bucket, Key=..., Body=...)`. They are called sequentially (no change to the existing sync-boto3 pattern). The five `put_object` calls and the subsequent DB UPDATE are wrapped in a single `try/except` block (see §3.5 for the full call sequence). If any `put_object` raises a `botocore.exceptions.ClientError`, `context.log.error()` is called before the exception propagates out of `handle_output()`; the DB row is **not** updated (see §3.6 failure semantics).

The `put_object` call order places `dataset_infos.json` **last** among the MinIO writes so that if it fails, the Parquet files are still present (they are the primary artifacts). The DB update comes **after** all five uploads.

### Step 4 — Compute `sample_count` and `size_bytes`

```python
sample_count: int = len(obj.train_rows) + len(obj.val_rows)
size_bytes: int   = len(train_bytes) + len(val_bytes)
```

`size_bytes` is defined as **the sum of the two Parquet buffer sizes** measured in memory before upload (see OQ-3 for the full rationale and alternatives). This definition is:
- Deterministic and idempotent (same input → same bytes, regardless of S3 response).
- Consistent with `dataset_infos.json` (both use the same two numbers).
- Available without any post-upload stat call.

### Step 5 — Update Postgres `dataset` row (sync via `psycopg2`)

After all five `put_object` calls succeed, call the new helper:

```python
# in sft_synthesis_qa.py
def update_dataset_row(
    dataset_id: int,
    sample_count: int,
    size_bytes: int,
) -> None:
    """UPDATE dataset SET status='done', sample_count, size_bytes, materialized_at=NOW()
    WHERE id = dataset_id AND status = 'pending'.

    The AND status = 'pending' predicate makes this a true no-op (0-row UPDATE,
    no error) when the row is already 'done', preserving the original materialized_at
    timestamp on any idempotent re-run.

    NOTE: 'stats' (JSONB heavy-stats column) is NOT updated here; it remains NULL
    after F-044. Population of stats is deferred to a later sprint.

    Uses psycopg2 (sync), same pattern as fetch_dataset_row().
    PLATFORM_DB_URL from os.environ.

    Raises:
        psycopg2.Error: on any DB failure (caller — HFDatasetIOManager — lets this
                        propagate, leaving MinIO uploads intact but the row at
                        status='pending'. Ops recovers by re-running the materialization
                        or by direct SQL UPDATE. See F-044 failure semantics §3.6.
    """
    db_url = os.environ["PLATFORM_DB_URL"]
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE dataset
                SET status = 'done',
                    sample_count = %s,
                    size_bytes = %s,
                    materialized_at = NOW()
                WHERE id = %s
                  AND status = 'pending'
                """,
                (sample_count, size_bytes, dataset_id),
            )
        conn.commit()
    finally:
        conn.close()
```

**`stats` column**: The `dataset` table has a `stats JSONB` column (design doc §4.1, `models.py` line 270) for split sizes and attribute distributions. `update_dataset_row()` does NOT touch `stats`; it remains `NULL` after F-044. Population of `stats` is deferred to a later sprint.

**Strategy decision — sync `psycopg2` (Option A)**:

The Dagster worker asset body is synchronous Python (no `asyncio` event loop). Three options were evaluated:

| Option | Mechanism | Verdict |
|---|---|---|
| **A — sync `psycopg2`** | `psycopg2.connect(os.environ["PLATFORM_DB_URL"])` | **Chosen.** Consistent with `fetch_dataset_row()` (F-043, `sft_synthesis_qa.py` lines 126-151) and every other Dagster Postgres helper in this codebase (`insert_document_variant()` in `extractor.py`, `lookup_source_collection_id()` in `chunker.py`). No new dependencies. |
| B — async via `asyncio.run()` | Wrap an async SQLAlchemy session in `asyncio.run()` | Rejected: introduces a new event loop inside a sync context; async SQLAlchemy engine startup overhead; inconsistent with the established Dagster pattern; invariant #5 is explicitly scoped to `apps/api/dataplat_api/`. |
| C — HTTP callback to FastAPI | POST to an internal FastAPI endpoint | Rejected: creates coupling between Dagster worker and FastAPI availability at dataset-write time; introduces a new internal endpoint that must be auth'd or secured; significantly more surface area for a one-line UPDATE. |

Hard invariant #5 (Async SQLAlchemy from day one) is **explicitly scoped to `apps/api/dataplat_api/`** (see CLAUDE.md §"Hard invariants" item 5). The Dagster layer has always used sync `psycopg2` for all Postgres access. This is not a violation.

### Step 6 — Emit metadata

After the DB update, add to `context.add_output_metadata()`:

```python
context.add_output_metadata({
    "train_rows":     MetadataValue.int(len(obj.train_rows)),
    "val_rows":       MetadataValue.int(len(obj.val_rows)),
    "sample_count":   MetadataValue.int(sample_count),
    "size_bytes":     MetadataValue.int(size_bytes),
    "dataset_uri":    MetadataValue.text(f"s3://{bucket}/{prefix}/"),
    "dataset_status": MetadataValue.text("done"),
})
```

This extends the F-043 metadata dict with `sample_count`, `size_bytes`, and `dataset_status` — visible in the Dagster asset materialization event log.

### 3.4 — `README.md` content (use `dataset_card_md` if available)

The F-043 stub set `README.md` to a one-liner: `f"# Dataset {prefix}\n\nGenerated by sft_synthesis_qa.\n"`. F-044 uses `obj.dataset_card_md` if it is non-null, falling back to the stub string.

**`DatasetOutput.dataset_card_md` field specification (NIT-2)**:

```python
@dataclass
class DatasetOutput:
    train_rows:       list[dict]
    val_rows:         list[dict]
    recipe_snapshot:  dict
    dataset_id:       int
    version_tag:      str
    dataset_card_md:  str | None = None   # ← NEW; appended last (has default; Python requires default fields last)
```

The field is placed **last** in the dataclass definition. Python raises `TypeError` if a field with a default appears before any field without a default — the `None` default requires this positioning. All existing callers that construct `DatasetOutput` positionally or by keyword remain backward-compatible (the new field is optional and defaults to `None`).

**Extending `fetch_dataset_row()`**: Option R1 (chosen per OQ-7 ruling): extend `fetch_dataset_row()` to `SELECT id, recipe_snapshot, hf_repo_uri, dataset_card_md FROM dataset WHERE ...` and include `dataset_card_md` in the returned dict. The `dataset` asset in `definitions.py` passes `dataset_card_md=row["dataset_card_md"]` when constructing `DatasetOutput`. Since `dataset_card_md` is `NULL` for all current datasets (F-042 does not set it), the fallback stub will always trigger in practice for MVP.

### 3.5 — `handle_output()` call sequence (complete)

```
1.  train_bytes = _rows_to_parquet_bytes(obj.train_rows)
2.  val_bytes   = _rows_to_parquet_bytes(obj.val_rows)
3.  dataset_infos_bytes = _build_dataset_infos(
        train_bytes, val_bytes,
        len(obj.train_rows), len(obj.val_rows)
    )
4.  s3 = _build_s3_client()
5.  bucket = os.environ.get("MINIO_DATASETS_BUCKET", "datasets")
6.  prefix = f"{obj.dataset_id}_{obj.version_tag}"
7.  try:
8.      s3.put_object(Bucket=bucket, Key=f"{prefix}/data/train-00000.parquet",      Body=train_bytes)
9.      s3.put_object(Bucket=bucket, Key=f"{prefix}/data/validation-00000.parquet", Body=val_bytes)
10.     s3.put_object(Bucket=bucket, Key=f"{prefix}/recipe.json",                   Body=recipe_json_bytes)
11.     s3.put_object(Bucket=bucket, Key=f"{prefix}/README.md",                     Body=readme_bytes)
12.     s3.put_object(Bucket=bucket, Key=f"{prefix}/dataset_infos.json",            Body=dataset_infos_bytes)
13.     sample_count = len(obj.train_rows) + len(obj.val_rows)
14.     size_bytes   = len(train_bytes) + len(val_bytes)
15.     update_dataset_row(obj.dataset_id, sample_count, size_bytes)
16. except botocore.exceptions.ClientError as exc:
17.     context.log.error(
18.         "HFDatasetIOManager: S3 put_object failed (dataset_id=%d, prefix=%r): %s",
19.         obj.dataset_id, prefix, exc,
20.     )
21.     raise
22. context.add_output_metadata({...})
```

Notes:
- The `try/except` block (steps 7–21) wraps **all five** `put_object` calls and the `update_dataset_row()` DB call. Any `ClientError` from MinIO causes structured logging and then re-raises; the DB update is naturally skipped.
- DB errors from `update_dataset_row()` (i.e., `psycopg2.Error`) propagate directly without being caught here — they bubble out of `handle_output()` as a separate signal; `context.log.error()` is called inside `update_dataset_row()` itself (see Step 5 docstring note about `psycopg2.Error`). Alternatively, a second `except psycopg2.Error` clause may be added at the implementer's discretion for symmetric logging.
- The F-043 existing steps (serialise Parquet + recipe.json + README) remain unchanged. The `try/except` wrapper and the `dataset_infos.json` upload are net-new.

### 3.6 — Failure semantics

| Failure point | MinIO state | DB state | Recovery |
|---|---|---|---|
| `put_object` fails at step 8 (train Parquet) | 0 objects written | `status='pending'` | Re-run the Dagster partition. All five `put_object` calls are idempotent (MinIO upsert). `context.log.error()` emits a structured message before re-raising. |
| `put_object` fails at step 12 (`dataset_infos.json`) | 4 objects written (train, val, recipe, README); `dataset_infos.json` absent | `status='pending'` | Re-run the Dagster partition. All five `put_object` calls are idempotent. The Parquet files from the partial run are valid but the dataset is not HF-loadable without `dataset_infos.json`. The `status='pending'` row signals incompleteness. `context.log.error()` emits a structured message before re-raising. |
| `update_dataset_row()` fails (DB error) | All 5 objects written and durable in MinIO | `status='pending'` (not updated to `done`) | Row is stuck at `status='pending'`; MinIO artifacts are present. Recovery: ops re-runs the Dagster partition (idempotent MinIO overwrites + retried DB UPDATE with `AND status='pending'`), or direct SQL: `UPDATE dataset SET status='done', sample_count=N, size_bytes=B, materialized_at=NOW() WHERE id=<id> AND status='pending'`. |
| Re-run after full success (row already `'done'`) | All 5 objects overwritten with identical bytes (deterministic keys + content) | `UPDATE ... WHERE id=%s AND status='pending'` matches 0 rows → no-op; `materialized_at` preserved from original run | N/A (no failure; idempotent re-run) | ✅ True no-op after M1 fix |

**No rollback of MinIO writes on DB failure.** MinIO `put_object` is not transactional with Postgres. If the DB UPDATE fails after all uploads succeed, the objects remain in MinIO. This is the correct trade-off: partial MinIO writes are worse (unusable artifacts) than a DB row that lags behind MinIO state. Re-running is safe because all five uploads are idempotent (MinIO `put_object` is an upsert).

**Idempotency on re-materialization (corrected from Rev 1)**: If `handle_output()` is called twice for the same `(dataset_id, version_tag)`, all five `put_object` calls overwrite existing objects with identical bytes (keys and content are fully deterministic from input). The `UPDATE dataset ... WHERE id = %s AND status = 'pending'` predicate ensures that once the row is `'done'`, subsequent re-runs produce a 0-row UPDATE — leaving `status`, `sample_count`, `size_bytes`, and `materialized_at` exactly as set on the first successful run. This is a **true no-op** on re-run (Rev 1 incorrectly claimed this without the `AND status = 'pending'` guard).

**`stats` column**: `update_dataset_row()` does NOT set `stats`. The `stats JSONB` column (design doc §4.1, `models.py` line 270) remains `NULL` after F-044. Population of `stats` (split sizes, attribute distributions, etc.) is deferred to a later sprint.

---

## 4. Verification Plan

The three F-044 verification criteria from `spec/feature_list.json` are mapped as follows:

### V1 — `GET /api/datasets/{id}` returns `status='done'`, `sample_count`, `size_bytes`

**Scope clarification for `passes: true` (L2)**: The `GET /api/datasets/{id}` endpoint is implemented in F-046 (not yet landed). **F-044's `passes` flag is earned by DB-layer assertions only** — specifically, by confirming via `SELECT status, sample_count, size_bytes, materialized_at FROM dataset WHERE id = <id>` (or by the mock-based unit tests V1a/V1b/V1c below) that the Postgres row reaches `status='done'` with correct values after `handle_output()` runs. The full HTTP-level V1 round-trip (`GET /api/datasets/{id}` returning 200 with the expected JSON body) will be verified green when F-046 lands; that HTTP check is **not** required for F-044's `passes` to flip to `true`.

**Test**: `dagster/tests/test_hf_dataset_io_manager.py::test_db_row_updated_to_done`

Setup: mock psycopg2 + mock boto3. Call `handle_output()` with a known `DatasetOutput` (3 train rows, 1 val row).  
Assert: `update_dataset_row` (or the captured mock `cur.execute`) was called with `status='done'`, `sample_count=4`, `size_bytes=len(train_bytes)+len(val_bytes)`.

**Test**: `dagster/tests/test_sft_synthesis_qa.py::test_update_dataset_row_happy_path`

Mock `psycopg2.connect`; assert `cur.execute` called with SQL containing `status = 'done'`, `sample_count=%s`, `size_bytes=%s`, `materialized_at = NOW()`, `WHERE id = %s`, and `AND status = 'pending'`.

### V2 — `s3://datasets/{dataset_id}_v{version}/dataset_infos.json` exists and is valid JSON

**Test**: `dagster/tests/test_hf_dataset_io_manager.py::test_dataset_infos_json_uploaded`

Assert `put_object` called with `Key="{prefix}/dataset_infos.json"`.

**Test**: `dagster/tests/test_hf_dataset_io_manager.py::test_dataset_infos_json_valid_json`

Capture the `Body=` bytes for the `dataset_infos.json` key; `json.loads(body)` must not raise.

**Test**: `dagster/tests/test_hf_dataset_io_manager.py::test_dataset_infos_json_content`

Assert the parsed JSON has top-level key `"default"`, and that `default["splits"]["train"]["num_examples"]` equals `len(train_rows)`, `default["splits"]["validation"]["num_examples"]` equals `len(val_rows)`, `default["splits"]["train"]["num_bytes"]` equals `len(train_parquet_bytes)`, `default["splits"]["validation"]["num_bytes"]` equals `len(val_parquet_bytes)`.

**Test**: `dagster/tests/test_hf_dataset_io_manager.py::test_dataset_infos_json_features_schema`

Assert `default["features"]` contains keys `"instruction"`, `"output"`, `"chunk_id"`, each with `{"dtype": "string", "_type": "Value"}`.

**Test**: `dagster/tests/test_hf_dataset_io_manager.py::test_build_dataset_infos_helper`

Direct unit test of `_build_dataset_infos(train_bytes, val_bytes, train_count, val_count)`: asserts the returned bytes are valid JSON with the expected structure (isolated from S3 mock).

### V3 — `s3://datasets/{dataset_id}_v{version}/README.md` exists and contains `dataset_card_md`

The F-043 upload of `README.md` is unchanged (it already satisfies "exists"). F-044 enhances the content to use `dataset_card_md` when available.

**Test** (existing, must still pass): `test_handle_output_uploads_readme_and_recipe` — `Key="{prefix}/README.md"` still present.

**Test** (new): `dagster/tests/test_hf_dataset_io_manager.py::test_readme_uses_dataset_card_md`

Provide a `DatasetOutput` with `dataset_card_md="# My dataset\n\nCool content."`. Assert the `Body=` of the `README.md` `put_object` call contains `"My dataset"` and `"Cool content."`.

**Test** (new): `dagster/tests/test_hf_dataset_io_manager.py::test_readme_fallback_when_no_card_md`

Provide a `DatasetOutput` with `dataset_card_md=None`. Assert `README.md` body contains the fallback stub string (includes the `{prefix}` value).

---

## 5. Test List

New test cases to be added. F-043's existing 41 tests are addressed as follows:
- **`dagster/tests/test_hf_dataset_io_manager.py`**: 13 of the 14 F-043 tests are preserved unchanged. Exactly one — `test_handle_output_total_four_objects` (lines ~220–227) — is **renamed** to `test_handle_output_total_five_objects` and its assertion updated from `call_count == 4` to `call_count == 5`. This is a spec change (F-044 adds a 5th upload), not a regression.
- **`dagster/tests/test_sft_synthesis_qa.py`**: all 27 F-043 tests preserved unchanged.

**In `dagster/tests/test_hf_dataset_io_manager.py`** (editing, not replacing):

| Label | Test name | What it asserts |
|---|---|---|
| V2a | `test_dataset_infos_json_uploaded` | `put_object` called with Key ending in `/dataset_infos.json`; now exactly **5** `put_object` calls total (was 4 in F-043). |
| V2b | `test_dataset_infos_json_valid_json` | Body bytes of the `dataset_infos.json` upload are valid UTF-8 JSON. |
| V2c | `test_dataset_infos_json_content` | `splits.train.num_examples`, `splits.validation.num_examples`, `splits.train.num_bytes`, `splits.validation.num_bytes` match the DatasetOutput row counts and buffer sizes. |
| V2d | `test_dataset_infos_json_features_schema` | `features` has keys `instruction`, `output`, `chunk_id`, each `{"dtype":"string","_type":"Value"}`. |
| V2e | `test_dataset_infos_download_and_dataset_size` | `download_size` and `dataset_size` both equal `len(train_bytes)+len(val_bytes)`. |
| V2f | `test_build_dataset_infos_helper` | Direct unit test of `_build_dataset_infos()` without S3 mock; asserts JSON structure and byte roundtrip. |
| V1a | `test_db_row_updated_to_done` | After `handle_output()`, `update_dataset_row` called with correct `dataset_id`, `sample_count`, `size_bytes`. Uses `patch("dagster_platform.sft_synthesis_qa.update_dataset_row")` to capture the call. |
| V1b | `test_db_update_not_called_if_minio_fails` | If a `boto3` `put_object` raises `botocore.exceptions.ClientError`, the exception propagates and `update_dataset_row` is **not** called (verify mock call count = 0). |
| V1c | `test_size_bytes_equals_parquet_buffer_sum` | Construct `DatasetOutput` with known row counts; capture `size_bytes` passed to `update_dataset_row`; assert it equals the sum of the two in-memory Parquet buffer sizes. |
| V3a | `test_readme_uses_dataset_card_md` | When `obj.dataset_card_md = "# Custom card"`, README.md Body contains `"Custom card"`. |
| V3b | `test_readme_fallback_when_no_card_md` | When `obj.dataset_card_md = None`, README.md Body contains the fallback stub string (non-empty, contains `f"{dataset_id}_{version_tag}"`). |
| A1  | `test_total_five_objects_uploaded` | Exactly 5 `put_object` calls after F-044 (was 4 in F-043; `test_handle_output_total_four_objects` renamed and updated — see §5 preamble). |
| A2  | `test_dataset_infos_key_prefix` | `dataset_infos.json` key starts with the correct `{dataset_id}_{version_tag}/` prefix. |
| A3  | `test_dataset_infos_zero_rows` | When both splits are empty (D5 zero-row), `dataset_infos.json` is still valid JSON with `num_examples=0` in both splits. |

**In `dagster/tests/test_sft_synthesis_qa.py`** (editing):

| Label | Test name | What it asserts |
|---|---|---|
| B1 | `test_update_dataset_row_happy_path` | Mock `psycopg2.connect`; assert `cur.execute` called with SQL containing `status = 'done'`, `AND status = 'pending'`, and correct parameter tuple `(sample_count, size_bytes, dataset_id)`. |
| B2 | `test_update_dataset_row_commits` | After `cur.execute`, `conn.commit()` is called before `conn.close()` (ordering assertion on mock). |
| B3 | `test_update_dataset_row_closes_on_db_error` | If `cur.execute` raises `psycopg2.OperationalError`, `conn.close()` is still called (finally block), and the exception propagates. |

---

## 6. Invariant Checklist

| # | Invariant | Status | Justification |
|---|---|---|---|
| 1 | **Lineage mandatory** (`parents[]`, processor identity, config hash, input refs) | ✅ N/A | F-044 updates a `dataset` row (not a `Commit` row). The `recipe_snapshot` already records the processor lineage (frozen by F-042 at INSERT time). The `dataset` table is the materialization record for a dataset pipeline, not a Silver/Gold commit in the sense of §1.2. No `Commit` row is required for F-044 (see OQ-5 for the deferred Commit question). |
| 2 | **Storage separation + CAS** | ✅ | `sample_count` (int) and `size_bytes` (int) are metrics stored in Postgres. Parquet bytes and `dataset_infos.json` bytes go to MinIO only. No blob bytes written to Postgres. |
| 3 | **Schema frozen post-publish** | ✅ | The `status` flip to `'done'` is the publish event for this dataset version. The `recipe_snapshot` is already frozen (from F-042). Setting `status='done'` does not modify `recipe_snapshot`. With M1 fix (`AND status = 'pending'`), a re-run after publish produces a 0-row UPDATE — the published row is not mutated. |
| 4 | **LLM calls through gateway** | ✅ N/A | No LLM calls in F-044. |
| 5 | **Async SQLAlchemy (`apps/api/`)** | ✅ N/A | F-044 code lives entirely in `dagster/`. The Postgres UPDATE uses `psycopg2` (sync) via `update_dataset_row()`, consistent with all other Dagster Postgres helpers (`fetch_dataset_row`, `insert_document_variant`, `lookup_source_collection_id`). Invariant #5 is explicitly scoped to `apps/api/dataplat_api/` per CLAUDE.md. |
| 6 | **OpenAPI ↔ TS type sync** | ✅ N/A | No changes to `apps/api/` schemas. `make codegen` is not required. |

---

## 7. Open Questions for Reviewer

> **Note**: OQ-1 through OQ-9 were ruled on definitively by the Mode A reviewer in `feedback.md`. Rulings are reproduced below for implementer reference. No open questions remain.

**OQ-1 (sync vs async DB session from Dagster worker)**  
**RULING: CONFIRMED — use sync psycopg2.** Consistent with `fetch_dataset_row()` (F-043, `sft_synthesis_qa.py` lines 125–151), `insert_document_variant()` (`extractor.py`), and `lookup_source_collection_id()` (`chunker.py`). CLAUDE.md invariant #5 is explicitly scoped to `apps/api/dataplat_api/` — the Dagster worker is outside this boundary. Options B and C rejected.

**OQ-2 (`dataset_infos.json` schema fidelity — full HF spec or minimal subset?)**  
**RULING: CHOOSE MINIMAL SUBSET.** The proposed shape (`default`, `features`, `splits`, `download_size`, `dataset_size`) is sufficient for `datasets.load_dataset()`. Omitting optional fields (`version`, `builder_name`, `supervised_keys`, `task_templates`) reduces maintenance surface and test fragility.

**OQ-3 (`size_bytes` definition)**  
**RULING: CONFIRM OPTION A** — `len(train_bytes) + len(val_bytes)` (Parquet buffers in memory, before upload). Consistent with `dataset_infos.json`, deterministic, available without S3 round-trip.

**OQ-4 (should `status` flip to `'done'` BEFORE or AFTER all 5 MinIO files are confirmed uploaded?)**  
**RULING: CONFIRMED — flip AFTER all 5 uploads.** `status='done'` guarantees all 5 MinIO objects are present.

**OQ-5 (Commit row for lineage compliance?)**  
**RULING: NO Commit row required for F-044.** The `dataset` row is the terminal artifact. `chunk_id` in each Parquet row + frozen `recipe_snapshot` satisfy design doc §1.2 req. 5 lineage requirements. No additional `Commit` table row needed for MVP.

**OQ-6 (idempotency on re-materialization — `status='pending'` → re-run?)**  
**RULING: PARTIALLY CONFIRMED with correction.** `status='done'` and `sample_count`/`size_bytes` values are idempotent on re-run. `materialized_at = NOW()` is NOT idempotent without the `AND status = 'pending'` predicate (see M1). With this fix, a re-run on an already-done row produces a 0-row UPDATE, preserving the original `materialized_at`. `status`, `sample_count`, and `size_bytes` are not mutated on re-run either (the entire row update is skipped).

**OQ-7 (`DatasetOutput.dataset_card_md` field vs second SELECT)**  
**RULING: CHOOSE OPTION (a)** — add `dataset_card_md: str | None = None` as the **last** field in `DatasetOutput` (see §3.4 for exact field spec). Extend `fetch_dataset_row()` to include `dataset_card_md` in the SELECT. The cascade touches `sft_synthesis_qa.py`, `definitions.py`, and `_run_dataset_asset()` — all confirmed acceptable (§R6). Option (b) rejected (leaks Postgres awareness into the IO manager).

**OQ-8 (should `README.md` content be the `dataset_card_md` Postgres column, or always the stub?)**  
**RULING: WIRE UP FALLBACK NOW.** The V3 verification criterion requires `dataset_card_md` content. `dataset_card_md if dataset_card_md else stub` is 3 lines + 2 tests. Since `dataset_card_md` is `NULL` for all current datasets, the fallback triggers in practice for MVP — zero functional risk.

**OQ-9 (number of `put_object` calls: 5 or keep existing `test_handle_output_total_four_objects` assertion?)**  
**RULING: CONFIRMED — update to assert 5.** `test_handle_output_total_four_objects` → `test_handle_output_total_five_objects`, `call_count == 4` → `call_count == 5`. This is the only F-043 test requiring modification (see M2 in §Round 2 Changes).

---

## 8. Risks

**R1 — Partial-failure between MinIO and Postgres DB**  
All 5 MinIO objects written successfully, then `psycopg2` raises (DB timeout, network drop). Result: MinIO is complete; Postgres row stays `status='pending'`. The dataset is queryable and usable but `GET /api/datasets/{id}` will not return `status='done'` until recovery. Recovery: re-run the Dagster materialization (idempotent) or direct SQL. This is the primary operational risk of F-044. Mitigation: the IO manager's `try/except` block (§3.5) logs a clear error at `context.log.error()` before re-raising, providing an observable failure signal.

**R2 — Partial MinIO write (fail mid-upload sequence)**  
If uploads 1-4 succeed but upload 5 (`dataset_infos.json`) fails, the Parquet files are present but the dataset is not HF-loadable (missing `dataset_infos.json`). `status` stays `'pending'`. Mitigation: same as R1 — re-run is safe and idempotent. `context.log.error()` emits a structured message identifying the failed key.

**R3 — `dataset_infos.json` schema drift vs. HF Datasets library version**  
The HF Datasets library's `DatasetInfo` schema has evolved across versions. Using a minimal subset reduces the risk of forward-compatibility issues. However, the specific field names (`_type`, `dtype`) are stable in datasets ≥ 2.x. Risk: minimal for MVP.

**R4 — `test_handle_output_total_four_objects` requires modification**  
This is the only F-043 test that breaks under F-044. The assertion `call_count == 4` becomes `call_count == 5`. This is expected and documented (M2 resolution); it is not a regression. The test must be renamed to `test_handle_output_total_five_objects`.

**R5 — `PLATFORM_DB_URL` not set in Dagster worker environment**  
`update_dataset_row()` reads `os.environ["PLATFORM_DB_URL"]`. This env var is already consumed by `fetch_dataset_row()` (F-043) in the same Dagster worker. If it is missing, a `KeyError` propagates out of `handle_output()` before any DB call, leaving `status='pending'`. This is an ops misconfiguration, not a code bug. No additional guard needed.

**R6 — `DatasetOutput` dataclass change ripples**  
Per OQ-7 ruling (Option a): adding `dataset_card_md: str | None = None` as the last field in `DatasetOutput` requires updating `definitions.py` (DatasetOutput constructor call) and `_run_dataset_asset()` in `sft_synthesis_qa.py` to pass the new field. This touches 3 files. Scope is small; cascade is confirmed acceptable. The `None` default means all existing positional/keyword callers remain backward-compatible if not immediately updated.

---

## §11 Round-2 reviewer addenda (folded into agreed.md as non-blocking corrections)

**NIT-4 (round 2)** — `context.log.error()` is NOT in scope inside `update_dataset_row()` (which is a pure helper called from `handle_output()`). Correction: the `context.log.error(...)` call belongs in the OUTER `try/except` in `handle_output()` itself (the one wrapping the 5 `put_object` calls + the `update_dataset_row()` call). Inside `update_dataset_row()` itself, on `psycopg2.Error`, simply re-raise (let the outer handler log + re-raise). §3.5's note to call `context.log.error` for psycopg2 errors stays correct only because the call site is in `handle_output`, not in the helper. Implementer: ensure `update_dataset_row()` does NOT take `context` as a parameter; the outer `try/except` in `handle_output()` does the logging.

**NIT-5 (round 2)** — §3.6 failure-mode table cells are cosmetically uneven (rows 3–4 have 5 cells, declared 4-col header). Implementer: render as a clean 4-column table (Failure point | Already written | Stays at | Recovery) when implementing; no functional change.

---

## §11 Round-2 reviewer addenda (folded into agreed.md as non-blocking corrections)

**NIT-4 (round 2)** — `context.log.error()` is NOT in scope inside `update_dataset_row()` (which is a pure helper called from `handle_output()`). Correction: the `context.log.error(...)` call belongs in the OUTER `try/except` in `handle_output()` itself (the one wrapping the 5 `put_object` calls + the `update_dataset_row()` call). Inside `update_dataset_row()` itself, on `psycopg2.Error`, simply re-raise (let the outer handler log + re-raise). §3.5's note to call `context.log.error` for psycopg2 errors stays correct only because the call site is in `handle_output`, not in the helper. Implementer: ensure `update_dataset_row()` does NOT take `context` as a parameter; the outer `try/except` in `handle_output()` does the logging.

**NIT-5 (round 2)** — §3.6 failure-mode table cells are cosmetically uneven (rows 3–4 have 5 cells, declared 4-col header). Implementer: render as a clean 4-column table (Failure point | Already written | Stays at | Recovery) when implementing; no functional change.
