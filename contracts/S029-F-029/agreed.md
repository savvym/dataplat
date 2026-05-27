# S029-F-029 — lang_fasttext tagger: agreed.md

## Objective

Implement the `attr_lang` Dagster asset that populates `attr_lang_code` (ISO 639-1
two-letter code, e.g. `"en"`) and `attr_lang_confidence` (float32 in [0.0, 1.0]) on
every existing `producer_asset='chunks'` row in the Lance `chunks` table for a given
`source_id`. Language detection uses the fasttext `lid.176.ftz` compressed model
(~900 KB). Zero new rows are created; only those two columns are overwritten on
existing rows (column-mode update, same contract as `attr_quality`/F-028). The feature
wires up the full call path: FastAPI `POST /api/runs` (new `"attr_lang"` asset value)
→ `DagsterGateway.launch_attr_lang_backfill()` → Dagster `attr_lang` asset →
`lang_tagger.update_lang_in_lance()` → fasttext classifier → per-row
`table.update(where=..., values=...)` in Lance.

---

## Design decisions

### D1 — fasttext package: `fasttext-langdetect==1.1.1`

**Implementer note (version change):** `fasttext-langdetect==1.0.6` is no longer
available on PyPI (confirmed at implementation time via `pip download` — only 1.1.0
and 1.1.1 are available). Using `fasttext-langdetect==1.1.1` per the D1 gate ("if
1.0.6 is unavailable, implementer must update agreed.md with the new version before
committing").

**Breaking changes from 1.0.6 → 1.1.1:**
- Import module renamed: `fasttext_langdetect` → `ftlangdetect`. All code uses
  `from ftlangdetect import detect`.
- The `lang` field is now stripped of `__label__` prefix internally (line 226 of
  detect.py: `lang=label.replace("__label__", "")`). Our `.replace("__label__", "")`
  call is a safe no-op / regression guard.
- `detect()` raises `ValueError` for empty/whitespace text (handled by D5 guard).
- `low_memory=True` parameter selects lid.176.ftz (~900 KB compressed model).
- The model is NOT bundled in the wheel — it is downloaded at first call and cached
  at the path specified by `FTLANG_CACHE` env var.

**R1 resolution:** `fasttext-predict==0.9.2.4` (C extension dependency) has a
pre-compiled binary wheel for `cp312-manylinux2014_x86_64`. No gcc/g++ needed.

**Build-time model warm-up**: add one `RUN python -c` bake step to the Dockerfile
with `FTLANG_CACHE` env set to `/app/fasttext-models` (non-temp path persists in
image layer):

```dockerfile
ENV FTLANG_CACHE=/app/fasttext-models
RUN python -c "from ftlangdetect import detect; detect('hello world', low_memory=True)"
```

**Version pin is exact** — no "or latest stable" escape hatch.

### D2 — fasttext is NOT an LLM SDK (CLAUDE.md invariant #4 does not apply)

fasttext is a classical ML classifier. CLAUDE.md invariant #4 ("LLM calls go through
the gateway") applies only to generative LLM SDKs (Anthropic, OpenAI, etc.).
`lang_tagger.py` may import and call `ftlangdetect.detect()` directly — no
HTTP gateway call required, no `requests` usage. This keeps the implementation
simple, fast, and self-contained.

### D3 — No Dagster imports in `lang_tagger.py` (same as `quality_tagger.py`)

`dagster/dagster_platform/lang_tagger.py` must have zero Dagster imports, zero
`requests` imports, and zero LLM SDK imports. Only stdlib, `os`, `logging`,
`lancedb`, `pyarrow`, and `ftlangdetect` are permitted. This matches the
no-Dagster guarantee in `chunker.py`, `extractor.py`, and `quality_tagger.py`, which
enables fast pytest unit tests without a Dagster runtime.

### D4 — Label parsing: strip `__label__` prefix, take first result

fasttext `detect(text, low_memory=True)` returns a dict `{"lang": "en", "score": 0.9999}`
in ftlangdetect>=1.1.0 (the `__label__` prefix is stripped internally). Our
`.replace("__label__", "")` is a safe no-op and a regression guard for any future
version change. Always take the first (highest-probability) prediction. No fallback
lookup or mapping table required for MVP; the 176 languages in `lid.176.ftz` already
use ISO 639-1 codes.

```python
result = detect(text, low_memory=True)  # {"lang": "en", "score": 0.9999}
code = result["lang"].replace("__label__", "")  # "en" (no-op in 1.1.1)
conf = float(result["score"])  # 0.9999
conf = max(0.0, min(1.0, conf))  # clamp (scores are already in [0,1] but be safe)
```

### D5 — Empty / whitespace-only text handling + exception guard (sentinel behavior)

fasttext will raise or return garbage on empty strings. `detect_language()` must guard:

```python
def detect_language(text: str) -> tuple[str, float]:
    if not text or not text.strip():
        return ("und", 0.0)   # ISO 639-2 "undetermined" code, zero confidence
    try:
        result = detect(text, low_memory=True)
        code = result["lang"].replace("__label__", "")
        conf = max(0.0, min(1.0, float(result["score"])))
        return (code, conf)
    except (ValueError, RuntimeError, Exception) as exc:
        logger.warning("fasttext detect() failed for text (len=%d): %s", len(text), exc)
        return ("und", 0.0)   # sentinel — never abort the entire batch
```

**Behavior commitment**: When `detect()` raises ANY exception, `detect_language()`
returns the sentinel `("und", 0.0)` and logs a warning. It does NOT re-raise. This
ensures a single problematic chunk does not abort the entire batch for a source_id.

### D6 — Column-mode update: per-row `table.update(where=..., values=...)`

Exactly as in `quality_tagger.py` (amendment to agreed.md D6 / F-028
`review-final.md H1`): use `table.update(where=f"chunk_id = '{cid}'", values={...})`
once per row. Do NOT use `merge_insert` — lancedb 0.30.2's
`when_matched_update_all()` without `updates=` kwarg replaces the entire row,
destroying lineage fields (`augmented_from`, `augmenter_id`, `augmenter_config_hash`,
`producer_asset`, `producer_version`). The per-row `table.update()` call touches
only `attr_lang_code` and `attr_lang_confidence`. No other columns are modified.

### D7 — WHERE clause and row selection (identical pattern to `quality_tagger.py`)

Filter: `source_id = {source_id} AND producer_asset = 'chunks'`
Columns read from Lance: `["chunk_id", "text"]`
Search call: `table.search().where(where_clause).select(["chunk_id", "text"]).to_list()`
If zero rows: log `INFO` and return 0 immediately (no crash, no error).

### D8 — No schema change needed

The Lance `CHUNKS_SCHEMA` in `apps/api/dataplat_api/storage/lance.py` already
contains both target columns at the correct types:

```python
("attr_lang_code",       pa.string()),
("attr_lang_confidence", pa.float32()),
```

No migration, no `CHUNKS_SCHEMA` edit, no new Alembic migration. CLAUDE.md invariant
#3 (schema frozen post-publish) is not triggered.

### D9 — RunCreate Literal: add `"attr_lang"` as an accepted value

`apps/api/dataplat_api/schemas/runs.py`:

```python
# before
asset: Literal["extract_mineru", "chunks", "attr_quality"]

# after
asset: Literal["extract_mineru", "chunks", "attr_quality", "attr_lang"]
```

Update the docstring field description to add `"attr_lang" (F-029): run lang_fasttext
tagger.`

### D10 — Router dispatch: add `elif body.asset == "attr_lang"` branch

In `apps/api/dataplat_api/routers/runs.py`, insert a new `elif` branch before the
final `else` (defensive unreachable), following the exact same pattern as the
`attr_quality` branch:

```python
elif body.asset == "attr_lang":
    try:
        backfill_id = await gateway.launch_attr_lang_backfill(partition_keys)
    except DagsterGatewayError as exc:
        return JSONResponse(
            status_code=503,
            content={"detail": str(exc)},
        )
    kind = "attr_lang"
    asset_keys = ["attr_lang"]
```

### D11 — DagsterGateway: new `launch_attr_lang_backfill()` method and mutation constant

In `apps/api/dataplat_api/dagster/gateway.py`:

1. Add module-level constant (after `_LAUNCH_ATTR_QUALITY_BACKFILL_MUTATION`):

```python
_LAUNCH_ATTR_LANG_BACKFILL_MUTATION = """
mutation LaunchAttrLangBackfill($backfillParams: LaunchBackfillParams!) {
  launchPartitionBackfill(backfillParams: $backfillParams) {
    __typename
    ... on LaunchBackfillSuccess { backfillId }
    ... on PartitionSetNotFoundError { message }
    ... on PartitionKeysNotFoundError { message }
    ... on PythonError { message }
    ... on UnauthorizedError { message }
    ... on InvalidSubsetError { message }
    ... on RunConflict { message }
  }
}
"""
```

2. Add method `async def launch_attr_lang_backfill(self, partition_keys: list[str]) -> str:`
   — structurally identical to `launch_attr_quality_backfill()`, with:
   - `"query": _LAUNCH_ATTR_LANG_BACKFILL_MUTATION`
   - `"assetSelection": [{"path": ["attr_lang"]}]`
   - `"title": "F-029 attr_lang"`

3. Add `launch_attr_lang_backfill(partition_keys) -> str  # F-029` to the module
   docstring `Methods:` list.

### D12 — Dagster asset `attr_lang` in `definitions.py`

Add `attr_lang` asset following the `attr_quality` pattern exactly:

```python
from dagster_platform.lang_tagger import (
    update_lang_in_lance,
)

@asset(
    partitions_def=sources_partitions,
    description=(
        "Lang tagger (F-029): updates attr_lang_code and attr_lang_confidence "
        "columns on existing producer_asset='chunks' rows in Lance using fasttext "
        "lid.176.ftz. Zero new rows created."
    ),
)
def attr_lang(context: AssetExecutionContext) -> MaterializeResult:
    partition_key = context.partition_key
    source_id = int(partition_key.removeprefix("src_"))
    context.log.info(
        "attr_lang: starting for partition_key=%s source_id=%d",
        partition_key, source_id,
    )
    row_count = update_lang_in_lance(source_id)
    context.log.info(
        "attr_lang: updated %d row(s) for source_id=%d", row_count, source_id
    )
    if row_count == 0:
        context.log.warning(
            "attr_lang: zero rows updated for source_id=%d — "
            "chunks may not yet exist", source_id,
        )
    return MaterializeResult(
        metadata={
            "source_id": MetadataValue.int(source_id),
            "rows_updated": MetadataValue.int(row_count),
        }
    )
```

Register in `Definitions`: add `attr_lang` to the `assets=[...]` list after
`attr_quality`.

### D13 — `make codegen` after `schemas/runs.py` change (CLAUDE.md invariant #6)

The `RunCreate.asset` Literal change in `schemas/runs.py` modifies the OpenAPI schema.
After implementation, run `make codegen` and commit the updated
`packages/api-types/openapi.json` (and any generated TS types) in the same commit.

### D14 — Dockerfile: add `fasttext-langdetect==1.1.1` install + model bake

Add `fasttext-langdetect==1.1.1` to the `RUN pip install --no-cache-dir` block in
`docker/dagster/Dockerfile`. Set `FTLANG_CACHE` env var to `/app/fasttext-models`
so the model persists in the image layer. Immediately after the tiktoken bake step,
add:

```dockerfile
# F-029: bake fasttext lid.176.ftz model into the image (avoids runtime download).
# Uses low_memory=True to select the compressed lid.176.ftz model (~900KB).
# FTLANG_CACHE set to /app/fasttext-models so the cached model persists in the
# image layer and is available at runtime without a network call.
ENV FTLANG_CACHE=/app/fasttext-models
RUN python -c "from ftlangdetect import detect; detect('hello world', low_memory=True)"
```

**Note:** fasttext-predict==0.9.2.4 (C extension dep of ftlangdetect 1.1.1) ships
a binary wheel for cp312-manylinux2014_x86_64 — no gcc/g++ needed.

---

## Files changed

| File | Change type | Description |
|---|---|---|
| `dagster/dagster_platform/lang_tagger.py` | **new** | Pure helper module: `detect_language(text) -> tuple[str, float]` (with sentinel on exception), `update_lang_in_lance(source_id) -> int`. No Dagster imports. |
| `dagster/dagster_platform/definitions.py` | modify | Add `from dagster_platform.lang_tagger import (update_lang_in_lance,)`. Add `attr_lang` asset function. Register `attr_lang` in `Definitions(assets=[...])`. |
| `dagster/tests/test_lang_tagger.py` | **new** | 10 unit tests (see table below). Mock `ftlangdetect.detect` and `lancedb.connect`. |
| `apps/api/dataplat_api/schemas/runs.py` | modify | Widen `RunCreate.asset` Literal to add `"attr_lang"`. Update docstring. |
| `apps/api/dataplat_api/routers/runs.py` | modify | Add `elif body.asset == "attr_lang":` dispatch branch (before final `else`). |
| `apps/api/dataplat_api/dagster/gateway.py` | modify | Add `_LAUNCH_ATTR_LANG_BACKFILL_MUTATION` constant. Add `launch_attr_lang_backfill()` async method. |
| `docker/dagster/Dockerfile` | modify | Add `fasttext-langdetect==1.0.6` to pip install block. Add model bake RUN step. |
| `packages/api-types/openapi.json` | auto-generated | Updated by `make codegen` — committed in the same commit (invariant #6). |
| `verify/checks.sh` | modify | Add `attr_lang)` layer and add `bash "$0" attr_lang` to `all)` case (after `attr_quality`). |

---

## Verification plan

### Setup (prerequisites — run inside checks.sh `attr_lang)` layer)

```bash
attr_lang)
echo "=== attr_lang layer (F-029) ==="

# --- Unit tests (run in dagster-webserver container) ---
echo "--- attr_lang: unit tests ---"
docker compose -f "$COMPOSE" exec -T dagster-webserver \
  python -m pytest /app/dagster/tests/test_lang_tagger.py -q \
  || { echo "FAIL: unit tests"; exit 1; }
echo "  unit tests OK"

# --- Integration: create collection + upload + extract + chunk ---
echo "--- attr_lang: setup (collection + upload + extract + chunk) ---"
AL_TOKEN=$(... mint token same as attr_quality setup ...)
AL_COLL_ID=$(curl -s -X POST ... /api/collections ...)
AL_SRC_ID=$(curl -s -X POST ... /api/sources ... | jq -r '.id')

# Trigger extract_mineru and poll to COMPLETED_SUCCESS
AL_EXTRACT_BF=$(curl -s -X POST "$API/api/runs" \
  -H "Authorization: Bearer $AL_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"asset\": \"extract_mineru\", \"source_ids\": [$AL_SRC_ID]}" \
  | jq -r '.dagster_run_id')
# ... poll until COMPLETED_SUCCESS (same pattern as attr_quality layer) ...

# Trigger chunks and poll to COMPLETED_SUCCESS
AL_CHUNKS_BF=$(curl -s -X POST "$API/api/runs" \
  -H "Authorization: Bearer $AL_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"asset\": \"chunks\", \"source_ids\": [$AL_SRC_ID]}" \
  | jq -r '.dagster_run_id')
# ... poll until COMPLETED_SUCCESS ...
```

### V1 — Non-null ISO 639-1 codes for all rows (fastapi container)

```bash
echo "--- attr_lang: V1 — non-null ISO 639-1 codes ---"

# Trigger attr_lang
AL_LANG_BF=$(curl -s -X POST "$API/api/runs" \
  -H "Authorization: Bearer $AL_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"asset\": \"attr_lang\", \"source_ids\": [$AL_SRC_ID]}" \
  | jq -r '.dagster_run_id')
test -n "$AL_LANG_BF" || { echo "FAIL V1: no backfill ID"; exit 1; }

# Poll until COMPLETED_SUCCESS (same loop as other layers)
# ...

docker compose -f "$COMPOSE" exec -T \
  -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
  -e SRC_ID="${AL_SRC_ID}" \
  fastapi python -c "
import lancedb, os
db = lancedb.connect('s3://lance/chunks', storage_options={
    'aws_access_key_id': os.environ['S3_USER'],
    'aws_secret_access_key': os.environ['S3_PASS'],
    'endpoint': 'http://minio:9000', 'aws_region': 'us-east-1', 'allow_http': 'true'})
t = db.open_table('chunks')
src_id = int(os.environ['SRC_ID'])
rows = t.search().where(
    f\"source_id = {src_id} AND producer_asset = 'chunks'\").select(
    ['attr_lang_code']).to_list()
assert len(rows) > 0, 'No chunk rows found'
for r in rows:
    code = r['attr_lang_code']
    assert code is not None and 2 <= len(code) <= 3, f'Invalid lang code: {code!r}'
print(f'V1 PASS: {len(rows)} rows, all have non-null ISO 639-1 codes')
" || { echo "FAIL V1"; exit 1; }
echo "  V1 OK"
```

### V2 — Confidence floats in [0.0, 1.0] (fastapi container)

```bash
echo "--- attr_lang: V2 — confidence in [0,1] ---"
docker compose -f "$COMPOSE" exec -T \
  -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
  -e SRC_ID="${AL_SRC_ID}" \
  fastapi python -c "
import lancedb, os
db = lancedb.connect('s3://lance/chunks', storage_options={
    'aws_access_key_id': os.environ['S3_USER'],
    'aws_secret_access_key': os.environ['S3_PASS'],
    'endpoint': 'http://minio:9000', 'aws_region': 'us-east-1', 'allow_http': 'true'})
t = db.open_table('chunks')
src_id = int(os.environ['SRC_ID'])
rows = t.search().where(
    f\"source_id = {src_id} AND producer_asset = 'chunks'\").select(
    ['attr_lang_confidence']).to_list()
assert len(rows) > 0, 'No chunk rows'
for r in rows:
    conf = r['attr_lang_confidence']
    assert conf is not None, 'attr_lang_confidence is None'
    assert isinstance(conf, float), f'not float: {type(conf)}'
    assert 0.0 <= conf <= 1.0, f'out of range: {conf}'
print(f'V2 PASS: {len(rows)} rows, all confidence in [0.0, 1.0]')
" || { echo "FAIL V2"; exit 1; }
echo "  V2 OK"
```

### V3 — No new rows inserted (column-mode only) (fastapi container)

```bash
echo "--- attr_lang: V3 — no new rows: count before == count after ---"

# Record count BEFORE attr_lang (after chunks completed)
AL_RC_BEFORE=$(docker compose -f "$COMPOSE" exec -T \
  -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
  -e SRC_ID="${AL_SRC_ID}" \
  fastapi python -c "
import lancedb, os
db = lancedb.connect('s3://lance/chunks', storage_options={
    'aws_access_key_id': os.environ['S3_USER'],
    'aws_secret_access_key': os.environ['S3_PASS'],
    'endpoint': 'http://minio:9000', 'aws_region': 'us-east-1', 'allow_http': 'true'})
t = db.open_table('chunks')
src_id = int(os.environ['SRC_ID'])
print(t.count_rows(f\"source_id = {src_id} AND producer_asset = 'chunks'\"))
" | tr -d '[:space:]')

# Re-trigger attr_lang backfill and poll to COMPLETED_SUCCESS
AL_LANG_BF2=$(curl -s -X POST "$API/api/runs" \
  -H "Authorization: Bearer $AL_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"asset\": \"attr_lang\", \"source_ids\": [$AL_SRC_ID]}" \
  | jq -r '.dagster_run_id')
# ... poll until COMPLETED_SUCCESS ...

# Record count AFTER second run
AL_RC_AFTER=$(docker compose -f "$COMPOSE" exec -T \
  -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
  -e SRC_ID="${AL_SRC_ID}" \
  fastapi python -c "
import lancedb, os
db = lancedb.connect('s3://lance/chunks', storage_options={
    'aws_access_key_id': os.environ['S3_USER'],
    'aws_secret_access_key': os.environ['S3_PASS'],
    'endpoint': 'http://minio:9000', 'aws_region': 'us-east-1', 'allow_http': 'true'})
t = db.open_table('chunks')
src_id = int(os.environ['SRC_ID'])
print(t.count_rows(f\"source_id = {src_id} AND producer_asset = 'chunks'\"))
" | tr -d '[:space:]')

test "$AL_RC_BEFORE" = "$AL_RC_AFTER" \
  || { echo "FAIL V3: row count changed $AL_RC_BEFORE → $AL_RC_AFTER"; exit 1; }
echo "  V3 OK: row count unchanged at $AL_RC_AFTER"
```

### Unit tests (`dagster/tests/test_lang_tagger.py`)

Run inside the `dagster-webserver` container:

```bash
python -m pytest /app/dagster/tests/test_lang_tagger.py -q
```

Required test cases (10 tests):

| Test | What it verifies |
|---|---|
| `test_detect_language_happy_path` | `detect` returns `{"lang": "__label__en", "score": 0.9999}` → `("en", 0.9999)` |
| `test_detect_language_label_prefix_stripped` | `__label__` prefix is removed from all codes (zh, fr, de) |
| `test_detect_language_confidence_clamped_above_1` | score `1.5` → clamped to `1.0` |
| `test_detect_language_confidence_clamped_below_0` | score `-0.1` → clamped to `0.0` |
| `test_detect_language_empty_text` | `text=""` → `("und", 0.0)` without calling `detect` |
| `test_detect_language_whitespace_only` | `text="   "` → `("und", 0.0)` without calling `detect` |
| `test_detect_language_detect_raises` | When `detect()` raises `ValueError`, returns `("und", 0.0)` sentinel (no re-raise) |
| `test_lang_update_calls_table_update` | `update_lang_in_lance` calls `table.update(where=..., values=...)` once per row, `merge_insert` not called |
| `test_lang_update_updates_correct_columns` | Each `table.update` call sets only `attr_lang_code` and `attr_lang_confidence` |
| `test_lang_update_no_rows` | Zero rows from `to_list()` → returns 0 immediately, `table.update` not called |

### `checks.sh` layer name

`bash verify/checks.sh attr_lang`

Add `bash "$0" attr_lang` to the `all)` case in `checks.sh` after `bash "$0" attr_quality`.

---

## Risks / open questions

### R1 — fasttext C extension wheel resolved

**Resolved at implementation time.** `fasttext-langdetect==1.0.6` was unavailable;
`1.1.1` was used. `fasttext-predict==0.9.2.4` (C extension dependency of
ftlangdetect 1.1.1) ships a pre-compiled binary wheel for cp312-manylinux2014_x86_64.
No `gcc`, `g++`, or `libstdc++` required in the Docker image.

### R2 — Model file not bundled in `fasttext-langdetect` 1.1.1

Unlike 1.0.6 spec, the model is NOT bundled in the wheel. The Dockerfile bake step
downloads and caches it at `FTLANG_CACHE=/app/fasttext-models` (non-temp path
persists in image layer). `detect('hello world', low_memory=True)` in the `RUN`
step triggers the download and caches `lid.176.ftz` (~900 KB). At runtime the
container reads from this cached path without any network call.

### R3 — Single-character or very short text chunks

fasttext can produce unreliable predictions on very short strings (1-3 characters).
For MVP, accept this — no minimum-length guard beyond the empty-string check in D5.

### R4 — ISO 639-1 vs ISO 639-2/3 codes in `lid.176.ftz`

The `lid.176.ftz` model uses ISO 639-1 two-letter codes for most languages but may
use ISO 639-2/3 three-letter codes for languages without a 639-1 code (e.g., `"war"`,
`"bpy"`). The V1 assertion allows `len(code) >= 2 AND len(code) <= 3` to accommodate
this. The column is typed `pa.string()` with no length constraint, so no schema issue.

### R5 — `make codegen` must be run before committing

If `make codegen` is not run after the `RunCreate.asset` Literal change, CI will
reject the PR with an OpenAPI mismatch. Implementer must run `make codegen` and
commit `packages/api-types/openapi.json` in the same commit as `schemas/runs.py`.

---

## Reviewer feedback addressed

| Finding | Resolution |
|---|---|
| BLOCKER 1: V1/V2 wrong container | Fixed: all Lance assertions use `fastapi` container with `-e S3_USER/S3_PASS/SRC_ID` injection |
| BLOCKER 2: V3 pseudocode | Fixed: full runnable bash provided following attr_quality V4 pattern |
| HIGH 1: version pin ambiguous | Fixed: exact pin `==1.0.6`, no parenthetical escape |
| HIGH 2: SOURCE_ID injection missing | Fixed: all snippets show `os.environ['SRC_ID']` pattern |
| MEDIUM 1: R1 unresolvable at review time | Fixed: converted to implementer gate with `pip download` command |
| MEDIUM 2: missing exception test | Fixed: added `test_detect_language_detect_raises` + committed to sentinel behavior in D5 |
