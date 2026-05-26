# Contract: S024-F-024 — Trigger chunking via POST /api/runs

**Status:** PROPOSED  
**Sprint:** S024-F-024  
**Feature:** F-024 — `POST /api/runs` with `{asset: "chunks", source_ids: [...]}` launches a
Dagster backfill for the `chunks` asset on the specified partitions  
**Author:** leader (inline)  
**Date:** 2026-05-26  
**Depends on:** F-018 (`passes: true`)

---

## Summary

F-024 is a **minimal extension of F-018**. The existing `POST /api/runs` endpoint currently accepts
only `asset: "extract_mineru"`. This sprint extends it to also accept `asset: "chunks"` using an
identical flow: validate sources → register partitions → launch Dagster backfill → insert Run row →
return 202. No new endpoint, no new migration, no new Postgres column is introduced. The only
Postgres-visible difference is a new `kind="chunk"` value in the existing `run` table (already
supported by the `kind: str` column — no schema change needed).

Four code locations change, one verification layer is extended:

| File | Change |
|---|---|
| `apps/api/dataplat_api/schemas/runs.py` | Extend `RunCreate.asset` Literal to include `"chunks"` |
| `apps/api/dataplat_api/dagster/gateway.py` | Add `launch_chunks_backfill()` method + constant |
| `apps/api/dataplat_api/routers/runs.py` | Add conditional dispatch on `body.asset` |
| `dagster/dagster_platform/definitions.py` | Add stub `chunks` asset (placeholder body) |
| `verify/checks.sh` — `runs)` layer | Append F024-V1, F024-V2, F024-V3 checks |

OpenAPI codegen (`make codegen`) must run after the schema change; the updated
`packages/api-types/openapi.json` must be committed in **the same commit** as the code changes
(hard invariant #6).

---

## What will be built

### 1. Schema extension — `apps/api/dataplat_api/schemas/runs.py`

**Change:** `RunCreate.asset` Literal widens from one value to two.

```python
# BEFORE
asset: Literal["extract_mineru"]

# AFTER
asset: Literal["extract_mineru", "chunks"]
```

Update the `RunCreate` class docstring to reflect both supported assets. No changes to
`RunCreateResponse`, `RunStatusResponse`, or `LaunchHelloWorldResponse`.

Pydantic v2's Literal validation continues to reject any other value with a 422 from FastAPI.

---

### 2. New gateway method — `apps/api/dataplat_api/dagster/gateway.py`

**Change:** Add one new module-level GraphQL constant and one new method. The GraphQL mutation
body is structurally identical to `_LAUNCH_EXTRACT_BACKFILL_MUTATION` (same `launchPartitionBackfill`
mutation) — it is given a separate constant for clarity and future extensibility.

**New constant** (add after `_LAUNCH_EXTRACT_BACKFILL_MUTATION`):

```python
# F-024: Launch an asset backfill for chunks. Structurally identical to the
# extract backfill mutation; separated for self-documentation.
_LAUNCH_CHUNKS_BACKFILL_MUTATION = """
mutation LaunchChunksBackfill($backfillParams: LaunchBackfillParams!) {
  launchPartitionBackfill(backfillParams: $backfillParams) {
    __typename
    ... on LaunchBackfillSuccess {
      backfillId
    }
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

**New method** on `DagsterGateway` (add after `launch_extract_backfill`):

```python
async def launch_chunks_backfill(self, partition_keys: list[str]) -> str:
    """Launch an asset backfill for chunks over the given partition keys.

    Identical in structure to launch_extract_backfill; differs only in
    assetSelection (["chunks"]) and title string.

    Args:
        partition_keys: List of partition keys in "src_{id}" format.

    Returns:
        The backfillId (string) from LaunchBackfillSuccess.

    Raises DagsterGatewayError for all failure cases (same as
    launch_extract_backfill — see that method's docstring for the full list).
    """
    payload = {
        "query": _LAUNCH_CHUNKS_BACKFILL_MUTATION,
        "variables": {
            "backfillParams": {
                "assetSelection": [{"path": ["chunks"]}],
                "partitionNames": partition_keys,
                "title": "F-024 chunks",
            }
        },
    }
    # ... identical httpx call, error-handling, and backfillId extraction
    # as launch_extract_backfill — all DagsterGatewayError paths preserved.
```

The full implementation copies the error-handling structure of `launch_extract_backfill` exactly:
network errors → `DagsterGatewayError`, HTTP non-2xx → `DagsterGatewayError`, GraphQL `errors`
key → `DagsterGatewayError`, `__typename != "LaunchBackfillSuccess"` → `DagsterGatewayError`,
empty `backfillId` → `DagsterGatewayError`.

Update the module docstring at the top of `gateway.py` to list both `launch_extract_backfill`
(already implemented in F-018 but missing from the docstring) and `launch_chunks_backfill`.

---

### 3. Route handler update — `apps/api/dataplat_api/routers/runs.py`

**Change:** Add a conditional dispatch block inside the existing `trigger_extract_run` handler.
The function name, decorator, and steps 1–3 (source validation, partition derivation, partition
registration) are **unchanged**. Only step 4 (backfill launch) and step 5 (Run row insertion)
acquire asset-specific values.

Update route `summary` and `description` strings to reflect both supported assets. Also update
the module-level docstring (line 11 `POST "" — trigger a MinerU extraction backfill …`) to read:

```python
  POST ""            — trigger an asset backfill (extract_mineru or chunks, HTTP 202 Accepted, F-018/F-024)
```

```python
@runs_router.post(
    "",
    response_model=RunCreateResponse,
    status_code=202,
    summary="Trigger asset backfill (extract_mineru or chunks)",
    description=(
        "Launch a Dagster asset backfill for the given asset over the supplied source IDs. "
        "Supported assets: 'extract_mineru' (F-018), 'chunks' (F-024). "
        "Returns the Dagster backfillId and the Postgres run.id. "
        "Returns 404 if any source_id does not exist. "
        "Returns 503 if Dagster is unreachable or the backfill launch fails. "
        "Requires a valid Bearer JWT (F-008)."
    ),
)
```

Inside the handler body, replace the current hardcoded call to `gateway.launch_extract_backfill`
with an if/else dispatch:

```python
# Step 4: Dispatch backfill launch and metadata by asset.
if body.asset == "extract_mineru":
    try:
        backfill_id = await gateway.launch_extract_backfill(partition_keys)
    except DagsterGatewayError as exc:
        return JSONResponse(status_code=503, content={"detail": str(exc)})
    kind = "extract"
    asset_keys = ["extract_mineru"]
else:  # body.asset == "chunks" — guaranteed by RunCreate.asset Literal validation
    try:
        backfill_id = await gateway.launch_chunks_backfill(partition_keys)
    except DagsterGatewayError as exc:
        return JSONResponse(status_code=503, content={"detail": str(exc)})
    kind = "chunk"
    asset_keys = ["chunks"]

# Step 5: Insert Run row into Postgres (uses dispatched kind + asset_keys).
run = Run(
    dagster_run_id=backfill_id,
    kind=kind,
    asset_keys=asset_keys,
    ...  # remaining fields unchanged
)
```

Update the handler docstring: mention that step 4 now dispatches on `body.asset`, and that
`kind="extract"` / `kind="chunk"` maps to `asset="extract_mineru"` / `asset="chunks"` respectively.

The function name `trigger_extract_run` is **kept as-is** (cosmetic rename is out of scope for
this sprint; it would require updating test references with no functional benefit).

---

### 4. Dagster stub asset — `dagster/dagster_platform/definitions.py`

**Change:** Add a minimal `chunks` asset and register it in `Definitions`. The asset MUST exist
in the code location for `launchPartitionBackfill` to accept `assetSelection: [{"path": ["chunks"]}]`
without returning an `InvalidSubsetError`.

```python
@asset(
    partitions_def=sources_partitions,
    description=(
        "Chunking (F-024 stub): body raises NotImplementedError. "
        "F-025 will implement the real chunking logic (read DoclingDocument from MinIO, "
        "split into chunks, write to Lance table)."
    ),
)
def chunks(context: AssetExecutionContext) -> MaterializeResult:
    """Stub chunking asset (F-024).

    The backfill can be launched successfully against this asset, but any
    per-partition run will fail with NotImplementedError. F-025 will fill in
    the real body. The stub satisfies F-024's V3 criterion (Dagster shows a
    backfill for the 'chunks' asset launched) without requiring the backfill
    to complete successfully.
    """
    raise NotImplementedError(
        "chunks asset body not yet implemented — see F-025"
    )
```

**No `deps=`** on the stub. Adding `deps=[extract_mineru]` would create a lineage constraint
that could interfere with the standalone backfill launch. F-025 will add the proper upstream
dependency when implementing the real body.

Update `defs`:

```python
defs = Definitions(
    jobs=[hello_world_job],
    assets=[source_asset, extract_mineru, chunks],  # chunks added
)
```

**Important:** After adding `chunks` to `definitions.py`, dagster-webserver must be restarted to
pick up the new code location. In the `all)` verification chain, the `dagster)` layer's
`F012-prerestart` step handles this restart before `runs)` executes. If running `runs)` standalone,
the tester must restart dagster-webserver manually first:
`docker compose -f docker/docker-compose.dev.yml restart dagster-webserver`

---

### 5. Unit tests — `apps/api/tests/`

New tests should mirror the F-018 pattern. Exact placement follows the existing test file layout.

**Gateway tests** (add to existing gateway test file, or a new `test_launch_chunks_backfill.py`):

| Test | What it checks |
|---|---|
| `test_launch_chunks_backfill_success` | Happy path: mock returns `LaunchBackfillSuccess` with `backfillId="bf-123"` → returns `"bf-123"` |
| `test_launch_chunks_backfill_python_error` | `__typename == "PythonError"` → raises `DagsterGatewayError` |
| `test_launch_chunks_backfill_unauthorized` | `__typename == "UnauthorizedError"` → raises `DagsterGatewayError` |
| `test_launch_chunks_backfill_network_error` | `httpx.ConnectError` → raises `DagsterGatewayError` |
| `test_launch_chunks_backfill_invalid_subset` | `__typename == "InvalidSubsetError"` → raises `DagsterGatewayError` |

**Router tests** (add to existing runs router test file):

| Test | What it checks |
|---|---|
| `test_trigger_run_chunks_returns_202` | POST with `asset="chunks"` → 202, response has `dagster_run_id` (str) and `run_id` (int) |
| `test_trigger_run_chunks_creates_run_row_kind_chunk` | After POST with `asset="chunks"`, the inserted Run has `kind="chunk"` and `asset_keys=["chunks"]` |
| `test_trigger_run_chunks_calls_chunks_backfill_not_extract` | `gateway.launch_chunks_backfill` is called; `gateway.launch_extract_backfill` is NOT called |
| `test_trigger_run_extract_still_works` | Regression: `asset="extract_mineru"` still returns 202 and calls `launch_extract_backfill` |

**Schema tests** (add to existing schema test file, or inline with router tests):

| Test | What it checks |
|---|---|
| `test_run_create_accepts_chunks` | `RunCreate(asset="chunks", source_ids=[1])` validates without error |
| `test_run_create_rejects_unknown_asset` | `RunCreate(asset="tagger", source_ids=[1])` raises `ValidationError` |

---

### 6. Verification layer extension — `verify/checks.sh` `runs)` layer

Append a new block immediately after the existing `runs F018-V3` check (before the closing `;;`).

**F024 setup** — Upload a fresh minimal PDF and capture `F024_SRC_ID`. This keeps F024 isolated
from F018 — if F018 setup ever changes, F024 is not affected.

```bash
# F024 setup: upload a minimal PDF to get a fresh source ID.
# Uses the same token and host already in scope from the top of the runs) block.
echo "--- runs F024-setup: uploading fresh source for F024 checks ---"
F024_PDF=$(mktemp /tmp/f024-XXXXXX.pdf)
python3 -c "
# Minimal valid single-page PDF (same approach as F018 setup).
pdf = b'%PDF-1.4\n'
pdf += b'1 0 obj\n<</Type /Catalog /Pages 2 0 R>>\nendobj\n'
pdf += b'2 0 obj\n<</Type /Pages /Kids [3 0 R] /Count 1>>\nendobj\n'
pdf += b'3 0 obj\n<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]>>\nendobj\n'
xref_pos = len(pdf)
pdf += b'xref\n0 4\n0000000000 65535 f \n'
pdf += b'0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n'
pdf += b'trailer\n<</Size 4 /Root 1 0 R>>\n'
pdf += b'startxref\n' + str(xref_pos).encode() + b'\n%%EOF\n'
open('$F024_PDF', 'wb').write(pdf)
"
F024_UP_BODY=$(mktemp)
F024_UP_STATUS=$(curl -sS -X POST \
  "http://localhost:${FASTAPI_HOST_PORT}/api/sources/upload" \
  -H "Authorization: Bearer $RUNS_TOKEN" \
  -F "file=@${F024_PDF};type=application/pdf" \
  -w '%{http_code}' -o "$F024_UP_BODY")
rm -f "$F024_PDF"
test "$F024_UP_STATUS" = "201" \
  || { echo "FAIL: F024-setup upload returned $F024_UP_STATUS: $(cat "$F024_UP_BODY")"; rm -f "$F024_UP_BODY"; exit 1; }
F024_SRC_ID=$(python3 -c "import json; print(json.load(open('$F024_UP_BODY'))['id'])")
rm -f "$F024_UP_BODY"
echo "  F024-setup OK: F024_SRC_ID=${F024_SRC_ID}"
```

**F024-V1** — POST returns 202 with correct shape:

```bash
echo "--- runs F024-V1: POST /api/runs with asset=chunks returns 202 ---"
# NOTE: dagster-webserver must be running with definitions.py containing the chunks
# asset. In the all) chain, the dagster) layer's restart step guarantees this.
# For standalone runs) execution, run:
#   docker compose -f docker/docker-compose.dev.yml restart dagster-webserver
# and wait for it to become healthy before proceeding.
F024_V1_BODY=$(mktemp)
F024_V1_STATUS=$(curl -sS -X POST \
  "http://localhost:${FASTAPI_HOST_PORT}/api/runs" \
  -H "Authorization: Bearer $RUNS_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"asset\": \"chunks\", \"source_ids\": [${F024_SRC_ID}]}" \
  -w '%{http_code}' -o "$F024_V1_BODY")
test "$F024_V1_STATUS" = "202" \
  || { echo "FAIL: F024-V1 returned $F024_V1_STATUS: $(cat "$F024_V1_BODY")"; rm -f "$F024_V1_BODY"; exit 1; }
python3 -c "
import json, sys
body = json.load(open('$F024_V1_BODY'))
assert 'dagster_run_id' in body, f'missing dagster_run_id: {body}'
assert 'run_id' in body, f'missing run_id: {body}'
assert isinstance(body['dagster_run_id'], str) and body['dagster_run_id'], \
  f'dagster_run_id empty or not str: {body}'
assert isinstance(body['run_id'], int), f'run_id not int: {body}'
print('  F024-V1 OK: dagster_run_id=%s run_id=%d' % (body['dagster_run_id'], body['run_id']))
" || { echo "FAIL: F024-V1 response shape incorrect"; rm -f "$F024_V1_BODY"; exit 1; }
F024_BACKFILL_ID=$(python3 -c "import json; print(json.load(open('$F024_V1_BODY'))['dagster_run_id'])")
F024_RUN_ID=$(python3 -c "import json; print(json.load(open('$F024_V1_BODY'))['run_id'])")
rm -f "$F024_V1_BODY"
```

**F024-V2** — Postgres run row has `kind='chunk'`:

```bash
echo "--- runs F024-V2: run row exists with kind=chunk, status=pending ---"
docker compose -f "$COMPOSE_F018" exec -T postgres \
  psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
    "SELECT kind || '|' || status FROM run WHERE id=${F024_RUN_ID}" \
  | grep -q '^chunk|pending$' \
  || { echo "FAIL: F024-V2 — run row missing or wrong kind/status (expected chunk|pending)"; exit 1; }
echo "  F024-V2 OK: run row kind=chunk status=pending"
```

**F024-V3** — Dagster backfill `assetSelection` contains `"chunks"`:

```bash
echo "--- runs F024-V3: Dagster shows backfill for chunks asset ---"
F024_BACKFILL_CHECK=$(docker compose -f "$COMPOSE_F018" exec -T dagster-webserver \
  python3 -c "
import urllib.request, json, sys
url = 'http://localhost:3000/graphql'
query = json.dumps({
    'query': '''query GetBackfill(\$id: String!) {
        partitionBackfillOrError(backfillId: \$id) {
            __typename
            ... on PartitionBackfill {
                id
                isAssetBackfill
                status
                assetSelection { path }
            }
            ... on BackfillNotFoundError { message }
            ... on PythonError { message }
        }
    }''',
    'variables': {'id': '$F024_BACKFILL_ID'}
})
req = urllib.request.Request(url, data=query.encode(), headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req)
data = json.load(resp)
result = data['data']['partitionBackfillOrError']
typename = result.get('__typename')
if typename != 'PartitionBackfill':
    print(f'FAIL: partitionBackfillOrError returned {typename}: {result}', file=sys.stderr)
    sys.exit(1)
assert result.get('isAssetBackfill'), f'isAssetBackfill not true: {result}'
paths = [seg for sel in result.get('assetSelection', []) for seg in sel.get('path', [])]
assert 'chunks' in paths, f'chunks not in assetSelection paths: {paths}'
print(f'  F024-V3 OK: backfillId={result[\"id\"]} status={result[\"status\"]} assets={paths}')
" 2>&1)
echo "$F024_BACKFILL_CHECK" | grep -q "FAIL" \
  && { echo "$F024_BACKFILL_CHECK"; exit 1; } || echo "$F024_BACKFILL_CHECK"
```

The `all)` chain entry at the bottom of checks.sh does **not** need updating: `runs)` already
appears there and the new F024 checks are appended inside the existing `runs)` case block.

---

## Design decisions

### D1 — Reuse `_LAUNCH_EXTRACT_BACKFILL_MUTATION` body vs. new constant

**Decision:** Add a new `_LAUNCH_CHUNKS_BACKFILL_MUTATION` constant with identical GraphQL body
(different operation name `LaunchChunksBackfill`).

**Rationale:** The F-018 constant name `_LAUNCH_EXTRACT_BACKFILL_MUTATION` is semantically scoped
to extraction. Using it for a chunks backfill would be misleading. A separate constant mirrors
how gateway methods are already named (one method per asset type) and makes future asset additions
trivially clear. The duplication is 18 lines of GraphQL that is unlikely to diverge.

### D2 — Dispatch style: if/else vs. dispatch table

**Decision:** `if body.asset == "extract_mineru": ... else: ...` inline in the handler body.

**Rationale:** Only two assets are supported. A dispatch table
(`_HANDLERS = {"extract_mineru": ..., "chunks": ...}`) adds indirection without benefit at this
scale. The if/else maps directly to the `RunCreate.asset` Literal values, is easy to read, and
keeps the two execution paths visible side by side. A dispatch table is appropriate when N > 3;
at N = 2 it is over-engineering.

### D3 — No `deps=` on the `chunks` stub

**Decision:** The `chunks` stub asset carries no `deps` declaration.

**Rationale:** Adding `deps=[extract_mineru]` would encode a lineage constraint in Dagster.
While `chunks` *semantically* depends on `extract_mineru`, adding this before the real body
exists could confuse the asset graph and complicate independent backfill launches in testing.
F-025 will add the correct upstream dependency when implementing the real chunking logic.

### D4 — `trigger_extract_run` function name unchanged

**Decision:** Keep the Python function name `trigger_extract_run`; do not rename to `trigger_run`.

**Rationale:** The function name has no API surface impact (routing is determined by the
`@runs_router.post("")` decorator). Renaming would require updating test mocks/references for zero
functional gain. The docstring update is sufficient to make the handler's expanded role clear.
A rename can be done as part of a future cleanup sprint if desired.

### D5 — `runs)` layer does not restart dagster-webserver

**Decision:** No dagster-webserver restart step is added inside the `runs)` verification layer.

**Rationale:** In the `all)` chain, `dagster)` runs before `runs)` and its `F012-prerestart` step
already restarts dagster-webserver with the latest definitions.py (which will include `chunks`
after F-024 is implemented). Adding a second restart inside `runs)` would slow `all)` by ~30 s
with no benefit. For standalone `runs)` invocations, a clear comment in the F024 block instructs
the tester to restart dagster-webserver manually. This is consistent with how the `runs)` layer
already implicitly relies on the `dagster)` layer having been run at least once (F018-V3
similarly assumes dagster-webserver knows about `extract_mineru`).

### D6 — F024 setup uploads a fresh source rather than reusing `$F018_SRC_ID`

**Decision:** The F024 checks block uploads its own source (`F024_SRC_ID`) rather than reusing
the `F018_SRC_ID` variable set earlier in the same `runs)` layer.

**Rationale:** Reusing `F018_SRC_ID` would create an ordering dependency between the two blocks
and would mean the chunk backfill targets the same source as the extract backfill. A separate
source per feature check makes each block independently understandable and avoids state pollution
between F018 and F024 assertions.

---

## Verification plan

All verification runs against the live Docker stack (`docker/docker-compose.dev.yml`).

### Automated checks

| Check ID | Command | Pass criterion |
|---|---|---|
| F024-V1 | `bash verify/checks.sh runs` | POST `/api/runs` `{"asset":"chunks","source_ids":[N]}` → HTTP 202, body contains `dagster_run_id` (non-empty str) and `run_id` (int) |
| F024-V2 | `bash verify/checks.sh runs` | `SELECT kind\|\|'\|'\|\|status FROM run WHERE id=<run_id>` → `chunk\|pending` |
| F024-V3 | `bash verify/checks.sh runs` | `partitionBackfillOrError(backfillId)` → `PartitionBackfill`, `isAssetBackfill=true`, `assetSelection` paths contain `"chunks"` |
| Backend regression | `bash verify/checks.sh backend` | `ruff check` + `mypy dataplat_api` + `pytest -q` all exit 0; existing 125 tests continue to pass; new tests (≥9) also pass |
| Contract sync | `bash verify/checks.sh contract` | `make codegen` exits 0; `git diff --exit-code packages/api-types/` shows no uncommitted drift (or codegen is deferred per guard in checks.sh if Makefile absent) |

### Pre-flight steps for the implementer

1. Implement all five file changes listed in "What will be built".
2. Run `bash verify/checks.sh backend` — must be green before touching the stack.
3. Restart dagster-webserver to pick up the `chunks` stub:
   `docker compose -f docker/docker-compose.dev.yml restart dagster-webserver`
   Wait for it to become healthy (poll `/dagster_version` or `/server_info`).
4. Run `bash verify/checks.sh runs` — F018 checks must remain green; F024-V1/V2/V3 must pass.
5. Run `make codegen` (if Makefile present) and verify `packages/api-types/openapi.json` is updated.
6. Commit **all changed files in a single commit**: schemas, gateway, router, definitions.py,
   checks.sh, tests, openapi.json. This satisfies hard invariant #6.

---

## Out of scope

The following are explicitly deferred and MUST NOT be implemented in this sprint:

| Item | Reason / where it belongs |
|---|---|
| Real chunking body (text splitting, Lance writes) | F-025 — separate sprint |
| `deps=[extract_mineru]` lineage link on `chunks` asset | F-025 — add when real body exists |
| Chunking operator / plugin registration | Separate feature (plugin system, F-028+) |
| Status polling for `kind="chunk"` runs | Covered by existing GET `/api/runs/{run_id}` (F-005) — already works for any dagster_run_id |
| `GET /api/runs` list endpoint | F-049 |
| Idempotent re-trigger deduplication | Not in F-024 spec; consistent with F-018 behavior |
| Any migration | `kind: str` column on `run` already stores arbitrary strings; `"chunk"` is a new value, not a new column |
