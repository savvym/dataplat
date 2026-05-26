# S023-F-023: Lance Global Chunks Table — Schema Initialization

**Feature**: Lance global chunks table is initialized with the correct Arrow schema (§4.2) on
first use; the table is created at `s3://lance/chunks/`.

**Status**: PROPOSED

**Dependencies**: F-003 (MinIO buckets created on startup) — PASSED ✓

---

## Summary

F-023 establishes the Lance dataset at `s3://lance/chunks/` and gives it the canonical 24-field
Arrow schema from design doc §4.2.  Nothing writes actual chunk rows yet — that is F-025.  This
sprint is purely infrastructure: a storage module, the schema constant, a
`get_or_create_chunks_table()` helper, config extension, unit tests, and a `lance` verification
layer in `checks.sh`.

### Verification criteria (from feature_list.json)

1. After system startup and first chunking run, `lance.dataset('s3://lance/chunks/').schema`
   contains all required fields including `chunk_id`, `source_id`, `text`, `token_count`,
   `attr_quality_score`, `attr_lang_code`, `attr_minhash_signature`.
2. The table path is accessible via the MinIO SDK.

---

## What will be built

### Files to create

**`apps/api/dataplat_api/storage/lance.py`** (NEW)

The central artifact for this sprint.  Provides:

- `CHUNKS_SCHEMA` — a `pa.Schema` constant with all 24 fields from design doc §4.2, in the exact
  order listed there.
- `make_lance_storage_options() -> dict[str, str]` — builds the S3-compatible storage_options
  dict from `settings`.  Separated from `get_or_create_chunks_table()` so unit tests can assert
  the dict contents without a live MinIO connection.
- `get_or_create_chunks_table() -> lance.LanceDataset` — opens the dataset at
  `s3://{settings.MINIO_LANCE_BUCKET}/chunks/` if it already exists; on any exception (table not
  yet present), writes an empty dataset carrying only the schema (0 rows), then returns it.

The URI is computed inside the function from `settings.MINIO_LANCE_BUCKET`, not at module-load
time as a constant, so it respects env-var overrides correctly.

**`apps/api/dataplat_api/storage/__init__.py`** (CREATE IF ABSENT)

If this file does not already exist (it almost certainly does, given F-011's `s3.py`), create
it as an empty file.  Python will not resolve the `dataplat_api.storage.lance` import without it.

**`apps/api/tests/test_lance_storage.py`** (NEW)

Unit tests that run under the existing `backend` layer (no live MinIO):

- `test_chunks_schema_field_count` — asserts `len(CHUNKS_SCHEMA) == 24`.
- `test_chunks_schema_has_all_required_fields` — asserts the 7 specifically-cited verification
  fields (`chunk_id`, `source_id`, `text`, `token_count`, `attr_quality_score`, `attr_lang_code`,
  `attr_minhash_signature`) are in `CHUNKS_SCHEMA.names`.
- `test_chunks_schema_key_field_types` — spot-checks a representative sample of field types:
  `chunk_id` → `pa.string()`, `source_id` → `pa.int64()`, `text` → `pa.large_string()`,
  `token_count` → `pa.int32()`, `attr_quality_score` → `pa.float32()`,
  `attr_minhash_signature` → `pa.list_(pa.uint64())`,
  `attr_embed_vector` → `pa.list_(pa.float32(), 1024)`,
  `attr_pii_has_pii` → `pa.bool_()`,
  `created_at` → `pa.timestamp("ms")`,
  `updated_at` → `pa.timestamp("ms")`.
- `test_make_lance_storage_options_shape` — calls `make_lance_storage_options()` and asserts the
  returned dict contains keys `aws_access_key_id`, `aws_secret_access_key`, `endpoint`,
  `aws_region`, `allow_http`; that `endpoint` starts with `"http://"` and embeds
  `settings.MINIO_ENDPOINT`; and that `allow_http == "true"`.

### Files to modify

**`apps/api/dataplat_api/config.py`**

Add one new field to `Settings`:

```python
# Added S023-F-023: Lance bucket for the global chunks table.
# Matches the bucket created by minio-init (F-003). Default "lance" so
# no docker-compose.dev.yml change is needed.
MINIO_LANCE_BUCKET: str = "lance"
```

Update the module docstring to mention S023-F-023.

**`apps/api/pyproject.toml`**

Add `lancedb` (exact version pinned at implementation time, following repo convention).
`pyarrow` and `lance` are transitive deps of `lancedb` and MUST NOT be listed separately.

```toml
# Added S023-F-023: Lance columnar store for global chunks table.
# pyarrow + lance come as transitive deps — do NOT list separately.
"lancedb==<latest-stable-0.x>",
```

**`verify/checks.sh`**

Add a `lance)` layer (see Verification Plan section for the full script body).
Add `bash "$0" lance` to the `all)` chain, after `documents`.

### Files unchanged

- `apps/api/dataplat_api/storage/s3.py` — aioboto3 client stays as-is; V2 of the lance check
  reuses the boto3 pattern from the `buckets` layer.
- `apps/api/dataplat_api/main.py` — no new router; this is storage infrastructure only.
- `docker/docker-compose.dev.yml` — no new env vars needed; fastapi container already exposes
  `MINIO_ENDPOINT`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`.
- All Dagster files — Lance IO managers are F-025+.
- All migration files — no Postgres schema change.

---

## Design decisions

**D1 — Use `lance` API directly, not the `lancedb` high-level API.**

The verification criterion is `lance.dataset('s3://lance/chunks/').schema`.  The `lancedb`
high-level `db.create_table("chunks", ...)` call creates the directory at
`s3://lance/chunks.lance/` (appends `.lance` suffix).  That would fail the criterion.  Using
`lance.write_dataset(empty_tbl, "s3://lance/chunks/", ...)` writes to the exact path specified.
The installed package is still `lancedb` (D2); the import used in production code is `import
lance` (which comes from the `lance` dep bundled inside `lancedb`).

**D2 — `lancedb` is the package in `pyproject.toml`; `lance` is NOT listed separately.**

`lancedb` depends on `lance` and `pyarrow`; those come in transitively.  Listing them
separately would risk version conflicts.  Implementer pins `lancedb` to the latest stable
`0.x` release at implementation time (follow the exact-pin repo convention).

**D3 — `get_or_create_chunks_table()` is synchronous.**

Lance's S3 I/O (backed by the Rust `object_store` crate) is synchronous from Python.  No
async wrapper is introduced for MVP.  Dagster assets (which will call this in F-025) are
already synchronous.  If FastAPI routes ever need it, they will wrap with
`asyncio.to_thread()` — that decision is deferred to the sprint that wires it into a route.

**D4 — "On first use" means a lazy try-open / create-on-exception pattern.**

```python
def get_or_create_chunks_table() -> lance.LanceDataset:
    storage_options = make_lance_storage_options()
    uri = f"s3://{settings.MINIO_LANCE_BUCKET}/chunks/"
    try:
        return lance.dataset(uri, storage_options=storage_options)
    except (FileNotFoundError, OSError) as exc:
        # Only create a new empty table when the path genuinely does not exist.
        # Re-raise anything else (permission denied, network error, corrupted manifest).
        if "does not exist" not in str(exc).lower() and "not found" not in str(exc).lower():
            raise
        empty_tbl = CHUNKS_SCHEMA.empty_table()   # pa.Schema.empty_table() — see fallback below
        return lance.write_dataset(
            empty_tbl, uri, schema=CHUNKS_SCHEMA, storage_options=storage_options
        )
```

The exception clause uses `(FileNotFoundError, OSError)` as a safe starting point.  The
implementer **must** test `lance.dataset()` against a nonexistent S3 prefix to identify the
exact exception type(s) raised by the installed `lance` version (could be `lance.LanceError`
or a subclass), and document the chosen form in the module docstring.

`CHUNKS_SCHEMA.empty_table()` creates a zero-row `pa.Table` with all 24 typed columns,
including fixed-size lists and timestamps.  Use the following pattern and document in the
module docstring which form was used and the resolved pyarrow version:

```python
# Preferred (PyArrow >= 14): CHUNKS_SCHEMA.empty_table()
# Fallback if AttributeError:
arrays = [pa.array([], type=field.type) for field in CHUNKS_SCHEMA]
empty_tbl = pa.table(dict(zip(CHUNKS_SCHEMA.names, arrays)))
# Note: pa.list_(pa.float32(), 1024) and pa.list_(pa.uint64()) support pa.array([], type=...)
```

Implementer must also confirm that `lance.write_dataset` accepts a zero-row table as the
schema source.

**D5 — Storage options dict uses lowercase keys matching the lance/object_store convention.**

```python
{
    "aws_access_key_id":     settings.MINIO_ROOT_USER,
    "aws_secret_access_key": settings.MINIO_ROOT_PASSWORD,
    "endpoint":              f"http://{settings.MINIO_ENDPOINT}",
    "aws_region":            "us-east-1",
    "allow_http":            "true",
}
```

`allow_http` must be the string `"true"` (not a bool) as required by `object_store`.
`aws_region` must be present even though MinIO ignores it (the AWS SDK validates its presence).
`endpoint` uses the same `f"http://{settings.MINIO_ENDPOINT}"` construction as `s3.py`.
**The implementer must verify these key names against the installed lancedb/lance version**,
because object_store sometimes accepts uppercase aliases (`AWS_ACCESS_KEY_ID`, `AWS_ENDPOINT`,
etc.) in addition to lowercase.  The recommended trial order for the `endpoint` key is:

```
Recommended trial order:
1. "endpoint"          (lancedb >= 0.6 / object_store 0.9)
2. "aws_endpoint"      (lancedb <= 0.5 / object_store 0.7)
3. "aws_endpoint_url"  (lancedb 0.10+ or rust object_store 0.10+)
```

Either casing is acceptable if it works; document the exact key + lancedb version it was
verified against in a comment inside `make_lance_storage_options()`.

**D6 — `MINIO_LANCE_BUCKET = "lance"` is a new Settings field with a Python default.**

The `lance` bucket already exists (minio-init creates it as part of F-003).  The Python default
`"lance"` matches the bucket name, so no change to `docker-compose.dev.yml` is needed.  This
follows the same pattern as `MINIO_SOURCES_BUCKET` and `MINIO_DOCUMENTS_BUCKET`.

**D7 — No FastAPI lifespan hook for table creation.**

Table creation is deferred to first use (lazy), not wired into the FastAPI `lifespan` startup
sequence.  This avoids adding a new synchronous blocking call to startup and prevents startup
failures if the Lance table has a transient I/O error.  The checks.sh `lance` layer is
responsible for triggering first-use during integration verification.

**D8 — `CHUNKS_SCHEMA` is a module-level constant; the URI is not.**

`CHUNKS_SCHEMA` is a `pa.schema([...])` constant — pyarrow schema objects are cheap and
immutable, making a module-level constant appropriate and efficient.  The URI
`f"s3://{settings.MINIO_LANCE_BUCKET}/chunks/"` is computed inside
`get_or_create_chunks_table()` (and `make_lance_storage_options()`) at call time so that env-var
overrides to `MINIO_LANCE_BUCKET` are respected without requiring a module reload.

---

## Verification plan

### Criterion 1 → `lance` layer V1

After calling `get_or_create_chunks_table()` inside the running `fastapi` container, inspect
`dataset.schema.names` for the 7 required fields cited in the feature specification.  All 24
schema fields are also verified by the unit tests (backend layer).

### Criterion 2 → `lance` layer V2

Use boto3 (already available in the `fastapi` container image) to call
`s3.list_objects_v2(Bucket='lance', Prefix='chunks/')` and assert that at least one object key
is returned.  Lance writes a manifest/version directory even for a zero-row table, so any
successful `get_or_create_chunks_table()` call guarantees at least one S3 object.

### Full `lance)` layer body

```bash
lance)
  COMPOSE="docker/docker-compose.dev.yml"
  [[ -f "$COMPOSE" ]] || { echo "no $COMPOSE yet"; exit 0; }

  MINIO_USER="${MINIO_ROOT_USER:-minioadmin}"
  MINIO_PASS="${MINIO_ROOT_PASSWORD:-devpassword}"

  echo "--- lance V1: get_or_create_chunks_table creates table with correct schema ---"
  # The fastapi container already has MINIO_ENDPOINT/ROOT_USER/ROOT_PASSWORD injected
  # by docker-compose.dev.yml; make_lance_storage_options() reads them via settings.
  docker compose -f "$COMPOSE" exec -T fastapi python -c "
from dataplat_api.storage.lance import get_or_create_chunks_table
dataset = get_or_create_chunks_table()
schema_names = dataset.schema.names
required = [
    'chunk_id', 'source_id', 'text', 'token_count',
    'attr_quality_score', 'attr_lang_code', 'attr_minhash_signature',
]
for field in required:
    assert field in schema_names, f'missing field {field!r}; schema has: {schema_names}'
assert len(schema_names) == 24, f'expected 24 fields, got {len(schema_names)}: {schema_names}'
print('  V1 OK: all required fields present; schema field count =', len(schema_names))
import sys; sys.exit(0)
" || { echo "FAIL: lance V1 schema check failed"; exit 1; }

  echo "--- lance V2: table path accessible via MinIO SDK ---"
  docker compose -f "$COMPOSE" exec -T \
    -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
    fastapi python -c "
import boto3, os, sys
s3 = boto3.client('s3', endpoint_url='http://minio:9000',
    aws_access_key_id=os.environ['S3_USER'],
    aws_secret_access_key=os.environ['S3_PASS'])
result = s3.list_objects_v2(Bucket='lance', Prefix='chunks/')
keys = [obj['Key'] for obj in result.get('Contents', [])]
if not keys:
    print('FAIL: no objects found at s3://lance/chunks/ — table was not written', file=sys.stderr)
    sys.exit(1)
print('  V2 OK: found', len(keys), 'objects at s3://lance/chunks/; first =', keys[0])
sys.exit(0)
" || { echo "FAIL: lance V2 MinIO SDK check failed"; exit 1; }
  ;;
```

`all)` chain update: add `bash "$0" lance` after `bash "$0" documents`.

### Criterion coverage summary

| Criterion | Layer | Check |
|---|---|---|
| Schema contains required fields | `backend` (unit) | `test_chunks_schema_has_all_required_fields` |
| Schema contains required fields | `lance` V1 | `dataset.schema.names` assertion |
| Table at `s3://lance/chunks/` accessible via SDK | `lance` V2 | `boto3.list_objects_v2` non-empty |

---

## Out of scope

- **Writing chunk rows** — F-025 (chunking operator).  This sprint creates schema only (0 rows).
- **`lancedb` high-level API** (`lancedb.connect`, `tbl.search`, etc.) — deferred to F-025+.
- **Dagster IO managers for Lance** — deferred to F-025+.
- **FastAPI route / endpoint** that exposes the chunks table — not planned for MVP.
- **Async wrapper** around `get_or_create_chunks_table` — deferred to the sprint that wires it
  into a FastAPI route (if that ever happens in MVP).
- **`pyarrow` or `lance` explicit pins** in `pyproject.toml` — they come from `lancedb` as
  transitive deps; explicit listing risks version conflicts.
- **`docker-compose.dev.yml` changes** — not needed; `MINIO_LANCE_BUCKET` defaults to `"lance"`
  in Python, and the other MinIO env vars are already injected.
- **Alembic migration** — no Postgres schema change.
- **OpenAPI / `make codegen`** — no new API routes, so no TS type sync required.
