# S029-F-029 — lang_fasttext tagger: proposed.md

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

### D1 — fasttext package: `fasttext-langdetect`

Use `fasttext-langdetect` (PyPI) rather than `fasttext-wheel` or bare `fasttext`.
`fasttext-langdetect` is a pure-Python wrapper that bundles `fasttext` via
`fasttext-wheel` (C extension, pre-compiled wheels for CPython 3.12 / linux/amd64)
and exposes `from fasttext_langdetect import detect`. No additional model download
step is needed at runtime — the package ships `lid.176.ftz` inside its wheel.

**Build-time model warm-up**: add one `RUN python -c` bake step to the Dockerfile
(same pattern as tiktoken cl100k_base in F-025) so the model is pre-loaded into the
image and cannot fail at runtime due to network absence:

```dockerfile
RUN python -c "from fasttext_langdetect import detect; detect('hello')"
```

This mirrors the F-025 tiktoken bake step exactly and mitigates R1 (air-gapped
environments).

### D2 — fasttext is NOT an LLM SDK (CLAUDE.md invariant #4 does not apply)

fasttext is a classical ML classifier. CLAUDE.md invariant #4 ("LLM calls go through
the gateway") applies only to generative LLM SDKs (Anthropic, OpenAI, etc.).
`lang_tagger.py` may import and call `fasttext_langdetect.detect()` directly — no
HTTP gateway call required, no `requests` usage. This keeps the implementation
simple, fast, and self-contained.

### D3 — No Dagster imports in `lang_tagger.py` (same as `quality_tagger.py`)

`dagster/dagster_platform/lang_tagger.py` must have zero Dagster imports, zero
`requests` imports, and zero LLM SDK imports. Only stdlib, `os`, `logging`,
`lancedb`, `pyarrow`, and `fasttext_langdetect` are permitted. This matches the
no-Dagster guarantee in `chunker.py`, `extractor.py`, and `quality_tagger.py`, which
enables fast pytest unit tests without a Dagster runtime.

### D4 — Label parsing: strip `__label__` prefix, take first result

fasttext `detect(text)` returns a dict `{"lang": "__label__en", "score": 0.9999}`.
Strip the `"__label__"` prefix to get the ISO 639-1 code. Always take the first
(highest-probability) prediction. No fallback lookup or mapping table required for
MVP; the 176 languages in `lid.176.ftz` already use ISO 639-1 codes.

```python
result = detect(text)          # {"lang": "__label__en", "score": 0.9999}
code = result["lang"].replace("__label__", "")  # "en"
conf = float(result["score"])  # 0.9999
conf = max(0.0, min(1.0, conf))  # clamp (scores are already in [0,1] but be safe)
```

### D5 — Empty / whitespace-only text handling

fasttext will raise or return garbage on empty strings. `lang_tagger.py` must guard:

```python
if not text or not text.strip():
    return ("und", 0.0)   # ISO 639-2 "undetermined" code, zero confidence
```

This prevents crashes and gives downstream consumers a predictable sentinel value.

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

### D14 — Dockerfile: add `fasttext-langdetect` install + model bake

Add `fasttext-langdetect==1.0.6` (or latest stable at implementation time) to the
`RUN pip install --no-cache-dir` block in `docker/dagster/Dockerfile`. Immediately
after the tiktoken bake step, add:

```dockerfile
# F-029: bake fasttext lid.176.ftz model into the image (avoids runtime download).
RUN python -c "from fasttext_langdetect import detect; detect('hello world')"
```

---

## Files changed

| File | Change type | Description |
|---|---|---|
| `dagster/dagster_platform/lang_tagger.py` | **new** | Pure helper module: `_build_lance_storage_options()`, `detect_language(text) -> tuple[str, float]`, `_lang_update(table, source_id, where_clause) -> None`, `update_lang_in_lance(source_id) -> int`. No Dagster imports. |
| `dagster/dagster_platform/definitions.py` | modify | Add `from dagster_platform.lang_tagger import (update_lang_in_lance,)`. Add `attr_lang` asset function. Register `attr_lang` in `Definitions(assets=[...])`. |
| `dagster/tests/test_lang_tagger.py` | **new** | Unit tests for `lang_tagger.py`: happy path, empty text sentinel, confidence clamping, error handling (detect raises), no-new-rows assertion, `update_lang_in_lance` full path. Mock `fasttext_langdetect.detect` and `lancedb.connect`. |
| `apps/api/dataplat_api/schemas/runs.py` | modify | Widen `RunCreate.asset` Literal to add `"attr_lang"`. Update docstring. |
| `apps/api/dataplat_api/routers/runs.py` | modify | Add `elif body.asset == "attr_lang":` dispatch branch (before final `else`). Update route summary/description docstrings to mention `attr_lang`. |
| `apps/api/dataplat_api/dagster/gateway.py` | modify | Add `_LAUNCH_ATTR_LANG_BACKFILL_MUTATION` constant. Add `launch_attr_lang_backfill()` async method. Update module docstring `Methods:` list. |
| `docker/dagster/Dockerfile` | modify | Add `fasttext-langdetect==<version>` to pip install block. Add model bake `RUN python -c "from fasttext_langdetect import detect; detect('hello world')"`. |
| `packages/api-types/openapi.json` | auto-generated | Updated by `make codegen` — must be committed in the same commit as `schemas/runs.py` change (CLAUDE.md invariant #6). |
| `verify/checks.sh` | modify | Add `attr_lang)` layer (mirroring `attr_quality)` layer) and add `bash "$0" attr_lang` to the `all)` case. |

---

## Verification plan

### Setup (same as `attr_quality` layer — sequential prerequisites)

1. Mint a bearer token (`smoke` layer or `auth` layer must pass).
2. Create a collection via `POST /api/collections`.
3. Upload a PDF source via `POST /api/sources`.
4. Trigger and poll `extract_mineru` backfill to `COMPLETED_SUCCESS`.
5. Trigger and poll `chunks` backfill to `COMPLETED_SUCCESS`.
6. Trigger `attr_lang` via `POST /api/runs` with `{"asset": "attr_lang", "source_ids": [<id>]}` — expect HTTP 202 with a `dagster_run_id` backfill ID.
7. Poll Dagster run status via `GET /api/runs/<dagster_run_id>` until `"success"` or timeout.

### V1 — Non-null ISO 639-1 codes for all rows

Inside the `dagster-webserver` container, run a Python snippet (embedded in
`checks.sh`) that opens the Lance table and queries all rows for the processed
`source_id`:

```python
import lancedb, os
db = lancedb.connect(
    f"s3://{os.environ.get('MINIO_LANCE_BUCKET','lance')}/chunks",
    storage_options={
        "aws_access_key_id": os.environ["MINIO_ROOT_USER"],
        "aws_secret_access_key": os.environ["MINIO_ROOT_PASSWORD"],
        "endpoint": f"http://{os.environ['MINIO_ENDPOINT']}",
        "aws_region": "us-east-1",
        "allow_http": "true",
    },
)
table = db.open_table("chunks")
rows = (
    table.search()
    .where(f"source_id = {SOURCE_ID} AND producer_asset = 'chunks'")
    .select(["attr_lang_code", "attr_lang_confidence"])
    .to_list()
)
assert len(rows) > 0, "No chunk rows found — chunks asset must run first"
for r in rows:
    code = r["attr_lang_code"]
    assert code is not None and len(code) >= 2 and len(code) <= 3, \
        f"Invalid lang code: {code!r}"
print(f"V1 PASS: {len(rows)} rows, all have non-null ISO 639-1 codes")
```

Exit code 0 = pass.

### V2 — Confidence floats in [0.0, 1.0]

Extend the V1 snippet (same Lance query, same `rows`):

```python
for r in rows:
    conf = r["attr_lang_confidence"]
    assert conf is not None, f"attr_lang_confidence is None"
    assert isinstance(conf, float), f"attr_lang_confidence not float: {type(conf)}"
    assert 0.0 <= conf <= 1.0, f"attr_lang_confidence out of range: {conf}"
print(f"V2 PASS: all confidence values in [0.0, 1.0]")
```

Exit code 0 = pass.

### V3 — No new rows inserted (column-mode only)

Record `count_before` via `table.count_rows(where_clause)` immediately after the
`chunks` backfill completes and before triggering `attr_lang`. Record `count_after`
after `attr_lang` succeeds. Assert `count_before == count_after`.

```bash
# In checks.sh (pseudocode — actual implementation uses python -c inline):
COUNT_BEFORE=$(python3 -c "...table.count_rows(where_clause)...")
# trigger attr_lang and poll ...
COUNT_AFTER=$(python3 -c "...table.count_rows(where_clause)...")
[ "$COUNT_BEFORE" -eq "$COUNT_AFTER" ] || { echo "V3 FAIL: row count changed"; exit 1; }
echo "V3 PASS: row count unchanged ($COUNT_BEFORE)"
```

### Unit tests (`dagster/tests/test_lang_tagger.py`)

Run inside the `dagster-webserver` container:

```bash
python -m pytest /app/dagster/tests/test_lang_tagger.py -q
```

Required test cases (following `test_quality_tagger_llm.py` structure):

| Test | What it verifies |
|---|---|
| `test_detect_language_happy_path` | `detect` returns `{"lang": "__label__en", "score": 0.9999}` → `("en", 0.9999)` |
| `test_detect_language_label_prefix_stripped` | `__label__` prefix is removed from all codes |
| `test_detect_language_confidence_clamped_above_1` | score `1.5` → clamped to `1.0` |
| `test_detect_language_empty_text` | `text=""` → `("und", 0.0)` without calling `detect` |
| `test_detect_language_whitespace_only` | `text="   "` → `("und", 0.0)` without calling `detect` |
| `test_lang_update_calls_table_update` | `_lang_update` calls `table.update(where=..., values=...)` once per row, `merge_insert` not called |
| `test_lang_update_updates_correct_columns` | Each `table.update` call sets only `attr_lang_code` and `attr_lang_confidence` |
| `test_lang_update_no_rows` | Zero rows from `to_list()` → returns immediately, `table.update` not called |
| `test_update_lang_in_lance_full_path` | Full path: `lancedb.connect` mocked, 2 rows, `count_rows` returns 2, `table.update` called twice, `merge_insert`/`insert`/`add` not called |

### `checks.sh` layer name

`bash verify/checks.sh attr_lang`

Add `bash "$0" attr_lang` to the `all)` case in `checks.sh` so it runs as part of
the full suite.

---

## Risks / open questions

### R1 — fasttext C extension build failure in the Dagster image

`fasttext-langdetect` depends on `fasttext-wheel`, which ships a pre-compiled C
extension. If the wheel is not available for the exact Python/platform combination
in the Dagster Docker image (`python:3.12-slim`, linux/amd64), pip will attempt to
compile from source, which requires `gcc`, `g++`, and `libstdc++`. The
`python:3.12-slim` base image does not include a C compiler.

**Mitigation**: verify at contract review time that a `fasttext-wheel` manylinux wheel
exists for `python3.12` / `linux_x86_64` on PyPI. If no pre-built wheel is available,
add `RUN apt-get install -y gcc g++ libstdc++-dev` to the Dockerfile before the pip
install step. Pin the version explicitly to avoid unintended updates.

### R2 — Model file not bundled in `fasttext-langdetect`

Some versions of `fasttext-langdetect` auto-download `lid.176.ftz` from the internet
on first `detect()` call rather than bundling it in the wheel. In air-gapped or
slow-network environments this would fail silently or time out.

**Mitigation**: the Dockerfile bake step `RUN python -c "from fasttext_langdetect
import detect; detect('hello world')"` runs at image build time, which downloads and
caches the model inside the image. If the model is downloaded to a user cache path
(e.g. `~/.cache/fasttext-langdetect/`), verify the cache directory is preserved in
the built image layer (it will be, since Docker layers are committed after each RUN).
Implementer must confirm model path after `docker build`.

### R3 — Single-character or very short text chunks

fasttext can produce unreliable predictions on very short strings (1–3 characters).
For MVP, accept this — no minimum-length guard beyond the empty-string check in D5.
Quality tiers (confidence threshold filtering) are deferred to a future feature.

### R4 — ISO 639-1 vs ISO 639-2/3 codes in `lid.176.ftz`

The `lid.176.ftz` model uses ISO 639-1 two-letter codes for most languages but may
use ISO 639-2/3 three-letter codes for languages without a 639-1 code (e.g., `"war"`,
`"bpy"`). The V1 assertion allows `len(code) >= 2 AND len(code) <= 3` to accommodate
this. The column is typed `pa.string()` with no length constraint, so no schema issue
arises.

### R5 — `make codegen` must be run before committing

If `make codegen` is not run after the `RunCreate.asset` Literal change, CI will
reject the PR with an OpenAPI mismatch. Implementer must run `make codegen` and
commit `packages/api-types/openapi.json` in the same commit as `schemas/runs.py`.
