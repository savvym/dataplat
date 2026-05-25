# S018-F-018 — Proposed Contract

**Status:** PROPOSED (reviewer iteration 1 applied — awaiting APPROVED)
**Date drafted:** 2026-05-25
**Author:** Leader (Claude)
**Sprint-id:** S018-F-018
**Depends on:** F-004 (passes: true), F-012 (passes: true), F-015 (passes: true)

---

## §1 Goal + Scope Boundary

F-018 implements the **trigger side only** of MinerU extraction:

- `POST /api/runs` with `{"asset": "extract_mineru", "source_ids": [<id>, ...]}` launches a
  Dagster asset backfill for the named partitions, inserts a `run` row into Postgres, and
  returns `{"dagster_run_id": "<backfillId>", "run_id": <int>}` with HTTP 202.

The use of a Dagster asset backfill (`launchPartitionBackfill`) is the design-doc-sanctioned
mechanism for this feature: design §5.4 (line 189) specifies `FastAPI → Dagster GraphQL:
launchPartitionBackfill`; line 214 repeats it; line 1008 defines submit as "trigger Dagster
backfill". The CLAUDE.md "MVP uses RQ" note refers to the plugin/operator execution sandbox
(how heavy operator code runs inside workers), not the asset-orchestration layer — F-004,
F-005, and F-012 all shipped on Dagster and establish the pattern this sprint follows.

**Explicit scope boundary:**
- This sprint does NOT implement real PDF→document extraction logic. The `extract_mineru`
  asset added to `definitions.py` is a **minimal stub** — its compute function logs the
  partition key and yields a trivial `MaterializeResult` (or returns `Output(None)`). This is
  enough for a backfill to launch and execute to success; it proves the wiring works without
  pretending to do extraction.
- The real MinerU compute body (calling the MinerU container, writing output, etc.) is
  **F-019**. Do NOT add any extraction logic here.
- The `extract_mineru` asset is wired into `Definitions(assets=[...])`. Dagster-webserver
  reads `definitions.py` at startup via the bind-mount established in F-012. After the
  file change, `docker compose restart dagster-webserver` (no image rebuild) picks it up —
  exactly as F-012 established.

---

## §2 Files Changed

| Path | New / Modified | Why |
|---|---|---|
| `dagster/dagster_platform/definitions.py` | **MODIFIED** | Add `@asset` stub `extract_mineru` partitioned by `sources_partitions`; add it to `Definitions(assets=[...])`. |
| `apps/api/dataplat_api/dagster/gateway.py` | **MODIFIED** | Add `launch_extract_backfill(partition_keys: list[str]) -> str` method + `_LAUNCH_EXTRACT_BACKFILL_MUTATION` string constant. |
| `apps/api/dataplat_api/routers/runs.py` | **MODIFIED** | Add `POST ""` (i.e. POST /api/runs) handler to `runs_router`; import `RunCreate`, `RunCreateResponse`, `Run` model, `get_session`. |
| `apps/api/dataplat_api/schemas/runs.py` | **MODIFIED** | Add `RunCreate` (request) and `RunCreateResponse` (response) schemas. |
| `verify/checks.sh` | **MODIFIED** | Add new `runs)` layer with F018-V1/V2/V3 checks; add `bash "$0" runs` to `all)` chain **before** `operators`. A `runs)` stub was already scaffolded by the `all)` chain at line 1131 — but the layer body is missing (falls through to `*) echo Unknown layer`). This sprint adds the real layer body. |
| `apps/api/tests/test_runs_trigger.py` | **NEW** | Pytest unit tests for the POST /api/runs handler using TestClient + dependency overrides. Five cases: (a) happy-path 202; (b) DagsterGatewayError → 503; (c) wrong asset → 422; (d) empty source_ids → 422; (e) missing source id → 404. Pattern mirrors `test_dagster_notify.py` / `test_sources_upload.py`. |
| `packages/api-types/openapi.json` | **MODIFIED** | Regenerated (new POST /api/runs route + RunCreate + RunCreateResponse schemas). Must be committed in the **same commit** (hard invariant #6). |

**Files NOT touched:**
- `apps/api/dataplat_api/main.py` — `runs_router` already included.
- `apps/api/dataplat_api/db/models.py` — `Run` model unchanged.
- Any Alembic migration — no DB schema change.
- `docs/data_platform_design.md` — read-only.

**Docker note:** After `definitions.py` is changed, the implementer must run
`docker compose -f docker/docker-compose.dev.yml restart dagster-webserver` (no rebuild)
before running `checks.sh runs`. The `runs)` check layer includes a webserver-ready wait
(≤60s) modelled on the `dagster)` layer.

---

## §3 Confirmed-Live `launchPartitionBackfill` GraphQL Signature

**All of the following was introspected against the live Dagster 1.11.16 instance
at localhost:13000 during contract drafting. Do NOT rely on design-doc pseudocode.**

### Mutation signature

```graphql
mutation LaunchExtractBackfill($backfillParams: LaunchBackfillParams!) {
  launchPartitionBackfill(backfillParams: $backfillParams) {
    __typename
    ... on LaunchBackfillSuccess {
      backfillId
    }
    ... on PartitionSetNotFoundError {
      message
    }
    ... on PartitionKeysNotFoundError {
      message
    }
    ... on PythonError {
      message
    }
    ... on UnauthorizedError {
      message
    }
    ... on InvalidSubsetError {
      message
    }
    ... on RunConflict {
      message
    }
  }
}
```

### `LaunchBackfillParams` input fields (confirmed live, all optional unless noted)

| Field | Type | Used |
|---|---|---|
| `selector` | `PartitionSetSelector` | NOT used (asset backfill uses `assetSelection` instead) |
| `partitionNames` | `[String!]` | YES — the `["src_1", "src_2", ...]` list |
| `partitionsByAssets` | `[PartitionsByAssetSelector]` | NOT used |
| `assetSelection` | `[AssetKeyInput!]` | YES — `[{"path": ["extract_mineru"]}]` |
| `fromFailure` | `Boolean` | NOT used |
| `allPartitions` | `Boolean` | NOT used |
| `tags` | `[ExecutionTag!]` | NOT used |
| `forceSynchronousSubmission` | `Boolean` | NOT used |
| `title` | `String` | optional, set to `"F-018 extract_mineru"` for traceability |
| `description` | `String` | NOT used |
| `runConfigData` | `RunConfigData` | NOT used |

**Chosen params shape:**
```python
{
    "backfillParams": {
        "assetSelection": [{"path": ["extract_mineru"]}],
        "partitionNames": ["src_1", "src_2"],   # list of converted source_ids
        "title": "F-018 extract_mineru",
    }
}
```

### Return union type: `LaunchBackfillResult` (confirmed live)

Members: `LaunchBackfillSuccess`, `PartitionSetNotFoundError`, `PartitionKeysNotFoundError`,
`InvalidStepError`, `InvalidOutputError`, `RunConfigValidationInvalid`, `PipelineNotFoundError`,
`RunConflict`, `UnauthorizedError`, `PythonError`, `InvalidSubsetError`, `PresetNotFoundError`,
`ConflictingExecutionParamsError`, `NoModeProvidedError`.

### `LaunchBackfillSuccess` fields (confirmed live)

- `backfillId: String!` — the id we store as `run.dagster_run_id`
- `launchedRunIds` — present but type not fully unwrapped; not used

**Decision: `backfillId` → `dagster_run_id`.** The `launchPartitionBackfill` mutation
returns a single `backfillId` string, not a per-partition run UUID. A backfill spawns
N individual per-partition Dagster runs (one per source id). We track at the **backfill
grain** in the `run` table: `run.dagster_run_id = backfillId`. This is the only stable
identifier the mutation provides. Documented as MVP granularity; per-partition run
tracking (via `launchedRunIds`) is deferred.

---

## §4 `extract_mineru` Asset — Stub Design

### Why `@asset` not `AssetSpec`

`AssetSpec` creates an **external** asset (Dagster does not execute it). External assets
cannot be backfilled — `launchPartitionBackfill` requires a materializable asset in the
code location. Therefore `extract_mineru` must be a `@asset`-decorated function.

### Stub definition (to be placed in `definitions.py`)

```python
from dagster import asset, AssetExecutionContext, MaterializeResult

@asset(
    partitions_def=sources_partitions,
    description="MinerU extraction stub — F-018 trigger wiring. Real extraction logic: F-019.",
)
def extract_mineru(context: AssetExecutionContext) -> MaterializeResult:
    """Stub asset: logs the partition key and yields a trivial result.

    F-018 scope: wiring only. The real MinerU PDF→document extraction body
    is F-019. Do NOT add extraction logic here.
    """
    partition_key = context.partition_key
    context.log.info("extract_mineru stub: partition_key=%s", partition_key)
    return MaterializeResult()
```

Add to `Definitions`:
```python
defs = Definitions(
    jobs=[hello_world_job],
    assets=[source_asset, extract_mineru],
)
```

Update import line: add `asset`, `AssetExecutionContext`, `MaterializeResult` from `dagster`.

### Dagster-webserver restart

After `definitions.py` is saved (bind-mounted), run:
```bash
docker compose -f docker/docker-compose.dev.yml restart dagster-webserver
```
No image rebuild needed (F-012 established). Wait up to 60s for the webserver to
become healthy before the backfill mutation fires.

---

## §5 Endpoint Contract

### §5.1 Route

```
POST /api/runs
```

Handler added to `runs_router` (prefix `/api/runs`) at path `""`. This resolves to
`POST /api/runs`. The existing `GET /{run_id}` handler is at path `"/{run_id}"` —
no shadowing concern (POST vs GET, and empty path vs parameterised path are distinct).

### §5.2 Request schema — `RunCreate`

```python
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

class RunCreate(BaseModel):
    asset: Literal["extract_mineru"]
    source_ids: Annotated[list[int], Field(min_length=1)]

    model_config = ConfigDict(extra="ignore")
```

`Literal["extract_mineru"]` makes Pydantic v2 raise `ValidationError` for any other
value, which FastAPI translates to 422. `Field(min_length=1)` enforces non-empty
`source_ids` at the schema level, also producing 422 without handler logic.

### §5.3 Validation rules

| Rule | Status code | Detail |
|---|---|---|
| `asset` not `"extract_mineru"` | **422** | FastAPI ValidationError via `Literal["extract_mineru"]` field annotation (see below) |
| `source_ids` empty list | **422** | FastAPI ValidationError via `min_length=1` constraint |
| Any `source_id` references a non-existent source | **404** | Handler queries `source` table; returns 404 if any id missing |
| Dagster launch fails | **503** | `DagsterGatewayError` caught → JSONResponse(503) |
| Postgres commit fails (after successful launch) | **500** | Unhandled exception → FastAPI 500 (documented acceptable leak, see §7) |

**`asset` validation decision:** Use `Literal["extract_mineru"]` as the field type.
Pydantic v2 raises `ValidationError` → FastAPI returns 422 for any other value. This
is cleaner than a manual if-check and makes future assets enumerable in the OpenAPI schema.
Rationale for 422 (not 400): FastAPI's standard for request body validation failures is 422.

**`source_ids` validation decision:** Validate existence up front. The handler queries
`SELECT id FROM source WHERE id = ANY(:ids)` and compares the returned set to the input
set. If any ids are missing → 404, `detail="Source not found: {missing_ids}"`. Rationale:
a partition key `src_{id}` for a non-existent source would make the backfill fail at
execution time in Dagster with an opaque error. Failing fast at the API layer with a 404
is a much better user experience and is consistent with the rest of the codebase (F-014,
F-013 return 404 for non-existent resources). Note: RFC 9110 guidance suggests 422 for
body-level semantic failures, but 404 is used here intentionally — the missing source id
is a reference to a non-existent resource (not merely a semantic schema violation), which
is precisely the 404 case. Future reviewers should not re-flag this as a deviation.

**Partition key conversion:** `source_ids` → `[f"src_{id}" for id in source_ids]`.
These partition keys were registered in Dagster's `sources` partition definition when
each source was uploaded (F-012). They are NOT registered in the `sources_partitions`
definition used by `extract_mineru` — see §8 Open Questions OQ-1 for the required
pre-backfill partition registration step.

### §5.4 Response schema — `RunCreateResponse`

```python
class RunCreateResponse(BaseModel):
    dagster_run_id: str   # the backfillId from LaunchBackfillSuccess
    run_id: int           # the Postgres run.id
```

### §5.5 Status codes

| Status | When |
|---|---|
| 202 Accepted | Backfill launched + run row committed |
| 401 | Missing/invalid/expired Bearer token |
| 404 | Any source_id in source_ids does not exist in the `source` table |
| 422 | `asset` is not `"extract_mineru"`, or `source_ids` is empty, or body missing |
| 503 | Dagster unreachable or `launchPartitionBackfill` returns an error typename |

---

## §6 Run-Row Field Mapping

Every NOT NULL column in `run` must be set at insert time (no server defaults available
for most). Nullable columns are set where meaningful.

| Column | NOT NULL? | Value set at insert |
|---|---|---|
| `dagster_run_id` | YES (UNIQUE) | `backfillId` from `LaunchBackfillSuccess` |
| `kind` | YES | `"extract"` |
| `asset_keys` | YES (ARRAY) | `["extract_mineru"]` |
| `status` | YES | `"pending"` |
| `partition_keys` | nullable (server_default `'{}'`) | `["src_1", "src_2", ...]` — the converted source partition keys |
| `triggered_by` | nullable | `current_user.id` |
| `config` | nullable | `None` |
| `trigger_context` | nullable | `None` |
| `source_collection_id` | nullable | `None` (no collection specified in request) |
| `dataset_id` | nullable | `None` |
| `recipe_id` | nullable | `None` |
| `started_at` | nullable | `None` (run not yet started; status='pending') |
| `ended_at` | nullable | `None` |

The `id` column is `sa.Identity()` — assigned by Postgres on INSERT, available after
`session.flush()` or `session.commit()` + `session.refresh()`.

---

## §7 Insert/Launch Ordering + Failure Semantics

**Ordering (agreed):**

1. Validate request (422 on bad `asset` or empty `source_ids`).
2. Validate source existence (404 if any missing).
3. Convert `source_ids` → `partition_keys` (`src_{id}`).
4. Register partition keys in `sources_partitions` via `addDynamicPartition`
   for each key — see OQ-1. Best-effort (duplicates are no-ops per F-012 gateway).
5. Call `gateway.launch_extract_backfill(partition_keys)` → get `backfill_id`.
   On `DagsterGatewayError` → return 503. NO run row is inserted.
6. Build `Run(dagster_run_id=backfill_id, kind="extract", status="pending", ...)`.
7. `session.add(run)` → `await session.commit()` → `await session.refresh(run)`.
8. Return 202 `{"dagster_run_id": backfill_id, "run_id": run.id}`.

**Failure semantics:**

- **Dagster launch fails (step 5):** `DagsterGatewayError` caught → 503. No DB row
  inserted. User can retry.
- **DB commit fails after successful launch (step 7):** Unhandled exception → 500.
  The backfill is already running in Dagster with no corresponding `run` row — an
  **orphan backfill**. This is an acceptable MVP leak, identical in philosophy to the
  F-011 orphan MinIO object and F-012 orphan Dagster partition. Documented here; no
  compensating transaction in MVP.
- **Source validation fails (step 2):** 404 before any Dagster or DB interaction —
  no side effects.

**Why launch-first, not insert-first:**
`run.dagster_run_id` is `NOT NULL UNIQUE`. We cannot insert the row with a placeholder
(no UNIQUE-safe placeholder exists), and we cannot know the `backfillId` until after
the mutation succeeds. Therefore the launch must come first.

---

## §8 Verification Mapping

**Placement:** The existing `runs)` case body in `checks.sh` (lines 408–483) covers
F-005 (hello-world trigger + poll + boundary grep). It already mints `$RUNS_TOKEN` and
sets `FASTAPI_HOST_PORT` at the top of that block. F-018 **extends this existing case**
by appending the F018 source-upload setup + V1/V2/V3 checks immediately before the
existing closing `;;` of the `runs)` case. The `all)` chain already calls
`bash "$0" runs` at line 1131 — no change to `all)` needed.

**Do NOT add a second token mint.** `$RUNS_TOKEN` and `$FASTAPI_HOST_PORT` are already
in scope from the F-005 block above. The F018 additions start at the source-upload step.

### F018 setup: upload a source (appended after the F-005 boundary grep, before `;;`)

```bash
    # F-018: POST /api/runs — trigger MinerU extraction backfill.
    # $RUNS_TOKEN and $FASTAPI_HOST_PORT already set by the F-005 block above.
    COMPOSE_F018="docker/docker-compose.dev.yml"

    echo "--- runs F018 setup: upload a source for backfill test ---"
    F018_PDF=$(mktemp /tmp/f018-XXXXXX.pdf)
    python3 -c "
pdf = (b'%PDF-1.4\n1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n'
       b'2 0 obj<</Type /Pages /Kids[3 0 R] /Count 1>>endobj\n'
       b'3 0 obj<</Type /Page /MediaBox[0 0 612 792] /Parent 2 0 R>>endobj\n'
       b'xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n'
       b'0000000058 00000 n \n0000000115 00000 n \n'
       b'trailer<</Size 4 /Root 1 0 R>>\nstartxref\n182\n%%EOF\nf018')
open('$F018_PDF', 'wb').write(pdf)
"
    F018_UP_BODY=$(mktemp)
    F018_UP_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/upload" \
      -H "Authorization: Bearer $RUNS_TOKEN" \
      -F "file=@${F018_PDF};type=application/pdf" \
      -w '%{http_code}' -o "$F018_UP_BODY")
    rm -f "$F018_PDF"
    test "$F018_UP_STATUS" = "201" \
      || { echo "FAIL: runs F018 setup upload returned $F018_UP_STATUS: $(cat "$F018_UP_BODY")"; rm -f "$F018_UP_BODY"; exit 1; }
    F018_SRC_ID=$(python3 -c "import json; print(json.load(open('$F018_UP_BODY'))['id'])")
    rm -f "$F018_UP_BODY"
    echo "  uploaded source id=$F018_SRC_ID"
```

### F018-V1: POST /api/runs → 202 with dagster_run_id + run_id

```bash
    echo "--- runs F018-V1: POST /api/runs returns 202 with dagster_run_id + run_id ---"
    V1_BODY=$(mktemp)
    V1_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/runs" \
      -H "Authorization: Bearer $RUNS_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"asset\": \"extract_mineru\", \"source_ids\": [${F018_SRC_ID}]}" \
      -w '%{http_code}' -o "$V1_BODY")
    test "$V1_STATUS" = "202" \
      || { echo "FAIL: F018-V1 returned $V1_STATUS: $(cat "$V1_BODY")"; rm -f "$V1_BODY"; exit 1; }
    python3 -c "
import json, sys
body = json.load(open('$V1_BODY'))
assert 'dagster_run_id' in body, f'missing dagster_run_id: {body}'
assert 'run_id' in body, f'missing run_id: {body}'
assert isinstance(body['dagster_run_id'], str) and len(body['dagster_run_id']) > 0, \
  f'dagster_run_id empty or not str: {body}'
assert isinstance(body['run_id'], int), f'run_id not int: {body}'
print('  F018-V1 OK: dagster_run_id=%s run_id=%d' % (body['dagster_run_id'], body['run_id']))
" || { echo "FAIL: F018-V1 response shape incorrect"; rm -f "$V1_BODY"; exit 1; }
    F018_BACKFILL_ID=$(python3 -c "import json; print(json.load(open('$V1_BODY'))['dagster_run_id'])")
    F018_RUN_ID=$(python3 -c "import json; print(json.load(open('$V1_BODY'))['run_id'])")
    rm -f "$V1_BODY"
```

### F018-V2: run row exists in Postgres with kind='extract' and status='pending'

F-018 writes the row once at insert with `status='pending'` and never updates it
(run-status sync is a future feature). The check asserts `status='pending'` exactly.
(OQ-4 DECIDED: status='pending' exact — no 'running' branch needed.)

```bash
    echo "--- runs F018-V2: run row exists with kind=extract, status=pending ---"
    docker compose -f "$COMPOSE_F018" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "SELECT kind || '|' || status FROM run WHERE id=${F018_RUN_ID}" \
      | grep -q '^extract|pending$' \
      || { echo "FAIL: F018-V2 — run row missing or wrong kind/status (expected extract|pending)"; exit 1; }
    echo "  F018-V2 OK: run row kind=extract status=pending"
```

### F018-V3: Dagster shows backfill for extract_mineru

V3 queries `partitionBackfillOrError(backfillId: $id)` (confirmed-live query — see §3)
against the Dagster GraphQL endpoint at container-internal `localhost:3000` (via
`docker compose exec dagster-webserver`) and asserts `isAssetBackfill=true` and
`assetSelection` includes `extract_mineru`.

```bash
    echo "--- runs F018-V3: Dagster shows backfill for extract_mineru ---"
    BACKFILL_CHECK=$(docker compose -f "$COMPOSE_F018" exec -T dagster-webserver \
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
    'variables': {'id': '$F018_BACKFILL_ID'}
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
assert 'extract_mineru' in paths, f'extract_mineru not in assetSelection paths: {paths}'
print(f'  F018-V3 OK: backfillId={result[\"id\"]} status={result[\"status\"]} assets={paths}')
" 2>&1)
    echo "$BACKFILL_CHECK" | grep -q "FAIL" \
      && { echo "$BACKFILL_CHECK"; exit 1; } || echo "$BACKFILL_CHECK"
```

---

## §9 `gateway.py` — New Method

```python
_LAUNCH_EXTRACT_BACKFILL_MUTATION = """
mutation LaunchExtractBackfill($backfillParams: LaunchBackfillParams!) {
  launchPartitionBackfill(backfillParams: $backfillParams) {
    __typename
    ... on LaunchBackfillSuccess {
      backfillId
    }
    ... on PartitionSetNotFoundError {
      message
    }
    ... on PartitionKeysNotFoundError {
      message
    }
    ... on PythonError {
      message
    }
    ... on UnauthorizedError {
      message
    }
    ... on InvalidSubsetError {
      message
    }
    ... on RunConflict {
      message
    }
  }
}
"""
```

Method signature:

```python
async def launch_extract_backfill(self, partition_keys: list[str]) -> str:
    """Launch an asset backfill for extract_mineru over the given partition keys.

    Returns the backfillId (string) from LaunchBackfillSuccess.
    Raises DagsterGatewayError for all failure cases.
    """
```

Variables passed:
```python
{
    "backfillParams": {
        "assetSelection": [{"path": ["extract_mineru"]}],
        "partitionNames": partition_keys,
        "title": "F-018 extract_mineru",
    }
}
```

The method uses the gateway's existing `httpx.AsyncClient(timeout=10.0)` default — sufficient
for MVP because `launchPartitionBackfill` is a quick enqueue operation (Dagster acknowledges
the backfill synchronously and returns a `backfillId` before any per-partition execution
begins; the actual extraction runs asynchronously in workers).

Error handling follows exactly the same pattern as `launch_hello_world`:
- httpx network errors → `DagsterGatewayError`
- HTTP non-2xx → `DagsterGatewayError`
- Non-JSON response → `DagsterGatewayError`
- GraphQL `errors` key present → `DagsterGatewayError`
- `__typename` not `LaunchBackfillSuccess` → `DagsterGatewayError` with the `message` field
- `backfillId` absent or empty → `DagsterGatewayError`

---

## §10 Invariant Compliance

| # | Invariant | Status |
|---|---|---|
| 1 | Lineage mandatory | N/A — no Commit object; run row has triggered_by + asset_keys + partition_keys which is the relevant provenance for a Run. Full lineage (parents, config hash) is F-030+. |
| 2 | Storage separation + CAS | N/A — no blob content. |
| 3 | Schema frozen post-publish | N/A |
| 4 | LLM calls through gateway | N/A — no LLM call. The `launch_extract_backfill` method lives in `dataplat_api/dagster/gateway.py` — the correct single chokepoint. The route handler MUST NOT call httpx directly. The `dagster)` layer's boundary grep (`grep -rln httpx apps/api/dataplat_api --include='*.py' \| grep -v dagster/`) will catch any violation. |
| 5 | Async SQLAlchemy only | **REQUIRED.** Source existence check: `await session.execute(select(Source.id).where(Source.id.in_(source_ids)))`. Run insert: `session.add(run)` → `await session.commit()` → `await session.refresh(run)`. No `session.query()`, no sync sessions anywhere. |
| 6 | OpenAPI ↔ TS type sync | **REQUIRED.** New route POST /api/runs + new schemas `RunCreate`/`RunCreateResponse` change OpenAPI output. Implementer MUST regenerate `packages/api-types/openapi.json` in the same commit via the established manual export command. |

**Boundary enforcement:** The `dagster)` layer in `checks.sh` already contains a grep
that forbids raw httpx outside `dataplat_api/dagster/`. No new enforcement needed —
the existing check covers the new method automatically.

---

## §11 Open Questions

1. **OQ-1 — `extract_mineru` partition pre-registration (CRITICAL):**
   `sources_partitions` (used by the `source` external asset) and the partition definition
   used by `extract_mineru` are the SAME `DynamicPartitionsDefinition(name="sources")`.
   `launchPartitionBackfill` with `partitionNames=["src_1"]` will fail at Dagster execution
   time if `src_1` is not registered in the `sources` partition definition. When a source is
   uploaded (F-012), `addDynamicPartition` registers it in `sources`. So a source uploaded
   BEFORE this sprint has its partition registered, and the backfill should work. However,
   the F-018 handler should also defensively call `add_source_partition` for each partition
   key before launching the backfill (idempotent — `DuplicateDynamicPartitionError` is a
   no-op per the existing gateway). The contract proposes: call `add_source_partition` for
   each key as step 4 in §7. The reviewer should confirm or override this.

2. **OQ-2 — `asset` field as `Literal["extract_mineru"]` vs. runtime string check:**
   Using `Literal["extract_mineru"]` in the Pydantic model ties the schema tightly to this
   one value and emits a clean enum in OpenAPI. As new assets are added in future sprints,
   the Literal will need to be updated (or replaced with a `str` + validator). The reviewer
   should confirm `Literal` is acceptable for now.

3. **OQ-3 — `sources_partitions` shared between `source` and `extract_mineru`:**
   Both assets share the same `DynamicPartitionsDefinition(name="sources")`. This means
   the `sources_partitions` variable must be referenced by `extract_mineru`. In
   `definitions.py`, `sources_partitions` is already defined at module level (line 13), so
   `@asset(partitions_def=sources_partitions, ...)` is straightforward. No change to the
   partition definition itself. The reviewer should confirm this sharing is acceptable.

4. **OQ-4 — status field value: DECIDED.** `status='pending'` is written at insert
   and never updated by F-018 (run-status sync is a future feature). V2 checks
   `grep -q '^extract|pending$'` — exact match, no 'running' branch. Future sprints
   that add status-sync will need to update V2 accordingly.

---

## §12 Files Summary

Total: **6 files** modified + **1 file** new = 7 files.

```
dagster/dagster_platform/definitions.py             MODIFIED (add extract_mineru @asset stub)
apps/api/dataplat_api/dagster/gateway.py            MODIFIED (add launch_extract_backfill method)
apps/api/dataplat_api/routers/runs.py               MODIFIED (add POST "" handler to runs_router)
apps/api/dataplat_api/schemas/runs.py               MODIFIED (add RunCreate, RunCreateResponse)
apps/api/tests/test_runs_trigger.py                 NEW      (5 pytest cases: 202/503/422/422/404)
verify/checks.sh                                    MODIFIED (extend runs) layer with F018-V1/V2/V3)
packages/api-types/openapi.json                     MODIFIED (regenerated, same commit)
```
