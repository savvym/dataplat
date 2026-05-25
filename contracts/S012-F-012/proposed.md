# S012-F-012 — Proposed Contract

**Status:** PROPOSED
**Date drafted:** 2026-05-25
**Author:** Leader (Claude)
**Sprint-id:** S012-F-012

---

## §1 Objective & Scope

**Goal:** After a successful source upload (`POST /api/sources/upload`, F-011), FastAPI notifies Dagster by:
1. Adding a dynamic partition key (`src_{source_id}`) to the `"sources"` `DynamicPartitionsDefinition`.
2. Reporting a runless asset materialization event for the external asset keyed `"source"` with that partition key.

Both mutations must go through `DagsterGateway` (boundary invariant). The `"sources"` partition definition and the `"source"` external asset must first be declared in `dagster/dagster_platform/definitions.py`. This sprint adds a bind mount for `dagster/` to all four dagster services in `docker-compose.dev.yml` so code changes take effect after a container restart — no image rebuild required for local dev (see §3-U1 and §3-D-bindmount-scope).

### In scope

- `dagster/dagster_platform/definitions.py` — add `DynamicPartitionsDefinition(name="sources")` and `AssetSpec(key="source", partitions_def=sources_partitions)` to `Definitions`.
- `apps/api/dataplat_api/dagster/gateway.py` — add two new async methods: `add_source_partition(partition_key)` and `report_source_materialization(partition_key, storage_uri, size_bytes)`.
- `apps/api/dataplat_api/routers/sources.py` — wire `gateway: DagsterGateway = Depends(get_dagster_gateway)` into `upload_source`; call both gateway methods (best-effort) after `session.commit()`.
- `apps/api/tests/test_dagster_notify.py` — NEW unit tests for the two gateway methods and the handler best-effort path.
- `verify/checks.sh` — extend the `dagster)` layer to add F-012 partition + materialization assertions; document the container-restart step (bind mount means no image rebuild needed for local dev).
- `docker/docker-compose.dev.yml` — add bind mount for `dagster/` to enable dev-cycle iteration without full rebuilds (see §3-U1 for rationale and exact change).

### Explicit non-goals (out of scope)

- `GET /api/runs` list endpoint — deferred to F-049.
- `Run` table row insertion — no real `dagster_run_id` exists for a notify-only operation; inserting a synthetic one would pollute the `Run` table (see §3-D-run-row).
- Any source extraction / processing logic (F-018).
- Schema-frozen concerns — no Silver/Gold commit in this sprint.
- Retry logic for Dagster outages — future reconciliation job, not MVP.
- Non-PDF source types, `GET /api/sources/{id}`, list-sources endpoints.

### Dependency confirmation

| Dependency | Required state | Evidence |
|---|---|---|
| F-004 (DagsterGateway) | `passes: true` | `gateway.py` exists with full error handling; `dagster)` layer passes. |
| F-011 (upload handler) | `passes: true` | `routers/sources.py` `upload_source` implemented; `sources)` layer passes. |

---

## §2 Files Changed

| Path | New / Modified | Summary of change |
|---|---|---|
| `dagster/dagster_platform/definitions.py` | MODIFIED | Add `sources_partitions = DynamicPartitionsDefinition(name="sources")` and `source_asset = AssetSpec(key="source", partitions_def=sources_partitions)`; wire both into `Definitions(jobs=[hello_world_job], assets=[source_asset])`. |
| `apps/api/dataplat_api/dagster/gateway.py` | MODIFIED | Add module-level mutation strings `_ADD_SOURCE_PARTITION_MUTATION` and `_REPORT_SOURCE_MATERIALIZATION_MUTATION`; add `add_source_partition(partition_key: str) -> None` and `report_source_materialization(partition_key: str, storage_uri: str, size_bytes: int) -> None` methods. |
| `apps/api/dataplat_api/routers/sources.py` | MODIFIED | Add `import logging` and module-level `logger = logging.getLogger(__name__)`; import `get_dagster_gateway` and `DagsterGateway`; add `gateway: DagsterGateway = Depends(get_dagster_gateway)` to `upload_source` signature; add best-effort notify block after `session.commit()`. |
| `apps/api/tests/test_dagster_notify.py` | NEW | Unit tests for F-012 (detailed in §6). |
| `verify/checks.sh` | MODIFIED | Extend `dagster)` layer with F-012 assertions (restart step + partition query + materialization event query). |
| `docker/docker-compose.dev.yml` | MODIFIED | Add bind mount `- ../dagster:/app/dagster` to ALL FOUR dagster services (`dagster-webserver`, `dagster-daemon`, `dagster-worker-cpu`, `dagster-worker-heavy`) so code changes take effect on restart without an image rebuild. See §3-U1 and §3-D-bindmount-scope. |

**Files NOT touched:**

- `apps/api/dataplat_api/db/models.py` — no new columns, no run row.
- Any migration file — no schema change.
- `apps/api/dataplat_api/dagster/dependencies.py` — `get_dagster_gateway` already correct.
- `packages/api-types/openapi.json` — upload response schema unchanged; no new route; regen produces empty diff. Still MUST run `make codegen` (or equivalent) to confirm no drift (see §8-Invariant6).

---

## §3 Design Decisions

### U1 — Dagster code reload: bind mount added this sprint; restart only, no rebuild

**Finding (confirmed):** Before this sprint, every dagster service (`dagster-webserver`, `dagster-daemon`, `dagster-worker-cpu`, `dagster-worker-heavy`) is built from `docker/dagster/Dockerfile` which `COPY dagster/ /app/dagster/` at build time, with `volumes: []`. If `addDynamicPartition` is called while `dagster-webserver` runs stale code (no `DynamicPartitionsDefinition(name="sources")` loaded), Dagster returns `{"__typename": "UnauthorizedError", "message": "The repository does not contain a dynamic partitions definition with the given name."}` — confirmed live.

**Fix (this sprint):** Add a bind mount `- ../dagster:/app/dagster` to all four dagster services in `docker-compose.dev.yml`. A Docker bind mount **shadows** the image's `COPY`-ed `/app/dagster` directory the instant the container is recreated with `docker compose up -d`, so the live host tree is served directly. The key mechanics:

- `docker compose up -d` (recreate, not build) → container now mounts the host `dagster/` directory. No image rebuild needed.
- `docker compose restart dagster-webserver` → Python re-imports `dagster_platform.definitions` from the bind-mounted host file. The Dagster webserver does NOT hot-reload; a restart is always required to pick up Python module changes.
- The `Dockerfile`'s `COPY dagster/` remains unchanged — it is the fallback for CI/production environments that do not use the compose bind mount.

**Local dev loop for this sprint and all future dagster sprints:**

```bash
# After editing dagster/dagster_platform/definitions.py:
docker compose -f docker/docker-compose.dev.yml up -d \
  dagster-webserver dagster-daemon dagster-worker-cpu dagster-worker-heavy
docker compose -f docker/docker-compose.dev.yml restart dagster-webserver
# Wait for dagster-webserver healthy (~10s), then checks pass.
```

No `docker compose build` is needed for local dev once the bind mount is in place. Image rebuilds (`docker compose build`) are only required when the `Dockerfile` itself changes (e.g., new Python dependency installed via pip).

### U2 — Confirmed GraphQL mutation signatures (LIVE Dagster 1.11.16)

Two mutations exist that differ from the design doc pseudocode. The confirmed live signatures are:

**Mutation 1: `addDynamicPartition` (singular, NOT `addDynamicPartitions` plural)**

```graphql
mutation AddSourcePartition(
  $partitionKey: String!
  $partitionsDefName: String!
  $repositorySelector: RepositorySelector!
) {
  addDynamicPartition(
    partitionKey: $partitionKey
    partitionsDefName: $partitionsDefName
    repositorySelector: $repositorySelector
  ) {
    __typename
    ... on AddDynamicPartitionSuccess {
      partitionKey
      partitionsDefName
    }
    ... on DuplicateDynamicPartitionError {
      partitionsDefName
      partitionKey: partitionName
      message
    }
    ... on UnauthorizedError {
      message
    }
    ... on PythonError {
      message
    }
  }
}
```

- `repositorySelector` is REQUIRED (`RepositorySelector!` with `repositoryName: String!` and `repositoryLocationName: String!`). Values: same constants already used in `gateway.py` — `_REPOSITORY_NAME = "__repository__"` and `_REPOSITORY_LOCATION_NAME = "dagster_platform.definitions"`.
- Return union `AddDynamicPartitionResult` has four possible types: `AddDynamicPartitionSuccess`, `DuplicateDynamicPartitionError`, `UnauthorizedError`, `PythonError`.
- **CRITICAL NAMING CONFUSION:** `UnauthorizedError` is NOT an auth error in this context — it is also returned with message `"The repository does not contain a dynamic partitions definition with the given name."` when the partition def does not exist in the loaded code. The gateway method MUST treat any `UnauthorizedError` as a `DagsterGatewayError` (since in OSS Dagster without auth, it indicates a code-location / def-missing error).
- `DuplicateDynamicPartitionError` is returned (not a hard error) when the partition key already exists. The gateway treats this as a no-op success (see §5-idempotency).
- Design doc used `addDynamicPartitions` (plural, `partitionKeys: [String!]!`) — the LIVE SCHEMA wins. We use the singular form confirmed by introspection.

**Mutation 2: `reportRunlessAssetEvents` (NOT `reportRuntimeAssetMaterialization`)**

```graphql
mutation ReportSourceMaterialization($params: ReportRunlessAssetEventsParams!) {
  reportRunlessAssetEvents(eventParams: $params) {
    __typename
    ... on ReportRunlessAssetEventsSuccess {
      assetKey {
        path
      }
    }
    ... on UnauthorizedError {
      message
    }
    ... on PythonError {
      message
    }
  }
}
```

- `ReportRunlessAssetEventsParams` is an input object with:
  - `eventType: AssetEventType!` — enum, use `ASSET_MATERIALIZATION`.
  - `assetKey: AssetKeyInput!` — input object with `path: [String!]!`; for the `"source"` asset use `path: ["source"]`.
  - `partitionKeys: [String]` — list (nullable); pass `[partition_key]`.
  - `description: String` — optional free-text. Use this to carry the storage URI (e.g. `"s3://sources/42/original.pdf"`). The live schema has NO `metadata` field — the design doc pseudocode `metadata: JSONString` does NOT exist in Dagster 1.11.16.
- Does NOT require a `repositorySelector`.
- Return union `ReportRunlessAssetEventsResult`: `ReportRunlessAssetEventsSuccess`, `UnauthorizedError`, `PythonError`.
- Confirmed working live (returns `{"__typename": "ReportRunlessAssetEventsSuccess"}`) with an asset key that has no partition def loaded yet — it does NOT require the asset to be pre-declared with a partition def (the materialization event is accepted regardless). However, the Dagster UI lineage only shows the partition if the asset node has `partitions_def` declared. So both mutations are needed for the full UI experience.

**Design doc pseudocode discrepancies** (confirmed live schema takes precedence):
- `addDynamicPartitions(plural)` → correct is `addDynamicPartition(singular)` with `partitionKey: String!`.
- `reportRuntimeAssetMaterialization` → correct is `reportRunlessAssetEvents` with `eventParams: ReportRunlessAssetEventsParams!`.
- `metadata: JSONString` → does NOT exist; use `description: String` instead.
- `reportRunlessAssetEvents` does NOT require `repositorySelector`; `addDynamicPartition` DOES.

### D-ordering — Best-effort, after commit

**Decision: best-effort — catch `DagsterGatewayError`, log at WARNING, still return 201.**

Rationale:
- The upload is durable in Postgres + MinIO before the notify step. The source genuinely exists.
- Returning 500 after commit would tell the client "upload failed" when it actually succeeded — the client would retry, creating a duplicate source row. This is the worst outcome.
- Rolling back after commit is impossible (SQLAlchemy cannot undo a committed transaction).
- A Dagster outage is an operational issue, not a source-data corruption. The partition can be re-registered by a future reconciliation job or manual script.
- The best-effort approach matches the "notification" semantic: FastAPI is announcing a fact (the source was uploaded), not depending on Dagster to confirm it.

The alternative (raise 500) is rejected: "upload failed, please retry" is a lie when the DB row and S3 object exist. The retry creates a second row.

**Known limitation:** If the Dagster notify fails (connection error, "sources" def not loaded, etc.), the `source.dagster_partition_key` column correctly stores `src_{id}` (set before commit), but the corresponding Dagster partition entry is absent. The partition is orphaned in the API DB. A future reconciliation job (not in this sprint) can query `source` rows where `dagster_partition_key` is not in Dagster's partition list and re-notify.

**Exact sequence after `await session.commit()` in `upload_source`:**

```python
# --- F-012: best-effort Dagster notification ---
partition_key = source.dagster_partition_key   # e.g. "src_42"
try:
    await gateway.add_source_partition(partition_key)
except DagsterGatewayError as exc:
    logger.warning(
        "F-012: add_source_partition failed for %s — Dagster may be down; "
        "partition not registered. Upload still succeeds. Error: %s",
        partition_key, exc,
    )
try:
    await gateway.report_source_materialization(
        partition_key=partition_key,
        storage_uri=source.storage_uri,
        size_bytes=source.size,
    )
except DagsterGatewayError as exc:
    logger.warning(
        "F-012: report_source_materialization failed for %s — "
        "materialization event not recorded. Upload still succeeds. Error: %s",
        partition_key, exc,
    )
# --- end F-012 ---
return SourceUploadResponse(id=source.id, storage_uri=source.storage_uri)
```

The two gateway calls are separate `try/except` blocks. If `add_source_partition` fails, we still attempt `report_source_materialization` (it may succeed even without the partition being formally registered). Each failure is logged at `WARNING` level with the partition key for traceability.

### D-run-row — No `Run` table row inserted

**Decision: no run row, no `GET /api/runs` list endpoint.**

A Dagster "notify" (addDynamicPartition + reportRunlessAssetEvents) does NOT create a Dagster run object. There is no real `dagster_run_id` to record. The `Run.dagster_run_id` column is `NOT NULL UNIQUE` — inserting a synthetic UUID would pollute the table with fake entries that have no corresponding Dagster execution. This would break the integrity assumption of the `Run` table (every row = a real Dagster run).

Verification criterion 3 ("GET /api/runs listing after upload shows a run record with kind='upload' OR the source appears in partition list") is satisfied via the OR-branch: the source appears in the Dagster partition list. This is demonstrated by verification criterion 1 (the partition query). GET /api/runs list (F-049) is deferred.

### D-asset — `AssetSpec` with `partitions_def` in `Definitions`

**Confirmed working in dagster 1.11.16 container:**

```python
from dagster import Definitions, DynamicPartitionsDefinition, AssetSpec, job, op

sources_partitions = DynamicPartitionsDefinition(name="sources")
source_asset = AssetSpec(key="source", partitions_def=sources_partitions)

defs = Definitions(
    jobs=[hello_world_job],
    assets=[source_asset],
)
```

`AssetSpec` is available directly from `dagster` in 1.11.16 (confirmed: `hasattr(dagster, 'AssetSpec') == True`). `external_asset_from_spec` does NOT exist (`hasattr(dagster, 'external_asset_from_spec') == False`), so we use `AssetSpec` directly passed to `Definitions(assets=[...])`. This is the correct pattern for declaring an external asset in this version.

### D-gateway — Two new methods on `DagsterGateway`

Following the exact pattern of `launch_hello_world` (module-level mutation string, `{"query": ..., "variables": ...}` payload, layered error handling raising `DagsterGatewayError`):

**Method 1: `add_source_partition(self, partition_key: str) -> None`**

Module-level string:
```python
_ADD_SOURCE_PARTITION_MUTATION = """
mutation AddSourcePartition(
  $partitionKey: String!
  $partitionsDefName: String!
  $repositorySelector: RepositorySelector!
) {
  addDynamicPartition(
    partitionKey: $partitionKey
    partitionsDefName: $partitionsDefName
    repositorySelector: $repositorySelector
  ) {
    __typename
    ... on AddDynamicPartitionSuccess {
      partitionKey
      partitionsDefName
    }
    ... on DuplicateDynamicPartitionError {
      partitionsDefName
      partitionName
      message
    }
    ... on UnauthorizedError {
      message
    }
    ... on PythonError {
      message
    }
  }
}
"""
```

Error handling:
- `DuplicateDynamicPartitionError` → log at DEBUG, return `None` (idempotent: partition already registered, that's fine).
- `UnauthorizedError` → raise `DagsterGatewayError` (in OSS Dagster this means the partition def is missing from the loaded code — a configuration error, not an auth issue).
- `PythonError` → raise `DagsterGatewayError`.
- All httpx network errors, HTTP non-2xx, non-JSON, top-level GraphQL `errors` → raise `DagsterGatewayError` (same pattern as existing methods).
- Unexpected `__typename` → raise `DagsterGatewayError`.

**Method 2: `report_source_materialization(self, partition_key: str, storage_uri: str, size_bytes: int) -> None`**

Module-level string:
```python
_REPORT_SOURCE_MATERIALIZATION_MUTATION = """
mutation ReportSourceMaterialization($params: ReportRunlessAssetEventsParams!) {
  reportRunlessAssetEvents(eventParams: $params) {
    __typename
    ... on ReportRunlessAssetEventsSuccess {
      assetKey {
        path
      }
    }
    ... on UnauthorizedError {
      message
    }
    ... on PythonError {
      message
    }
  }
}
"""
```

Variables:
```python
{
    "params": {
        "eventType": "ASSET_MATERIALIZATION",
        "assetKey": {"path": ["source"]},
        "partitionKeys": [partition_key],
        "description": f"uri={storage_uri} size={size_bytes}",
    }
}
```

Note: no `metadata` field exists in `ReportRunlessAssetEventsParams`; `description` is the only free-text carrier. The `description` format `"uri=... size=..."` is human-readable and sufficient for MVP traceability.

Error handling:
- `ReportRunlessAssetEventsSuccess` → return `None`.
- `UnauthorizedError` → raise `DagsterGatewayError` with message.
- `PythonError` → raise `DagsterGatewayError`.
- All httpx/HTTP/JSON/GraphQL errors → raise `DagsterGatewayError`.

### D-wiring — Upload handler dependency injection

`upload_source` gains one new parameter:

```python
async def upload_source(
    file: UploadFile = File(...),
    collection_id: int | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    s3: Any = Depends(get_s3_client),
    gateway: DagsterGateway = Depends(get_dagster_gateway),
) -> SourceUploadResponse:
```

`routers/sources.py` does not currently import `logging`. The implementer MUST add:

```python
import logging
# ... (at module level, after imports)
logger = logging.getLogger(__name__)
```

The best-effort `logger.warning(...)` calls in the notify block depend on this. Without it the implementation will fail at runtime with a `NameError`.

Tests override gateway via `app.dependency_overrides[get_dagster_gateway] = _mock_gateway_dep`. This is the same pattern as `get_s3_client` overrides in `test_sources_upload.py`.

### D-bindmount-scope — Bind mount goes on all four dagster services

**Decision:** The bind mount `- ../dagster:/app/dagster` is added to ALL FOUR dagster services: `dagster-webserver`, `dagster-daemon`, `dagster-worker-cpu`, `dagster-worker-heavy`.

Rationale: all four services run code from `dagster_platform/` — the daemon and workers use the same `definitions.py` when executing jobs. An inconsistent code state (webserver on new code, workers on old image) would cause split-brain errors when future sprints add real asset jobs. Mounting all four services now is cheap and prevents this class of bug. The `COPY dagster/` in the Dockerfile remains as the production/CI baseline.

---

## §4 Notify Sequence in `upload_source` (after commit)

The full operation sequence of `upload_source` after F-012 is:

```
1.  Validate content-type = "application/pdf" (415 if not).
2.  Read bytes → sha256 → size → original_name.
3.  Build Source ORM with placeholder storage_uri / dagster_partition_key.
4.  session.add(source); await session.flush()  → DB assigns id.
5.  source.storage_uri = f"s3://sources/{source.id}/original.pdf"
6.  source.dagster_partition_key = f"src_{source.id}"
7.  await s3.put_object(...)  → if exception propagates, implicit rollback.
8.  await session.commit()    → row durable; source.id, storage_uri, partition_key all final.

[F-012 notify block — best-effort, after commit]
9.  partition_key = source.dagster_partition_key  (= "src_{id}")
10. try: await gateway.add_source_partition(partition_key)
    except DagsterGatewayError: logger.warning(...) — swallowed.
11. try: await gateway.report_source_materialization(
            partition_key, source.storage_uri, source.size)
    except DagsterGatewayError: logger.warning(...) — swallowed.

12. return SourceUploadResponse(id=source.id, storage_uri=source.storage_uri)
```

Steps 10–11 do NOT affect the HTTP status code (201) or the response body. The handler returns 201 even if both gateway calls fail.

The two gateway calls are always attempted sequentially (10 before 11), not concurrently, to avoid racing on the Dagster event log. If step 10 fails (partition not added), step 11 still fires — the materialization event may still land in Dagster even without the partition being formally registered. This is acceptable for best-effort.

---

## §5 Error / Edge Semantics

### Dagster down (connection refused or timeout)

`gateway.add_source_partition` raises `DagsterGatewayError` (httpx.ConnectError or TimeoutException). Caught in the best-effort block. WARNING logged. Handler returns 201. Upload is not retried or rolled back.

### Dagster mutation returns GraphQL error

The `errors` key in the response body is non-empty → gateway raises `DagsterGatewayError`. Caught and logged. Returns 201.

### Partition already exists (`DuplicateDynamicPartitionError`)

**Idempotency confirmed by introspection:** `addDynamicPartition` returns `DuplicateDynamicPartitionError` (not a hard error, not a 4xx HTTP response) when the partition key is already registered. The gateway method `add_source_partition` catches this specific `__typename` and returns `None` (success-like, log at DEBUG). This means:
- Duplicate uploads of the same source (creating a second row with a different id, hence a different partition key `src_{new_id}`) produce a NEW partition key and are fine.
- If the SAME partition key is somehow submitted twice (should not happen given `src_{unique_id}` naming), it does not error.
- The handler sees no error and returns 201 normally.

### Partition def not found in loaded Dagster code (`UnauthorizedError` message "does not contain a dynamic partitions definition with the given name")

This happens if the `dagster-webserver` is running stale code (before the `definitions.py` change was loaded). The gateway receives `{"__typename": "UnauthorizedError", "message": "The repository does not contain a dynamic partitions definition with the given name."}`. The gateway raises `DagsterGatewayError`. The best-effort handler logs WARNING and returns 201. The upload is not broken; the operator needs to run `docker compose up -d dagster-webserver` (to apply the bind mount) then `docker compose restart dagster-webserver` to reload the updated `definitions.py`. No image rebuild is needed (see §3-U1).

### `reportRunlessAssetEvents` with a partition key not in the partition set

Confirmed working: `reportRunlessAssetEvents` accepts any partition key without requiring it to be in the partition set — it fires the materialization event regardless. Returns `ReportRunlessAssetEventsSuccess`. The event appears in Dagster's event log but may not surface in the UI partition view unless the partition was formally added. This is acceptable for the best-effort path.

### Upload handler failure modes UNCHANGED

Steps 1–8 retain all existing error semantics from F-011:
- S3 upload failure → 500 (implicit rollback, no DB row).
- `session.commit()` failure → 500 (MinIO orphan leak, acceptable).
- Content-type check → 415.
- Missing file → 422.

---

## §6 Test Plan

All F-012 tests live in `apps/api/tests/test_dagster_notify.py`. All are pure unit tests using `TestClient(app)` with `conftest.py` autouse fixtures. No live Dagster or Postgres required.

### Gateway unit tests (testing `add_source_partition` and `report_source_materialization` in isolation)

These tests use `httpx.MockTransport` or `unittest.mock.AsyncMock` to mock `_client.post`, exactly as existing gateway tests do.

| Test name | What it asserts |
|---|---|
| `test_add_source_partition_success` | Mock returns `{"data": {"addDynamicPartition": {"__typename": "AddDynamicPartitionSuccess", "partitionKey": "src_1", "partitionsDefName": "sources"}}}` → method returns `None` without raising. |
| `test_add_source_partition_duplicate_is_noop` | Mock returns `DuplicateDynamicPartitionError` → method returns `None` (no exception). |
| `test_add_source_partition_unauthorized_raises` | Mock returns `UnauthorizedError` → raises `DagsterGatewayError`. |
| `test_add_source_partition_python_error_raises` | Mock returns `PythonError` → raises `DagsterGatewayError`. |
| `test_add_source_partition_connect_error_raises` | `_client.post` raises `httpx.ConnectError` → raises `DagsterGatewayError`. |
| `test_add_source_partition_timeout_raises` | `_client.post` raises `httpx.TimeoutException` → raises `DagsterGatewayError`. |
| `test_add_source_partition_http_error_raises` | `_client.post` raises `httpx.HTTPError` → raises `DagsterGatewayError`. |
| `test_add_source_partition_non_2xx_raises` | Mock returns HTTP 503 → raises `DagsterGatewayError`. |
| `test_add_source_partition_graphql_errors_raises` | Mock returns `{"errors": [{"message": "fail"}]}` → raises `DagsterGatewayError`. |
| `test_report_source_materialization_success` | Mock returns `{"data": {"reportRunlessAssetEvents": {"__typename": "ReportRunlessAssetEventsSuccess", "assetKey": {"path": ["source"]}}}}` → returns `None`. |
| `test_report_source_materialization_unauthorized_raises` | Mock returns `UnauthorizedError` → raises `DagsterGatewayError`. |
| `test_report_source_materialization_python_error_raises` | Mock returns `PythonError` → raises `DagsterGatewayError`. |
| `test_report_source_materialization_connect_error_raises` | `_client.post` raises `httpx.ConnectError` → raises `DagsterGatewayError`. |
| `test_report_source_materialization_payload_shape` | Capture the POST payload; assert `variables.params.eventType == "ASSET_MATERIALIZATION"`, `variables.params.assetKey == {"path": ["source"]}`, `variables.params.partitionKeys == ["src_42"]`. |

### Handler integration tests (testing `upload_source` with mocked gateway)

These build on the existing mock session + mock S3 pattern from `test_sources_upload.py`.

**Mock gateway dependency:**

```python
from dataplat_api.dagster.dependencies import get_dagster_gateway

def _make_gateway_dep(
    partition_raises: Exception | None = None,
    mat_raises: Exception | None = None,
) -> Any:
    """Return a dependency override with a mock DagsterGateway."""
    async def _override(request: Request) -> Any:
        gw = AsyncMock()
        gw.add_source_partition = AsyncMock(
            side_effect=partition_raises if partition_raises else None
        )
        gw.report_source_materialization = AsyncMock(
            side_effect=mat_raises if mat_raises else None
        )
        return gw
    return _override
```

Tests MUST combine the session, S3, and gateway overrides simultaneously.

| Test name | Maps to criterion | What it asserts |
|---|---|---|
| `test_upload_notify_calls_gateway_methods` | V1/V2 | On successful upload, assert `gateway.add_source_partition` called once with `"src_{id}"` and `gateway.report_source_materialization` called once with correct args. |
| `test_upload_notify_partition_key_format` | V1 | Capture call args; assert partition_key matches regex `^src_[0-9]+$`. |
| `test_upload_notify_report_mat_gets_storage_uri` | V2 | `report_source_materialization` is called with `storage_uri == f"s3://sources/{id}/original.pdf"`. |
| `test_upload_returns_201_even_if_add_partition_fails` | D-ordering best-effort | Override `add_source_partition` to raise `DagsterGatewayError`; assert handler still returns 201. |
| `test_upload_returns_201_even_if_report_mat_fails` | D-ordering best-effort | Override `report_source_materialization` to raise `DagsterGatewayError`; assert handler still returns 201. |
| `test_upload_calls_report_mat_even_if_add_partition_fails` | D-ordering best-effort | Even when `add_source_partition` raises, `report_source_materialization` is still called (sequential try/except). |
| `test_upload_notify_called_after_commit` | D-ordering sequence | Using a recording mock, assert `session.commit()` is called BEFORE `gateway.add_source_partition`. |

### Criterion-to-test mapping

| F-012 criterion | Unit test(s) | checks.sh assertion |
|---|---|---|
| V1: dynamic partition appears in Dagster after upload | `test_upload_notify_calls_gateway_methods`, `test_upload_notify_partition_key_format` | `dagster F012-V1`: query `assetNodes(assetKeys:[{path:["source"]}]) { partitionKeys }` after upload; assert partition key present. |
| V2: materialization event in Dagster UI | `test_report_source_materialization_success`, `test_upload_notify_report_mat_gets_storage_uri` | `dagster F012-V2`: query `assetOrError(assetKey:{path:["source"]}) { ... on Asset { assetMaterializations(partitions:["<key>"], limit:10) { partition } } }` — confirmed working (see §7-F012-V2). |
| V3 (OR-branch): source appears in partition list | Same as V1 | Satisfied by V1 check — the partition list includes the key. |

---

## §7 checks.sh Extension

### Where checks go

F-012 checks are appended to the EXISTING `dagster)` case block, after the existing `dagster V2: restart fastapi container` check. They are NOT a new top-level layer. The checks.sh `all)` chain does NOT change.

### Restart step (bind mount — no image rebuild needed)

With the bind mount added to `docker-compose.dev.yml` (§3-U1, §3-D-bindmount-scope), `dagster/dagster_platform/definitions.py` is served live from the host. The checks.sh `dagster)` layer restarts `dagster-webserver` to force Python re-import of the updated module, then waits for healthy:

```bash
echo "--- dagster F012-prerestart: reload dagster-webserver with updated definitions ---"
# The bind mount (added this sprint) means no image rebuild is needed —
# docker compose up -d has already mounted the live dagster/ tree.
# A restart is required because the Dagster webserver does NOT hot-reload Python.
docker compose -f "$COMPOSE" restart dagster-webserver

# Wait for dagster-webserver healthy (max 60s).
DAGSTER_HOST_PORT="${DAGSTER_HOST_PORT:-13000}"
READY=0
for i in $(seq 1 60); do
  STATUS=$(curl -s -o /dev/null -w '%{http_code}' \
    "http://localhost:${DAGSTER_HOST_PORT}/dagster_version")
  [[ "$STATUS" == "200" ]] && { READY=1; break; }
  sleep 1
done
[[ "$READY" == "1" ]] || { echo "FAIL: dagster-webserver did not become healthy after restart"; exit 1; }
echo "  dagster-webserver restarted and healthy"
```

Note for CI/production environments that do not use `docker-compose.dev.yml`: those environments build from the Dockerfile (`COPY dagster/ /app/dagster/`), so a new image build is needed whenever `definitions.py` changes. This is a CI/CD concern, not a local-dev concern.

### F012-V1: Partition key appears in Dagster after upload

After a test upload (reuse the `SRC_ID` from an upload performed in the same check run, or do a fresh upload within the `dagster)` layer), query the asset node's partition keys:

```bash
echo "--- dagster F012-V1: dynamic partition key appears in Dagster ---"
# Do a fresh upload to get a partition key to check.
# (Reuse sources) token pattern.)
F012_TOKEN_BODY=$(mktemp)
F012_TOKEN_STATUS=$(curl -sS -X POST \
  "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
  -d "username=admin@example.com&password=testpassword123" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -w '%{http_code}' -o "$F012_TOKEN_BODY")
test "$F012_TOKEN_STATUS" = "200" \
  || { echo "FAIL: dagster F012-V1 could not mint token"; rm -f "$F012_TOKEN_BODY"; exit 1; }
F012_TOKEN=$(python3 -c "import json; print(json.load(open('$F012_TOKEN_BODY'))['access_token'])")
rm -f "$F012_TOKEN_BODY"

F012_PDF=$(mktemp /tmp/f012-XXXXXX.pdf)
python3 -c "
pdf = (b'%PDF-1.4\n1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n'
       b'2 0 obj<</Type /Pages /Kids[3 0 R] /Count 1>>endobj\n'
       b'3 0 obj<</Type /Page /MediaBox[0 0 612 792] /Parent 2 0 R>>endobj\n'
       b'xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n'
       b'0000000058 00000 n \n0000000115 00000 n \n'
       b'trailer<</Size 4 /Root 1 0 R>>\nstartxref\n182\n%%EOF\n')
open('$F012_PDF', 'wb').write(pdf)
"
F012_UPLOAD_BODY=$(mktemp)
F012_STATUS=$(curl -sS -X POST \
  "http://localhost:${FASTAPI_HOST_PORT}/api/sources/upload" \
  -H "Authorization: Bearer $F012_TOKEN" \
  -F "file=@${F012_PDF};type=application/pdf" \
  -w '%{http_code}' -o "$F012_UPLOAD_BODY")
rm -f "$F012_PDF"
test "$F012_STATUS" = "201" \
  || { echo "FAIL: dagster F012-V1 upload returned $F012_STATUS: $(cat "$F012_UPLOAD_BODY")"; rm -f "$F012_UPLOAD_BODY"; exit 1; }
F012_SRC_ID=$(python3 -c "import json; print(json.load(open('$F012_UPLOAD_BODY'))['id'])")
rm -f "$F012_UPLOAD_BODY"
F012_PARTITION_KEY="src_${F012_SRC_ID}"

echo "  uploaded source id=$F012_SRC_ID, expected partition key=$F012_PARTITION_KEY"

# Query Dagster GraphQL for the source asset's partition keys.
# Uses assetNodes with assetKeys filter and reads partitionKeys field (confirmed in schema).
PARTITION_CHECK=$(docker compose -f "$COMPOSE" exec -T dagster-webserver \
  python3 -c "
import urllib.request, json, sys
url = 'http://localhost:3000/graphql'
query = json.dumps({
    'query': '''query {
        assetNodes(assetKeys: [{path: [\"source\"]}]) {
            assetKey { path }
            isPartitioned
            partitionKeys
        }
    }'''
})
req = urllib.request.Request(url, data=query.encode(), headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req)
data = json.load(resp)
nodes = data['data']['assetNodes']
if not nodes:
    print('FAIL: no assetNodes returned for source', file=sys.stderr)
    sys.exit(1)
node = nodes[0]
keys = node.get('partitionKeys', [])
target = '$F012_PARTITION_KEY'
if target in keys:
    print(f'  F012-V1 OK: partition key {target} found in {len(keys)} keys')
else:
    print(f'FAIL: partition key {target} not found. Keys: {keys}', file=sys.stderr)
    sys.exit(1)
" 2>&1)
echo "$PARTITION_CHECK" | grep -q "FAIL" \
  && { echo "$PARTITION_CHECK"; exit 1; } || echo "$PARTITION_CHECK"
```

### F012-V2: Materialization event in Dagster

**Confirmed working end-to-end** against live Dagster 1.11.16: `assetOrError` exists, returns an `Asset` object, and `assetMaterializations` accepts `partitions: [String]` (plural list, NOT `partitionInLast`). The `MaterializationEvent` type has a `partition: String` field. Confirmed by firing `reportRunlessAssetEvents` for partition `src_probe_99999` then reading it back — `assetMaterializations(partitions: ["src_probe_99999"], limit: 10)` returned `[{"partition": "src_probe_99999", "runId": ""}]`.

```bash
echo "--- dagster F012-V2: materialization event recorded in Dagster ---"
MAT_CHECK=$(docker compose -f "$COMPOSE" exec -T dagster-webserver \
  python3 -c "
import urllib.request, json, sys
url = 'http://localhost:3000/graphql'
# Confirmed query shape (Dagster 1.11.16):
#   assetMaterializations arg is 'partitions' (plural, list), NOT 'partitionInLast'.
#   MaterializationEvent has a 'partition' (singular) field.
query = json.dumps({
    'query': '''query {
        assetOrError(assetKey: {path: [\"source\"]}) {
            __typename
            ... on Asset {
                assetMaterializations(partitions: [\"$F012_PARTITION_KEY\"], limit: 10) {
                    partition
                    runId
                }
            }
            ... on AssetNotFoundError { message }
        }
    }'''
})
req = urllib.request.Request(url, data=query.encode(), headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req)
data = json.load(resp)
result = data['data']['assetOrError']
typename = result.get('__typename')
if typename != 'Asset':
    print(f'FAIL: assetOrError returned {typename}', file=sys.stderr)
    sys.exit(1)
mats = result.get('assetMaterializations', [])
target = '$F012_PARTITION_KEY'
found = any(m.get('partition') == target for m in mats)
if found:
    print(f'  F012-V2 OK: materialization event for partition {target} found')
else:
    print(f'FAIL: no materialization for {target}. Got: {mats}', file=sys.stderr)
    sys.exit(1)
" 2>&1)
echo "$MAT_CHECK" | grep -q "FAIL" \
  && { echo "$MAT_CHECK"; exit 1; } || echo "$MAT_CHECK"
```

### Position in `all)` chain

No change to `all)`. F-012 checks are appended inside the existing `dagster)` case, after the existing `dagster V2` check.

---

## §8 Hard-Invariant & CAL Compliance Notes

### Invariant #1 — Lineage: NOT APPLICABLE

This sprint creates a Dagster external asset notification, not a `Commit` object. No `parents[]`, processor identity, or config hash is required. The `Source` table row already exists (F-011). No lineage columns are added or modified.

### Invariant #2 — Storage separation + CAS: SATISFIED

No new Postgres blob storage. No new MinIO writes. The `source` row was written in F-011. This sprint only adds Dagster notifications after the existing write path.

### Invariant #3 — Schema frozen post-publish: NOT APPLICABLE

No Silver/Gold repo commit.

### Invariant #4 — LLM calls through gateway: NOT APPLICABLE

No LLM calls in this feature.

### Invariant #5 — Async SQLAlchemy: SATISFIED

No new DB operations are added. The existing `upload_source` already uses `await session.flush()` and `await session.commit()` (verified F-011). The two new gateway calls are async (`await gateway.add_source_partition(...)`, `await gateway.report_source_materialization(...)`). No sync DB usage is introduced.

### Invariant #6 — OpenAPI ↔ TS type sync: CONFIRMED NO DRIFT EXPECTED

The upload response schema (`SourceUploadResponse`) is unchanged. No new API route is added. The `upload_source` signature gains a new `DagsterGateway` parameter but this is a FastAPI dependency — it is invisible to the OpenAPI schema. The `openapi.json` diff should be empty.

**Required action:** The implementer MUST still run the regen command and commit the result (even if empty) to prove no drift:

```bash
cd apps/api && uv run python -c \
  'import json; from dataplat_api.main import app; print(json.dumps(app.openapi(), indent=2))' \
  > ../../packages/api-types/openapi.json
git diff packages/api-types/openapi.json
```

If the diff IS empty, commit `packages/api-types/openapi.json` in the same commit as the router change with a message noting "regen confirms no schema drift". If non-empty (unexpected), investigate and fix before committing.

### Boundary invariant — Dagster mutations in gateway only

Both new GraphQL mutations (`addDynamicPartition`, `reportRunlessAssetEvents`) MUST be implemented as methods of `DagsterGateway` in `apps/api/dataplat_api/dagster/gateway.py`. The upload handler MUST NOT import `httpx` or call the Dagster GraphQL endpoint directly. The existing `dagster)` layer check greps for `httpx.(get|post|AsyncClient)` on lines with "dagster" outside `dataplat_api/dagster/`; the `runs)` layer greps for `import httpx` or `from httpx import` outside `dataplat_api/dagster/`. Both checks must still pass.

### CAL notes

- **CAL-3 (schema + regen same commit):** Run codegen; commit even if empty diff.
- **CAL-5 (CAS for processed artifacts):** Not triggered; no artifact storage in this sprint.

---

## §9 Open Questions

| ID | Question | Recommendation |
|---|---|---|
| OQ-1 | Should the two gateway calls be concurrent (`asyncio.gather`) rather than sequential to reduce latency? | Sequential is safer for MVP: if `add_source_partition` fails, attempting `report_source_materialization` separately still gives best-effort. The latency cost is two sequential HTTP calls to Dagster (typically <100ms total on local network). Sequential is the recommendation; reviewer may request `gather`. |
| OQ-2 | DECIDED — see §3-D-bindmount-scope. Bind mount goes on all four dagster services. | N/A |
| OQ-3 | The design doc §5.3 specifies `partition_key = src_<sha256[:12]>` but F-011/F-012 feature_list.json says `src_{source_id}`. The F-011 implementation (already merged) uses `src_{source.id}`. Should the design doc be updated? | The feature_list.json is authoritative for MVP. No action needed in this sprint. The design doc discrepancy is noted; a future design-sync PR can align them. |
| OQ-4 | `reportRunlessAssetEvents` has no `metadata` field — we use `description` for the storage URI. Is `description: "uri=s3://sources/42 size=12345"` sufficient, or should we skip the `description` entirely? | Passing description provides traceability in the Dagster event log. Recommended to include it. |
| OQ-5 | DECIDED — see §7-F012-V2. Confirmed working query: `assetOrError(assetKey:{path:["source"]}) { ... on Asset { assetMaterializations(partitions: ["<key>"], limit: 10) { partition runId } } }`. Arg is `partitions` (plural list), not `partitionInLast`. Verified end-to-end against live Dagster 1.11.16. | N/A |
