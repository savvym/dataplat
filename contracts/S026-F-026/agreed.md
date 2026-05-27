# S026-F-026 — LanceChunksIOManager Row Mode — Agreed Contract

> Derived from `proposed.md` with all 3 findings from `feedback.md` addressed:
> C1 HIGH (has_partition_key guard), C2 MEDIUM (dedicated count snippet for V5),
> C3 NIT (§2 imports named).

---

## 1. What

Create a proper Dagster `IOManager` subclass — `LanceChunksIOManager` — that owns the
delete-before-insert idempotency pattern for the Lance `chunks` table. Refactor the
existing `chunks` asset to delegate all Lance writes to this IO manager (via
`io_manager_key="lance_chunks_io"`) rather than calling `write_chunks_to_lance()`
directly. Wire the IO manager resource into the Dagster `Definitions` object. Extend
`verify/checks.sh chunks)` with two new integration checks (V5 and V6) that re-run the
chunking pipeline for the same source and assert that the Lance row count is unchanged
(not doubled) and that no duplicate `chunk_id` values exist.

The `write_chunks_to_lance()` helper in `chunker.py` is superseded but intentionally
retained (it is already unit-tested and removing it would require updating
`test_chunker.py`; the function simply stops being called from the asset). Column mode
(tagger category) is **not** implemented here — that is deferred to F-028.

---

## 2. Files Changed / Created

| Path | Action | Purpose |
|------|--------|---------|
| `dagster/dagster_platform/lance_io_manager.py` | **CREATE** | `LanceChunksIOManager` class with row-mode `handle_output()` and a `NotImplementedError` `load_input()`; imports `CHUNKS_SCHEMA` and `build_lance_storage_options` from `chunker.py` |
| `dagster/dagster_platform/definitions.py` | **MODIFY** | (a) Remove `write_chunks_to_lance` import, (b) annotate `chunks` with `io_manager_key="lance_chunks_io"`, (c) return rows list instead of calling `write_chunks_to_lance()` directly, (d) add IO manager to `Definitions(resources={...})` |
| `dagster/dagster_platform/chunker.py` | **MODIFY** (comment only) | Add a one-line comment to `write_chunks_to_lance()` noting it is superseded by `LanceChunksIOManager`; no logic change |
| `verify/checks.sh` | **MODIFY** | Append V5 (idempotency row-count check) and V6 (no-duplicate chunk_ids check) to the `chunks)` layer |

No migrations, no API schema changes, no `apps/api/` changes, no `packages/api-types/`
changes.

---

## 3. Design Decisions

### D1 — New file `lance_io_manager.py`, not appended to `chunker.py`

The IO manager is a different abstraction level from the pure helpers in `chunker.py`
(which have no Dagster dependency). Keeping it in a separate module maintains the
existing `chunker.py` guarantee: importable outside Dagster for unit testing. The IO
manager imports from `chunker` (for `CHUNKS_SCHEMA` and `build_lance_storage_options()`)
but not vice-versa.

### D2 — `lancedb` API, not raw `lance.dataset()`

The design doc §8.2 pseudocode uses `lance.dataset(self.table_uri)`. F-025 already
established that `lancedb.connect() / db.create_table(..., exist_ok=True)` is the
working pattern for lancedb==0.30.2 against MinIO. The IO manager uses the same
`lancedb` API for consistency and to avoid introducing a second dependency path.

### D3 — No constructor arguments; all config from `os.environ`

The IO manager lives entirely in the Dagster container. All config (MinIO endpoint,
credentials, bucket name) is injected via environment variables — same pattern as
`build_lance_storage_options()` and `build_s3_client()` in the existing codebase.
There is no Pydantic `Settings` dependency in this package.

### D4 — `handle_output()` parameter contract

`obj` is a `list[dict[str, Any]]` — exactly what `fixed_size_chunk()` currently returns
and what the refactored `chunks` asset will return. The IO manager does **not** accept
`pyarrow.Table` at this stage; lancedb's `table.add()` accepts `list[dict]` natively.
If future assets need to pass an Arrow table, that can be added without changing the
interface (lancedb accepts both).

### D5 — `source_id` extraction from `context.partition_key`

Partition keys follow the established `src_{source_id}` convention (set by F-012 /
FastAPI upload). The IO manager uses a **two-step guard pattern** (C1 fix):

```python
if not context.has_partition_key:
    raise ValueError(
        "LanceChunksIOManager requires a partitioned asset; "
        "context.has_partition_key is False"
    )
source_id = int(context.partition_key.removeprefix("src_"))
```

`context.has_partition_key` is checked FIRST because `context.partition_key` raises
`DagsterInvariantViolationError` (not returns falsy) when no partition exists. This
produces a clean error message instead of a Dagster-internal traceback.

### D6 — `producer_asset` from `context.asset_key.path[-1]`

The IO manager reads `context.asset_key.path[-1]` (e.g. `"chunks"`) to build the Lance
delete predicate `AND producer_asset = '{asset_name}'`. This matches the design doc §8.2
and means the IO manager is reusable for future chunker/augmenter assets without
modification.

### D7 — Row mode only; no category lookup from Postgres

The design doc §8.2 mentions looking up operator category from Postgres to dispatch
row-mode vs. column-mode. F-026 implements row mode only. The category lookup is
deferred to F-028 (column mode / tagger). For now the IO manager's `handle_output()`
always executes row mode (delete + insert). A TODO comment marks the dispatch point.

### D8 — `load_input()` raises `NotImplementedError`

No downstream Dagster asset currently reads chunks through this IO manager; downstream
processors will connect to Lance directly. `load_input()` raises `NotImplementedError`
with a descriptive message. This is consistent with how other write-only IO managers are
handled in Dagster.

### D9 — `chunks` asset switches from returning `MaterializeResult` to returning `list[dict]`

When `io_manager_key` is set on an asset, Dagster routes the return value to the IO
manager's `handle_output()`. The asset must return the data, not a `MaterializeResult`.
Materialization metadata (source_id, chunk_count, text_length) moves from the
`MaterializeResult` constructor to `context.add_output_metadata()` called inside the
asset body before the return. IO-level metadata (row_count written, mode) is added via
`context.add_output_metadata()` inside `handle_output()`. Both sets of metadata appear
on the same materialization event in the Dagster UI.

### D10 — `write_chunks_to_lance()` is kept but superseded

Rather than removing the function and updating its unit tests (which would widen scope),
the function is retained with a comment. The asset no longer imports or calls it. It
becomes dead code that can be cleaned up in a future sprint.

### D11 — Empty `obj` early-return in `handle_output()`

If `obj` is an empty list, the IO manager returns immediately without touching Lance.
This guards against edge cases (e.g. a source whose text extracts to zero tokens after
the fallback chain). The delete step is skipped too — there is nothing to replace.

---

## 4. Verification Plan

### Existing checks (must remain green)

All existing `chunks)` checks V1–V4 must continue to pass unchanged:
- V1: Lance row count > 0 for source after first run
- V2: `chunk_id` matches `{source_id}_{seq}` pattern
- V3: `text` non-null, `0 < token_count ≤ 512`
- V4: `augmented_from=null`, `attr_*=null`, `producer_asset='chunks'`

### New checks (F-026 criteria)

**V5 — Idempotency: re-run does not double row count** (criterion 1)

After V4 completes, capture the current Lance row count for the test source using a
**dedicated count-only Python snippet** (C2 fix — this is NOT the same as V1's
human-readable output):

```bash
CH_COUNT1=$(docker compose -f "$COMPOSE" exec -T fastapi python -c "
import lancedb, os
db = lancedb.connect(
    f\"s3://{os.environ['MINIO_LANCE_BUCKET']}/chunks\",
    storage_options={
        'aws_access_key_id': os.environ['MINIO_ROOT_USER'],
        'aws_secret_access_key': os.environ['MINIO_ROOT_PASSWORD'],
        'endpoint': f\"http://{os.environ['MINIO_ENDPOINT']}\",
        'aws_region': 'us-east-1',
        'allow_http': 'true',
    })
t = db.open_table('chunks')
n = t.count_rows(\"source_id = ${CH_SRC_ID} AND producer_asset = 'chunks'\")
print(n)
")
```

Then trigger a **second** backfill of the `chunks` asset for the same `CH_SRC_ID`,
poll it to `COMPLETED_SUCCESS` (≤120 s, 40×3 s sleep), then capture count again:

```bash
CH_COUNT2=$(docker compose ... same snippet ...)
[ "$CH_COUNT2" -eq "$CH_COUNT1" ] || { echo "FAIL V5: count changed from $CH_COUNT1 to $CH_COUNT2"; exit 1; }
echo "  V5 OK: idempotent row count $CH_COUNT1 == $CH_COUNT2"
```

If the IO manager's delete step is absent or broken, `CH_COUNT2` will be `2 × CH_COUNT1`.

**V6 — No duplicate chunk_ids** (criterion 2)

After V5 completes, run a single Python snippet inside the `fastapi` container:

```bash
docker compose -f "$COMPOSE" exec -T fastapi python -c "
import lancedb, os, sys
db = lancedb.connect(
    f\"s3://{os.environ['MINIO_LANCE_BUCKET']}/chunks\",
    storage_options={
        'aws_access_key_id': os.environ['MINIO_ROOT_USER'],
        'aws_secret_access_key': os.environ['MINIO_ROOT_PASSWORD'],
        'endpoint': f\"http://{os.environ['MINIO_ENDPOINT']}\",
        'aws_region': 'us-east-1',
        'allow_http': 'true',
    })
t = db.open_table('chunks')
rows = t.search().where(\"source_id = ${CH_SRC_ID} AND producer_asset = 'chunks'\").select(['chunk_id']).to_list()
ids = [r['chunk_id'] for r in rows]
assert len(ids) == len(set(ids)), f'duplicate chunk_ids: {len(ids)} rows, {len(set(ids))} unique'
print(f'  V6 OK: {len(ids)} chunk_ids, all unique')
" || { echo "FAIL V6: duplicate chunk_ids detected"; exit 1; }
```

Both V5 and V6 are appended to the existing `chunks)` case in `verify/checks.sh` and
run in the same shell context (sharing `CH_SRC_ID`, `CH_TOKEN`, `COMPOSE`, etc.).

### Unit tests

The existing `dagster/tests/test_chunker.py` covers `fixed_size_chunk()`,
`extract_text_from_document()`, and related helpers — these are unaffected. No new unit
test file is required for the IO manager itself: the idempotency guarantee is an
integration property (it requires a real Lance table in MinIO) and is covered end-to-end
by V5 and V6 above.

---

## 5. Invariant Compliance

| # | Invariant | Assessment |
|---|-----------|------------|
| 1 | **Lineage mandatory** | Not applicable — Lance chunk storage is not a lineage-tracked commit. The `run` table in Postgres (populated by F-024) records the Dagster backfill ID for the asset execution; that path is unchanged. |
| 2 | **Storage separation + CAS** | Chunk bytes continue to live in MinIO/Lance (`s3://lance/chunks`). Nothing in this sprint writes blob data to Postgres. ✓ |
| 3 | **Schema frozen post-publish** | Not applicable — Lance `chunks` table is not a Silver/Gold commit-based repo. The `CHUNKS_SCHEMA` constant in `chunker.py` is reused verbatim by the IO manager; no schema change occurs. ✓ |
| 4 | **LLM calls via gateway** | No LLM calls introduced. ✓ |
| 5 | **Async SQLAlchemy in `apps/api/`** | IO manager lives in `dagster/dagster_platform/`, not `apps/api/`. No SQLAlchemy usage whatsoever in this sprint. ✓ |
| 6 | **OpenAPI ↔ TS type sync** | No API schema changes. `make codegen` not required. ✓ |

---

## 6. Risks / Open Questions

### R1 — `context.partition_key` availability inside `OutputContext`

**Risk:** Dagster's `OutputContext` exposes `partition_key` only when the asset is
partitioned. If `LanceChunksIOManager` is accidentally attached to a non-partitioned
asset, `context.partition_key` raises `DagsterInvariantViolationError`.

**Mitigation (C1 fix):** Guard on `context.has_partition_key` before accessing
`context.partition_key`. See D5 for the exact code pattern.

### R2 — `context.add_output_metadata()` inside `handle_output()`

**Risk:** Low. Dagster 1.11.16 allows `context.add_output_metadata()` inside IO
manager `handle_output()`. The version is pinned.

### R3 — Second backfill in V5 may race with in-flight first run

**Risk:** Cannot happen — V5's second backfill is only triggered AFTER the first
backfill reaches `COMPLETED_SUCCESS` (verified by V1 prior to V5 running).

### R4 — `write_chunks_to_lance()` dead code retention

**Risk:** Function remains but is no longer called. Comment added (D10) references
this sprint and the IO manager by name. Future cleanup is low-risk.

### R5 — `io_manager_key` interaction with Dagster 1.11.16

**Risk:** None. `io_manager_key` has been stable since Dagster 0.14. Confirmed installed.

### R6 — `MaterializeResult` removal breaks existing F-025 checks

**Risk:** Low. V1–V4 in `chunks)` layer query Lance directly; they don't inspect
`MaterializeResult`. Switching return type to `list[dict]` + `context.add_output_metadata()`
does not affect V1–V4. All must remain green (verified in this sprint).
