# Sprint S043-F-043 — Proposed Contract

**Feature**: F-043 — `sft_synthesis_qa` materializer  
**Sprint directory**: `contracts/S043-F-043/`  
**Author**: leader (inline)  
**Date**: 2026-06-04  
**Revision**: 2 (post-reviewer-Mode-A)

---

## Goal

Replace the no-op `dataset` asset stub (landed by F-042) with a real
`sft_synthesis_qa` materializer that reads matching chunks from Lance using the
recipe's filter, calls the LLM via the internal gateway to synthesise Q+A pairs,
writes train/val Parquet splits plus README.md and recipe.json to
`s3://datasets/{dataset_id}_{version_tag}/`, and wires a new
`HFDatasetIOManager` to own the MinIO writes — leaving F-044 to update the
dataset row status on completion.

---

## Files to add / change

### New files

| File | Contents | Approx LoC |
|---|---|---|
| `dagster/dagster_platform/sft_synthesis_qa.py` | Pure helper functions: parse partition key, query Postgres for dataset row, read Lance chunks, call LLM gateway, deterministic train/val split. No Dagster imports — same no-Dagster convention as `quality_tagger.py`. | ~180 |
| `dagster/dagster_platform/hf_dataset_io_manager.py` | `HFDatasetIOManager(IOManager)`: receives `DatasetOutput` from the asset, serialises train/val lists to PyArrow tables, uploads two Parquet files + README.md + recipe.json to MinIO via boto3 (sync, same `_build_s3_client()` pattern as `extractor.py`). | ~120 |
| `dagster/tests/test_sft_synthesis_qa.py` | Unit tests for all helpers in `sft_synthesis_qa.py`: mocked `requests.post`, mocked `lancedb.connect`, mocked `psycopg2.connect`. Covers happy path, LLM parse failure / fallback, empty Lance result, split ratio boundary. | ~160 |
| `dagster/tests/test_hf_dataset_io_manager.py` | Unit tests for `HFDatasetIOManager.handle_output()`: mock boto3 S3 client, assert `put_object` called for each of the four objects, assert Parquet bytes contain `instruction`, `output`, and `chunk_id` columns, assert `recipe.json` content equals serialised recipe_snapshot. | ~90 |

### Modified files

| File | Change | Approx LoC delta |
|---|---|---|
| `dagster/dagster_platform/definitions.py` | (a) Add imports: `DatasetOutput` from `sft_synthesis_qa`, `HFDatasetIOManager` from `hf_dataset_io_manager`. (b) Replace the stub `dataset` asset body with the real implementation (call helpers, return `DatasetOutput`). (c) Add `io_manager_key="hf_dataset_io"` to the `@asset` decorator — this is the **ADDABLE** element frozen in F-042 agreed.md §6. (d) Add `"hf_dataset_io": HFDatasetIOManager()` to `Definitions(resources={...})`. | +35 / −5 |

No FastAPI changes. No new Alembic migration (all needed dataset columns exist:
`recipe_snapshot`, `hf_repo_uri`, `status`, `sample_count`, `size_bytes`, `stats`).  
No `make codegen` run (no API schema changes).

---

## Plugin shape

### Protocol / class hierarchy

There is no standalone `plugins/` directory in this codebase — the `Processor`
Protocol from `skills/plugin-protocol/SKILL.md` is not yet instantiated as a
formal class. The established pattern (followed by `quality_tagger.py`,
`lang_tagger.py`, `minhash_tagger.py`) is:

- **Pure helper module** in `dagster/dagster_platform/<name>.py` — no Dagster
  imports, no Protocol class, just functions.
- **Dagster asset** in `definitions.py` that calls those helpers and returns a
  typed payload.
- **IOManager** that owns the storage write.

This sprint follows that exact pattern rather than introducing a new Protocol
class. (Reviewer confirmed: the helper-function pattern is acceptable for F-043;
a formal `Materializer(Protocol)` class is not required in MVP.)

### Operator registration

`sft_synthesis_qa` will be registered in the Postgres `operator` table with
`category='materializer'` so the recipe editor can reference it. This is
**deferred to F-092** (see Out of Scope). The materializer reads config from
`recipe_snapshot["schema"]["config"]` directly at runtime — no operator table
query is needed for F-043 to function.

### `DatasetOutput` dataclass (return type of the `dataset` asset)

```python
# dagster/dagster_platform/sft_synthesis_qa.py
from dataclasses import dataclass
from typing import Any

@dataclass
class DatasetOutput:
    train_rows: list[dict[str, Any]]  # dicts with "instruction", "output", "chunk_id"
    val_rows:   list[dict[str, Any]]
    recipe_snapshot: dict[str, Any]   # frozen copy from Postgres dataset.recipe_snapshot
    dataset_id: int                   # DB-assigned dataset.id
    version_tag: str                  # e.g. "v1"
```

### `ctx` — no formal `ctx` object in this codebase

The asset receives `context: AssetExecutionContext` (Dagster). There is no
`ctx.llm`, `ctx.lance`, `ctx.s3`, `ctx.recipe`, or `ctx.dataset_id` attribute.
The existing convention maps these conceptual fields to:

| Design-doc `ctx` field | Actual mechanism |
|---|---|
| `ctx.llm.call()` | `requests.post(f"{LLM_GATEWAY_URL}/api/internal/llm/completions", ...)` — same pattern as `quality_tagger.py:score_chunks_via_gateway()` |
| `ctx.lance` | `lancedb.connect(db_uri, storage_options=_build_lance_storage_options())` |
| `ctx.s3` | `boto3.client("s3", ...)` via `_build_s3_client()` (same as `extractor.py`) |
| `ctx.recipe` / `ctx.dataset_id` | Retrieved from Postgres via `psycopg2.connect(os.environ["PLATFORM_DB_URL"])` |
| `ctx.dataset_id`, `ctx.version` | Parsed from `context.partition_key` (`"ds_{recipe_id}_v{n}"`) + Postgres lookup |

**Hard invariant #4 compliance**: the word `import anthropic` and `import openai`
must not appear anywhere in `sft_synthesis_qa.py` or `hf_dataset_io_manager.py`.
All LLM traffic goes through the internal HTTP endpoint.

### Inputs read

- **Lance chunks table** at `s3://{MINIO_LANCE_BUCKET}/chunks/chunks.lance/`
  filtered by `recipe_snapshot["filter"]["where"]` (SQL predicate; `None` means
  no filter). Columns read: `chunk_id`, `text` (same projection as
  `compute_quality_scores()` in `quality_tagger.py`).
- **Postgres `dataset` table**: `SELECT id, recipe_snapshot, hf_repo_uri FROM
  dataset WHERE recipe_id = ? AND version_tag = ?` — to resolve `dataset_id`
  and confirm the frozen `recipe_snapshot`.

### Outputs written (by `HFDatasetIOManager`)

All under the prefix `s3://datasets/{dataset_id}_{version_tag}/` (matches
`hf_repo_uri` written by F-042 datasets router: `f"s3://datasets/{dataset.id}_{version_tag}"`):

```
s3://datasets/{dataset_id}_{version_tag}/
    README.md                          ← dataset card stub
    recipe.json                        ← json.dumps(recipe_snapshot)
    data/
        train-00000.parquet            ← train split (PyArrow → Parquet bytes)
        validation-00000.parquet       ← val split
```

Note: **NOT** CAS-addressed (these are user-facing artifacts at a deterministic
path, not content-addressed blobs — consistent with design doc §4.3 layout and
hard invariant #2 which applies CAS to intermediate blobs, not dataset output).

---

## Algorithm sketch

```
─── Asset: dataset (definitions.py) ───────────────────────────────────────────

1.  partition_key = context.partition_key
    # format: "ds_{recipe_id}_v{n}"
    recipe_id, n = parse_dataset_partition_key(partition_key)
    version_tag = f"v{n}"
    # → recipe_id: int, version_tag: str ("v{n}")

2.  Postgres query (psycopg2, sync — invariant #5 is scoped to apps/api/):
    SELECT id, recipe_snapshot, hf_repo_uri
    FROM dataset
    WHERE recipe_id = %s AND version_tag = %s
    → dataset_id: int, recipe_snapshot: dict, hf_repo_uri: str
    (Raises ValueError with clear message if no row found — indicates F-042 step
    committed the row before this asset ran, which is the guaranteed ordering.)

3.  filter_sql = recipe_snapshot.get("filter", {}).get("where")   # may be None
    template_config = recipe_snapshot.get("schema", {}).get("config", {})
    prompt_template = template_config.get(
        "prompt_template",
        "Generate a question and answer for the following text:\n\n{chunk_text}\n\n"
        "Respond with JSON: {{\"instruction\": \"...\", \"output\": \"...\"}}",
    )
    val_ratio = (
        recipe_snapshot.get("output", {})
                       .get("splits", {})
                       .get("validation", 0.1)
    )
    fallback_on_failure = template_config.get("fallback_on_failure", True)
    max_tokens = template_config.get("max_tokens", 512)

4.  chunks = read_chunks_from_lance(filter_sql)
    # Returns list[dict] with keys "chunk_id", "text"

5.  qa_rows = []
    for chunk in chunks:
        prompt = prompt_template.format(chunk_text=chunk["text"])
        raw = call_llm_gateway(prompt, max_tokens=max_tokens)
        # call_llm_gateway: requests.post(.../api/internal/llm/completions)
        # parses resp.json()["content"] as JSON → {"instruction": str, "output": str}
        # On parse failure: if fallback_on_failure → skip (log warning); else raise
        if raw is not None:
            qa_rows.append({
                "instruction": raw["instruction"],
                "output":      raw["output"],
                "chunk_id":    chunk["chunk_id"],
            })

6.  train_rows, val_rows = deterministic_split(qa_rows, val_ratio)
    # Deterministic: int(hashlib.md5(row["chunk_id"].encode()).hexdigest(), 16) % 100
    #                < int(val_ratio * 100) → val bucket; else → train bucket.
    # md5 is collision-tolerant here (not cryptographic); fast and available stdlib.

7.  context.add_output_metadata({
        "dataset_id": MetadataValue.int(dataset_id),
        "train_count": MetadataValue.int(len(train_rows)),
        "val_count": MetadataValue.int(len(val_rows)),
        "chunks_processed": MetadataValue.int(len(chunks)),
        "chunks_skipped": MetadataValue.int(len(chunks) - len(qa_rows)),
    })

8.  return DatasetOutput(
        train_rows=train_rows,
        val_rows=val_rows,
        recipe_snapshot=recipe_snapshot,
        dataset_id=dataset_id,
        version_tag=version_tag,   # parsed in step 1
    )

─── HFDatasetIOManager.handle_output(context, obj: DatasetOutput) ─────────────

9.  For train_rows and val_rows separately:
    pa_table = pa.Table.from_pylist(
        rows,
        schema=pa.schema([("instruction", pa.string()), ("output", pa.string()), ("chunk_id", pa.string())])
    )
    buf = io.BytesIO(); pq.write_table(pa_table, buf); parquet_bytes = buf.getvalue()

10. s3 = boto3.client("s3", ...)   # same _build_s3_client() as extractor.py
    bucket = os.environ.get("MINIO_DATASETS_BUCKET", "datasets")
    prefix = f"{obj.dataset_id}_{obj.version_tag}"
    s3.put_object(Bucket=bucket, Key=f"{prefix}/data/train-00000.parquet", Body=train_bytes)
    s3.put_object(Bucket=bucket, Key=f"{prefix}/data/validation-00000.parquet", Body=val_bytes)
    s3.put_object(Bucket=bucket, Key=f"{prefix}/recipe.json",
                  Body=json.dumps(obj.recipe_snapshot, ensure_ascii=False, indent=2))
    s3.put_object(Bucket=bucket, Key=f"{prefix}/README.md",
                  Body=f"# Dataset {prefix}\n\nGenerated by sft_synthesis_qa.\n")

11. context.add_output_metadata({
        "train_rows":  MetadataValue.int(len(obj.train_rows)),
        "val_rows":    MetadataValue.int(len(obj.val_rows)),
        "dataset_uri": MetadataValue.text(f"s3://{bucket}/{prefix}/"),
    })
    # NOTE: dataset row status update (status='done', sample_count, size_bytes)
    # is deliberately NOT done here — that belongs to F-044.
    # NOTE: F-047 must add MINIO_DATASETS_BUCKET to FastAPI Settings
    # (apps/api/dataplat_api/config.py) before the download path is computed.
```

---

## Hard invariant compliance

| # | Invariant | Status | Notes |
|---|---|---|---|
| 1 | **Lineage mandatory** | ✅ | `recipe_snapshot` is read from `dataset.recipe_snapshot` (frozen at F-042 INSERT time). The materializer does not re-freeze the recipe — it uses the already-frozen snapshot, so lineage is preserved. `chunk_id` is included in the Parquet output for row-level lineage traceability (design doc §1.2 req. 5). |
| 2 | **Storage separation + CAS** | ✅ | Parquet bytes go to MinIO under a deterministic path (not CAS-addressed — user-facing artifacts, consistent with design doc §4.3). `recipe_snapshot` in Postgres stays as metadata. No blob bytes stored in Postgres. |
| 3 | **Schema frozen post-publish** | ✅ N/A | The recipe was already published before `POST /api/datasets/{recipe_id}/materialize` was called (F-040 freeze guard). The materializer reads `recipe_snapshot` (already immutable) and does not touch the `recipe` table. |
| 4 | **LLM calls through gateway** | ✅ | `sft_synthesis_qa.py` calls `requests.post(f"{LLM_GATEWAY_URL}/api/internal/llm/completions", ...)`. No `import anthropic`, no `import openai`, no direct `httpx.post("https://api.anthropic.com/...")` anywhere in the new files. |
| 5 | **Async SQLAlchemy** | ✅ N/A | No SQLAlchemy usage in `dagster/`. Postgres access uses `psycopg2` (sync), same as all other Dagster helpers. Invariant #5 is explicitly scoped to `apps/api/dataplat_api/`. |
| 6 | **OpenAPI ↔ TS type sync** | ✅ N/A | No API schema changes in this sprint. `make codegen` is not required. |

---

## Verification plan

### V1 — Parquet files exist in MinIO at the expected path

**Test**: `dagster/tests/test_hf_dataset_io_manager.py::test_handle_output_uploads_parquet`

Setup: construct a `DatasetOutput(train_rows=[...], val_rows=[...], recipe_snapshot={...}, dataset_id=7, version_tag="v1")`.  
Mock boto3: `patch("boto3.client")` → capture all `put_object` calls.  
Assert: `put_object` called with `Bucket="datasets"`, `Key="7_v1/data/train-00000.parquet"`.  
Assert: `put_object` called with `Bucket="datasets"`, `Key="7_v1/data/validation-00000.parquet"`.

The path pattern `{dataset_id}_{version_tag}` is confirmed by F-042's agreed.md §R4:
`hf_repo_uri = f"s3://datasets/{dataset.id}_{version_tag}"`.

### V2 — Parquet columns include `instruction` and `output`

**Test**: `dagster/tests/test_hf_dataset_io_manager.py::test_parquet_columns_instruction_output`

In `handle_output`, capture the `Body=` bytes passed to `put_object` for
`train-00000.parquet`, read back via `pa.ipc.open_file` or `pq.read_table`,
assert `"instruction"`, `"output"`, and `"chunk_id"` are in the table's schema field names.  
Alternatively, spy on `pq.write_table` to assert the pa.Table schema has those
three string columns.

### V3 — README.md and recipe.json exist alongside the Parquet files

**Test**: `dagster/tests/test_hf_dataset_io_manager.py::test_handle_output_uploads_readme_and_recipe`

Same mock setup as V1.  
Assert: `put_object` called with `Key="7_v1/README.md"`.  
Assert: `put_object` called with `Key="7_v1/recipe.json"`.

### V4 — `recipe_snapshot` in recipe.json matches the recipe definition at materialization time

**Test**: `dagster/tests/test_hf_dataset_io_manager.py::test_recipe_json_matches_snapshot`

Provide a non-trivial `recipe_snapshot = {"filter": {"where": "attr_quality_score > 0.7"}, "schema": {"template": "sft_synthesis_qa"}}`.  
Capture the `Body=` bytes for `Key="7_v1/recipe.json"`.  
`assert json.loads(body_bytes) == recipe_snapshot`.

### V5 — No direct SDK imports in the materializer

**Test (static)**: `dagster/tests/test_sft_synthesis_qa.py::test_no_direct_llm_sdk_imports`

```python
import ast, pathlib

def test_no_direct_llm_sdk_imports():
    src = pathlib.Path("dagster/dagster_platform/sft_synthesis_qa.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) \
                    else ([node.module] if node.module else [])
            for name in names:
                assert "anthropic" not in name
                assert "openai" not in name
```

Same test repeated for `hf_dataset_io_manager.py`.

**Behavioural test**: `dagster/tests/test_sft_synthesis_qa.py::test_call_llm_gateway_uses_requests_post`

Mock `requests.post` → confirm `call_llm_gateway()` calls it with the internal
endpoint URL and does not attempt any other outbound call.

**Additional unit tests in `test_sft_synthesis_qa.py`:**

| Test | What it exercises |
|---|---|
| `test_parse_dataset_partition_key_valid` | `"ds_5_v2"` → `(5, "v2")` |
| `test_parse_dataset_partition_key_invalid` | Malformed key → `ValueError` |
| `test_read_chunks_from_lance_with_filter` | Mock lancedb: `.where()` called with filter SQL |
| `test_read_chunks_from_lance_no_filter` | Mock lancedb: `.where()` NOT called when filter is None |
| `test_call_llm_gateway_happy_path` | `requests.post` returns `{"content": '{"instruction":"Q","output":"A"}', "model":"mock"}` → returns dict |
| `test_call_llm_gateway_parse_failure_fallback_true` | Malformed JSON → returns `None` (not raised) |
| `test_call_llm_gateway_parse_failure_fallback_false` | Malformed JSON with `fallback_on_failure=False` (sourced from `template_config` in recipe, not a per-call parameter) → raises |
| `test_deterministic_split_reproducible` | Same input → same train/val assignment on repeated calls |
| `test_deterministic_split_ratio_approx` | 1000 rows, val_ratio=0.1 → ~10% in val bucket (±3%) |
| `test_deterministic_split_zero_val` | val_ratio=0.0 → all rows in train |

**Mock `ctx` / `context` for the asset-level integration test:**

`dagster/tests/test_sft_synthesis_qa.py::test_dataset_asset_end_to_end` — patches all
external I/O (lancedb, psycopg2, requests.post), calls `_run_dataset_asset(partition_key="ds_5_v1")` (a thin wrapper that exercises the asset body logic without a Dagster runtime), asserts the returned `DatasetOutput` has both non-empty lists and `recipe_snapshot` equals the mocked DB value.  
The integration test uses the pure-function wrapper pattern (`_run_dataset_asset(partition_key)`) — consistent with the `quality_tagger.py`-style precedent. `dagster.materialize()` is NOT used (it would require a live Dagster runtime and registered resources).

---

## Out of scope

- **Dataset row status update** (`status='done'`, `sample_count`, `size_bytes`, `stats`, `materialized_at`) — F-044 owns the `HFDatasetIOManager` post-write Postgres update.
- **HF-compatible README.md content** — the stub `"# Dataset {prefix}\n\nGenerated by sft_synthesis_qa."` is intentionally minimal; F-044 will flesh out the dataset card.
- **Streaming sharding** — the design doc mentions `train-00000.parquet` / `train-00001.parquet` and `validation-00000.parquet` / `validation-00001.parquet` shards. F-043 writes a single shard per split (shard_size_mb logic is deferred).
- **Per-source sampling caps** (`recipe.sampling.per_source_caps`) — deferred to v2 (design doc §12.1 non-goals).
- **recipe.definition validation against `config_schema`** — deferred to F-082.
- **Multiple schema templates** (cpt_plain, dpo_two_model, etc.) — only `sft_synthesis_qa` is implemented here.
- **Dataset download / list endpoints** — F-045 onwards.
- **sft_synthesis_qa operator row** — deferred to F-092 (feature_list.json, passes: false). The operator row seed is a separate deliverable.
- **dataset_infos.json** — deferred to F-044 (F-044 verification criterion owns this file).
- **MINIO_DATASETS_BUCKET FastAPI Settings entry** — deferred to F-047 (download endpoint sprint). F-047 must add `MINIO_DATASETS_BUCKET` to `apps/api/dataplat_api/config.py` before the download path is computed.

---

## Decisions (resolved by reviewer Mode A)

1. **LLM call pattern**: Use `requests.post(LLM_GATEWAY_URL + "/api/internal/llm/completions", ...)`. The `quality_tagger.py` precedent confirms this. The enforceable criterion is "no direct SDK imports" (V5 AST walk); `ctx.llm.call()` in the design doc is a conceptual description, not a mandatory call signature.

2. **val_ratio source**: Read from `recipe_snapshot["output"]["splits"]["validation"]`, with fallback to 0.1 when the key is absent. Do NOT read from the operator `config_schema`.

3. **Operator row**: Deferred to F-092. The materializer reads config from `recipe_snapshot["schema"]["config"]` directly — no operator table query at runtime.

4. **MINIO_DATASETS_BUCKET in FastAPI Settings**: Deferred to F-047. The Dagster layer uses `os.environ.get("MINIO_DATASETS_BUCKET", "datasets")` directly; F-047 (download endpoint) owns the FastAPI `Settings` entry.

5. **Zero-row materialization**: Allowed; log a warning. Two zero-row Parquet files are valid HuggingFace artifacts. Raising would leave the dataset row at `status='pending'` indefinitely, which is worse.

6. **chunk_id in Parquet output**: Included as a third column (`pa.string()`). Required by design doc §1.2 req. 5 (row-level traceability to source document page).

7. **max_tokens**: Configurable via `recipe_snapshot["schema"]["config"]["max_tokens"]` with fallback to 512. Follows the same `template_config.get(...)` pattern used for `prompt_template` and `fallback_on_failure`.

8. **DatasetOutput type**: Use `@dataclass`. `TypedDict` is for dict-shaped data; `DatasetOutput` is a structured domain object passed between system components and benefits from instantiation-time type validation.
