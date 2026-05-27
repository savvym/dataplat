"""DagsterGateway — single chokepoint for all FastAPI → Dagster GraphQL calls.

ENFORCEMENT BOUNDARY (S004-F-004, agreed.md §3.3):
All FastAPI → Dagster GraphQL calls MUST go through
`apps/api/dataplat_api/dagster/gateway.py`.
No other module in `apps/api/` — and no plugin — may import `httpx` to call
Dagster directly.

Use the FastAPI dependency `get_dagster_gateway()` from
`dataplat_api.dagster.dependencies` to receive an instance in route handlers.

Methods:
    get_dagster_version() -> str                    # F-004
    launch_hello_world() -> str                     # F-005
    get_run_status(run_id: str) -> dict             # F-005
    add_source_partition(partition_key) -> None     # F-012
    report_source_materialization(...) -> None      # F-012
    launch_extract_backfill(partition_keys) -> str  # F-018
    launch_chunks_backfill(partition_keys) -> str   # F-024
    launch_attr_quality_backfill(partition_keys) -> str  # F-027
    reload_code_location(...) -> None               # future
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# Confirmed via introspection against Dagster 1.11.16 (see agreed.md Addendum 1).
_REPOSITORY_LOCATION_NAME = "dagster_platform.definitions"
_REPOSITORY_NAME = "__repository__"

# Confirmed via introspection: both pipelineRunOrError and runOrError exist;
# not-found type is RunNotFoundError (not PipelineRunNotFoundError).
# See agreed.md Addendum 2.
_LAUNCH_HELLO_WORLD_MUTATION = """
mutation LaunchHelloWorld(
  $repositoryLocationName: String!,
  $repositoryName: String!,
  $jobName: String!
) {
  launchRun(
    executionParams: {
      selector: {
        repositoryLocationName: $repositoryLocationName,
        repositoryName: $repositoryName,
        jobName: $jobName
      }
      runConfigData: {}
    }
  ) {
    __typename
    ... on LaunchRunSuccess {
      run {
        runId
      }
    }
    ... on PythonError {
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

# pipelineRunOrError confirmed present in Dagster 1.11.16.
# Not-found type: RunNotFoundError (confirmed via introspection — see agreed.md
# Addendum 2; the candidate §4.2 used PipelineRunNotFoundError which is wrong).
_GET_RUN_STATUS_QUERY = """
query GetRunStatus($runId: ID!) {
  pipelineRunOrError(runId: $runId) {
    __typename
    ... on Run {
      id
      status
    }
    ... on RunNotFoundError {
      message
    }
    ... on PythonError {
      message
    }
  }
}
"""

# F-012: Add a single dynamic partition key to the "sources" partition definition.
# Confirmed mutation name: addDynamicPartition (singular) — NOT addDynamicPartitions.
# Confirmed via introspection against live Dagster 1.11.16 (S012-F-012 agreed.md §3-U2).
# repositorySelector is REQUIRED for addDynamicPartition.
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

# F-012: Report a runless asset materialization for the external "source" asset.
# Confirmed mutation name: reportRunlessAssetEvents — NOT reportRuntimeAssetMaterialization.
# ReportRunlessAssetEventsParams has NO metadata field; use description for traceability.
# Confirmed via introspection against live Dagster 1.11.16 (S012-F-012 agreed.md §3-U2).
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

# F-018: Launch an asset backfill for extract_mineru.
# Confirmed live against Dagster 1.11.16 (S018-F-018 agreed.md §3).
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

# F-024: Launch an asset backfill for chunks. Structurally identical to the
# extract backfill mutation; separated for self-documentation.
_LAUNCH_CHUNKS_BACKFILL_MUTATION = """
mutation LaunchChunksBackfill($backfillParams: LaunchBackfillParams!) {
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

# F-027: Launch an asset backfill for attr_quality. Structurally identical to
# the chunks backfill mutation; only the asset path differs.
_LAUNCH_ATTR_QUALITY_BACKFILL_MUTATION = """
mutation LaunchAttrQualityBackfill($backfillParams: LaunchBackfillParams!) {
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

# Dagster RunStatus → internal 3-value set (per agreed.md §2.2).
_TERMINAL_SUCCESS = {"SUCCESS"}
_TERMINAL_FAILURE = {"FAILURE", "CANCELED"}
# All others map to "running". Unknown future values also map to "running" with a warning.


class DagsterGatewayError(Exception):
    """Raised when Dagster is unreachable or returns an unexpected response.

    Never exposed as HTTPException directly — route handlers catch this and
    return JSONResponse(status_code=503, content={"detail": "<message>"}).
    Keeping this as a plain Exception (not HTTPException) preserves the
    gateway's independence from FastAPI internals.
    """


class DagsterRunNotFoundError(DagsterGatewayError):
    """Raised by get_run_status() when Dagster reports the run does not exist.

    Route handlers catch this BEFORE DagsterGatewayError and return 404.
    This is a subclass of DagsterGatewayError so it is also caught by any
    catch-all DagsterGatewayError handler — but the more specific handler
    must appear first in the except chain (agreed.md §4.2).
    """


class DagsterGateway:
    """Single chokepoint for all FastAPI → Dagster GraphQL communication.

    Instantiated ONCE at application startup in the `lifespan` context manager
    and stored on `app.state.dagster_gateway`. Route handlers receive it via
    `Depends(get_dagster_gateway)` from `dataplat_api.dagster.dependencies`.

    Never instantiate this class inside a route handler — doing so opens a
    new `httpx.AsyncClient` on every request, which is wasteful and defeats
    connection pooling.
    """

    def __init__(
        self,
        graphql_url: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = graphql_url
        self._client = client or httpx.AsyncClient(timeout=10.0)

    async def get_dagster_version(self) -> str:
        """Return the Dagster version string from the GraphQL endpoint.

        GraphQL query: { version }
        Confirmed against running Dagster 1.11.16 instance — returns:
            {"data": {"version": "1.11.16"}}

        Raises DagsterGatewayError for ALL of the following (each annotated
        below with the user-visible HTTP outcome they produce via the route):

        - httpx network error (ConnectError, TimeoutException, etc.)
          → 503 "Dagster unreachable"
        - HTTP response status is not 2xx
          → 503 "Dagster unreachable"
        - Response body is not valid JSON (json.JSONDecodeError)
          → 503 "Dagster unreachable"
        - Top-level "errors" key present and its value is a non-empty list
          (standard GraphQL server-side error; HTTP status may still be 200)
          → 503 "Dagster unreachable"
        - "data" key absent from parsed response
          → 503 "Dagster unreachable"
        - "data"["version"] absent, None, or empty string
          → 503 "Dagster unreachable"
        - Any KeyError / ValueError from response parsing
          → 503 "Dagster unreachable"

        The route handler only catches DagsterGatewayError — NEVER let
        KeyError, ValueError, or httpx exceptions bubble up from this method.
        """
        query = {"query": "{ version }"}
        try:
            response = await self._client.post(self._url, json=query)
        except httpx.TimeoutException as exc:
            # httpx.TimeoutException → 503 "Dagster unreachable"
            raise DagsterGatewayError("Dagster request timed out") from exc
        except httpx.ConnectError as exc:
            # httpx.ConnectError → 503 "Dagster unreachable"
            raise DagsterGatewayError("Cannot connect to Dagster") from exc
        except httpx.HTTPError as exc:
            # httpx.HTTPError (catch-all for other network failures) → 503
            raise DagsterGatewayError(f"HTTP error contacting Dagster: {exc}") from exc

        if not response.is_success:
            # HTTP non-2xx → 503 "Dagster unreachable"
            raise DagsterGatewayError(
                f"Dagster returned HTTP {response.status_code}"
            )

        try:
            body = response.json()
        except Exception as exc:
            # Response body not valid JSON → 503 "Dagster unreachable"
            raise DagsterGatewayError("Dagster response is not valid JSON") from exc

        # GraphQL server-side error: HTTP 200 with {"errors": [...]} body
        # Standard GraphQL behaviour; must be treated as a gateway failure → 503
        errors = body.get("errors")
        if errors:
            msg = errors[0].get("message", "unknown GraphQL error") if isinstance(errors, list) else str(errors)
            raise DagsterGatewayError(f"Dagster GraphQL error: {msg}")

        try:
            version: str = body["data"]["version"]
        except (KeyError, TypeError) as exc:
            # "data" or "data"["version"] absent → 503 "Dagster unreachable"
            raise DagsterGatewayError(
                "Unexpected Dagster GraphQL response shape"
            ) from exc

        if not version:
            # version is None or empty string → 503 "Dagster unreachable"
            raise DagsterGatewayError("Dagster returned an empty version string")

        return version

    async def launch_hello_world(self) -> str:
        """Launch the hello_world_job in Dagster and return the run ID (UUID string).

        Executes the `launchRun` GraphQL mutation against the Dagster webserver.
        The job is queued immediately; status polling is done separately via
        `get_run_status()`.

        Selector values confirmed via introspection (agreed.md Addendum 1):
            repositoryLocationName = "dagster_platform.definitions"
            repositoryName         = "__repository__"
            jobName                = "hello_world_job"

        Returns:
            The Dagster run ID (UUID string, e.g. "550e8400-e29b-41d4-a716-446655440000").

        Raises DagsterGatewayError for ALL of the following:
        - httpx network error (ConnectError, TimeoutException, etc.)
          → 503 from route
        - HTTP response status is not 2xx
          → 503 from route
        - Response body is not valid JSON
          → 503 from route
        - Top-level "errors" key present (GraphQL server-side error)
          → 503 from route
        - launchRun.__typename is not "LaunchRunSuccess" (e.g. PythonError,
          InvalidSubsetError, RunConflict — wrong selector, schema mismatch, etc.)
          → 503 from route
        - run.runId absent or empty
          → 503 from route
        """
        payload = {
            "query": _LAUNCH_HELLO_WORLD_MUTATION,
            "variables": {
                "repositoryLocationName": _REPOSITORY_LOCATION_NAME,
                "repositoryName": _REPOSITORY_NAME,
                "jobName": "hello_world_job",
            },
        }
        try:
            response = await self._client.post(self._url, json=payload)
        except httpx.TimeoutException as exc:
            raise DagsterGatewayError("Dagster request timed out") from exc
        except httpx.ConnectError as exc:
            raise DagsterGatewayError("Cannot connect to Dagster") from exc
        except httpx.HTTPError as exc:
            raise DagsterGatewayError(f"HTTP error contacting Dagster: {exc}") from exc

        if not response.is_success:
            raise DagsterGatewayError(
                f"Dagster returned HTTP {response.status_code}"
            )

        try:
            body = response.json()
        except Exception as exc:
            raise DagsterGatewayError("Dagster response is not valid JSON") from exc

        errors = body.get("errors")
        if errors:
            msg = errors[0].get("message", "unknown GraphQL error") if isinstance(errors, list) else str(errors)
            raise DagsterGatewayError(f"Dagster GraphQL error: {msg}")

        try:
            launch_result = body["data"]["launchRun"]
        except (KeyError, TypeError) as exc:
            raise DagsterGatewayError(
                "Unexpected Dagster launchRun response shape"
            ) from exc

        typename = launch_result.get("__typename")
        if typename != "LaunchRunSuccess":
            msg = launch_result.get("message", f"launchRun returned {typename}")
            raise DagsterGatewayError(f"Dagster launchRun failed: {msg}")

        try:
            run_id: str = launch_result["run"]["runId"]
        except (KeyError, TypeError) as exc:
            raise DagsterGatewayError(
                "launchRun succeeded but runId was absent in response"
            ) from exc

        if not run_id:
            raise DagsterGatewayError("Dagster returned an empty runId")

        return run_id

    async def get_run_status(self, run_id: str) -> dict:  # type: ignore[type-arg]
        """Return the current status of a Dagster run.

        Executes `pipelineRunOrError` (confirmed present in Dagster 1.11.16 —
        see agreed.md Addendum 2). Not-found type is `RunNotFoundError`
        (not `PipelineRunNotFoundError`, which exists in schema but is not
        returned by this field for missing runs — confirmed via introspection).

        Args:
            run_id: The Dagster run ID (UUID string).

        Returns:
            A dict with shape:
                {
                    "dagster_run_id": str,
                    "status": "running" | "success" | "failure"
                }
            The `status` field maps Dagster's RunStatus enum per agreed.md §2.2:
                SUCCESS                                    → "success"
                FAILURE, CANCELED                          → "failure"
                QUEUED, NOT_STARTED, STARTING, STARTED,
                MANAGED, CANCELING, and any unknown value  → "running"

        Raises:
            DagsterRunNotFoundError: when Dagster reports the run does not exist
                (typename == "RunNotFoundError"). Route handler catches this
                BEFORE DagsterGatewayError and returns 404.
            DagsterGatewayError: for all other failures:
                - httpx network errors → 503 from route
                - HTTP non-2xx → 503 from route
                - Non-JSON response → 503 from route
                - GraphQL "errors" key present → 503 from route
                - typename == "PythonError" → 503 from route
                - Unexpected response shape → 503 from route
        """
        payload = {
            "query": _GET_RUN_STATUS_QUERY,
            "variables": {"runId": run_id},
        }
        try:
            response = await self._client.post(self._url, json=payload)
        except httpx.TimeoutException as exc:
            raise DagsterGatewayError("Dagster request timed out") from exc
        except httpx.ConnectError as exc:
            raise DagsterGatewayError("Cannot connect to Dagster") from exc
        except httpx.HTTPError as exc:
            raise DagsterGatewayError(f"HTTP error contacting Dagster: {exc}") from exc

        if not response.is_success:
            raise DagsterGatewayError(
                f"Dagster returned HTTP {response.status_code}"
            )

        try:
            body = response.json()
        except Exception as exc:
            raise DagsterGatewayError("Dagster response is not valid JSON") from exc

        errors = body.get("errors")
        if errors:
            msg = errors[0].get("message", "unknown GraphQL error") if isinstance(errors, list) else str(errors)
            raise DagsterGatewayError(f"Dagster GraphQL error: {msg}")

        try:
            run_result = body["data"]["pipelineRunOrError"]
        except (KeyError, TypeError) as exc:
            raise DagsterGatewayError(
                "Unexpected Dagster pipelineRunOrError response shape"
            ) from exc

        typename = run_result.get("__typename")

        if typename == "RunNotFoundError":
            raise DagsterRunNotFoundError(f"run not found: {run_id}")

        if typename == "PythonError":
            msg = run_result.get("message", "unknown PythonError")
            raise DagsterGatewayError(f"Dagster PythonError: {msg}")

        if typename != "Run":
            raise DagsterGatewayError(
                f"Unexpected pipelineRunOrError typename: {typename}"
            )

        try:
            dagster_status: str = run_result["status"]
        except (KeyError, TypeError) as exc:
            raise DagsterGatewayError(
                "Run response missing status field"
            ) from exc

        # Map Dagster RunStatus → internal 3-value set (agreed.md §2.2).
        if dagster_status in _TERMINAL_SUCCESS:
            mapped_status = "success"
        elif dagster_status in _TERMINAL_FAILURE:
            mapped_status = "failure"
        else:
            # QUEUED, NOT_STARTED, STARTING, STARTED, MANAGED, CANCELING,
            # and any future unknown values → "running"
            if dagster_status not in {
                "QUEUED", "NOT_STARTED", "STARTING", "STARTED", "MANAGED", "CANCELING"
            }:
                logger.warning(
                    "Unknown Dagster RunStatus %r for run %s — treating as 'running'",
                    dagster_status,
                    run_id,
                )
            mapped_status = "running"

        return {"dagster_run_id": run_id, "status": mapped_status}

    async def add_source_partition(self, partition_key: str) -> None:
        """Add a dynamic partition key to the "sources" partition definition.

        Called after a successful source upload to register the partition in
        Dagster so downstream asset jobs can target it.

        Partition key format: "src_{source_id}" (F-012 agreed.md §3-D-gateway).

        Idempotent: if the partition already exists, Dagster returns
        DuplicateDynamicPartitionError — this is treated as a no-op (logged
        at DEBUG, not raised), because the partition is already registered.

        Raises DagsterGatewayError for all failure cases:
        - httpx network errors (ConnectError, TimeoutException, HTTPError)
        - HTTP non-2xx response
        - Non-JSON response body
        - Top-level GraphQL "errors" key present
        - UnauthorizedError typename — in OSS Dagster without auth this means
          the "sources" DynamicPartitionsDefinition is not loaded in the code
          location (dagster-webserver not restarted after definitions.py change)
        - PythonError typename
        - Any unexpected __typename
        """
        payload = {
            "query": _ADD_SOURCE_PARTITION_MUTATION,
            "variables": {
                "partitionKey": partition_key,
                "partitionsDefName": "sources",
                "repositorySelector": {
                    "repositoryLocationName": _REPOSITORY_LOCATION_NAME,
                    "repositoryName": _REPOSITORY_NAME,
                },
            },
        }
        try:
            response = await self._client.post(self._url, json=payload)
        except httpx.TimeoutException as exc:
            raise DagsterGatewayError("Dagster request timed out") from exc
        except httpx.ConnectError as exc:
            raise DagsterGatewayError("Cannot connect to Dagster") from exc
        except httpx.HTTPError as exc:
            raise DagsterGatewayError(f"HTTP error contacting Dagster: {exc}") from exc

        if not response.is_success:
            raise DagsterGatewayError(
                f"Dagster returned HTTP {response.status_code}"
            )

        try:
            body = response.json()
        except Exception as exc:
            raise DagsterGatewayError("Dagster response is not valid JSON") from exc

        errors = body.get("errors")
        if errors:
            msg = errors[0].get("message", "unknown GraphQL error") if isinstance(errors, list) else str(errors)
            raise DagsterGatewayError(f"Dagster GraphQL error: {msg}")

        try:
            result = body["data"]["addDynamicPartition"]
        except (KeyError, TypeError) as exc:
            raise DagsterGatewayError(
                "Unexpected Dagster addDynamicPartition response shape"
            ) from exc

        typename = result.get("__typename")

        if typename == "AddDynamicPartitionSuccess":
            return None

        if typename == "DuplicateDynamicPartitionError":
            # Partition already exists — idempotent no-op.
            logger.debug(
                "add_source_partition: partition %r already exists in 'sources' — no-op",
                partition_key,
            )
            return None

        if typename == "UnauthorizedError":
            # In OSS Dagster this indicates the partition def is missing from the
            # loaded code location (not an auth error). Treat as gateway failure.
            msg = result.get("message", "UnauthorizedError from addDynamicPartition")
            raise DagsterGatewayError(
                f"Dagster addDynamicPartition UnauthorizedError: {msg}"
            )

        if typename == "PythonError":
            msg = result.get("message", "unknown PythonError")
            raise DagsterGatewayError(f"Dagster addDynamicPartition PythonError: {msg}")

        raise DagsterGatewayError(
            f"Unexpected addDynamicPartition typename: {typename}"
        )

    async def report_source_materialization(
        self,
        partition_key: str,
        storage_uri: str,
        size_bytes: int,
    ) -> None:
        """Report a runless asset materialization event for the external 'source' asset.

        Records that the partition was materialised (i.e., the PDF was uploaded)
        so Dagster's asset lineage graph has a node for this source. This uses
        reportRunlessAssetEvents — the correct Dagster 1.11.16 mutation
        (NOT reportRuntimeAssetMaterialization, which does not exist).

        The description field carries "uri=<storage_uri> size=<size_bytes>" for
        traceability in the Dagster event log (no metadata field available in
        ReportRunlessAssetEventsParams in Dagster 1.11.16 — confirmed via
        introspection, S012-F-012 agreed.md §3-U2).

        Raises DagsterGatewayError for all failure cases (same pattern as
        add_source_partition above).
        """
        payload = {
            "query": _REPORT_SOURCE_MATERIALIZATION_MUTATION,
            "variables": {
                "params": {
                    "eventType": "ASSET_MATERIALIZATION",
                    "assetKey": {"path": ["source"]},
                    "partitionKeys": [partition_key],
                    "description": f"uri={storage_uri} size={size_bytes}",
                }
            },
        }
        try:
            response = await self._client.post(self._url, json=payload)
        except httpx.TimeoutException as exc:
            raise DagsterGatewayError("Dagster request timed out") from exc
        except httpx.ConnectError as exc:
            raise DagsterGatewayError("Cannot connect to Dagster") from exc
        except httpx.HTTPError as exc:
            raise DagsterGatewayError(f"HTTP error contacting Dagster: {exc}") from exc

        if not response.is_success:
            raise DagsterGatewayError(
                f"Dagster returned HTTP {response.status_code}"
            )

        try:
            body = response.json()
        except Exception as exc:
            raise DagsterGatewayError("Dagster response is not valid JSON") from exc

        errors = body.get("errors")
        if errors:
            msg = errors[0].get("message", "unknown GraphQL error") if isinstance(errors, list) else str(errors)
            raise DagsterGatewayError(f"Dagster GraphQL error: {msg}")

        try:
            result = body["data"]["reportRunlessAssetEvents"]
        except (KeyError, TypeError) as exc:
            raise DagsterGatewayError(
                "Unexpected Dagster reportRunlessAssetEvents response shape"
            ) from exc

        typename = result.get("__typename")

        if typename == "ReportRunlessAssetEventsSuccess":
            return None

        if typename == "UnauthorizedError":
            msg = result.get("message", "UnauthorizedError from reportRunlessAssetEvents")
            raise DagsterGatewayError(
                f"Dagster reportRunlessAssetEvents UnauthorizedError: {msg}"
            )

        if typename == "PythonError":
            msg = result.get("message", "unknown PythonError")
            raise DagsterGatewayError(
                f"Dagster reportRunlessAssetEvents PythonError: {msg}"
            )

        raise DagsterGatewayError(
            f"Unexpected reportRunlessAssetEvents typename: {typename}"
        )

    async def launch_extract_backfill(self, partition_keys: list[str]) -> str:
        """Launch an asset backfill for extract_mineru over the given partition keys.

        Executes the `launchPartitionBackfill` GraphQL mutation. The backfill
        enqueues one per-partition Dagster run per key in `partition_keys`.

        Args:
            partition_keys: List of partition keys in "src_{id}" format.

        Returns:
            The backfillId (string) from LaunchBackfillSuccess.

        Raises DagsterGatewayError for ALL of the following:
        - httpx network error (ConnectError, TimeoutException, etc.) → 503 from route
        - HTTP response status is not 2xx → 503 from route
        - Response body is not valid JSON → 503 from route
        - Top-level "errors" key present (GraphQL server-side error) → 503 from route
        - __typename not "LaunchBackfillSuccess" (e.g. PythonError, UnauthorizedError,
          PartitionSetNotFoundError, PartitionKeysNotFoundError, InvalidSubsetError,
          RunConflict) → 503 from route
        - backfillId absent or empty → 503 from route
        """
        payload = {
            "query": _LAUNCH_EXTRACT_BACKFILL_MUTATION,
            "variables": {
                "backfillParams": {
                    "assetSelection": [{"path": ["extract_mineru"]}],
                    "partitionNames": partition_keys,
                    "title": "F-018 extract_mineru",
                }
            },
        }
        try:
            response = await self._client.post(self._url, json=payload)
        except httpx.TimeoutException as exc:
            raise DagsterGatewayError("Dagster request timed out") from exc
        except httpx.ConnectError as exc:
            raise DagsterGatewayError("Cannot connect to Dagster") from exc
        except httpx.HTTPError as exc:
            raise DagsterGatewayError(f"HTTP error contacting Dagster: {exc}") from exc

        if not response.is_success:
            raise DagsterGatewayError(
                f"Dagster returned HTTP {response.status_code}"
            )

        try:
            body = response.json()
        except Exception as exc:
            raise DagsterGatewayError("Dagster response is not valid JSON") from exc

        errors = body.get("errors")
        if errors:
            msg = errors[0].get("message", "unknown GraphQL error") if isinstance(errors, list) else str(errors)
            raise DagsterGatewayError(f"Dagster GraphQL error: {msg}")

        try:
            backfill_result = body["data"]["launchPartitionBackfill"]
        except (KeyError, TypeError) as exc:
            raise DagsterGatewayError(
                "Unexpected Dagster launchPartitionBackfill response shape"
            ) from exc

        typename = backfill_result.get("__typename")
        if typename != "LaunchBackfillSuccess":
            msg = backfill_result.get("message", f"launchPartitionBackfill returned {typename}")
            raise DagsterGatewayError(f"Dagster launchPartitionBackfill failed: {msg}")

        try:
            backfill_id: str = backfill_result["backfillId"]
        except (KeyError, TypeError) as exc:
            raise DagsterGatewayError(
                "launchPartitionBackfill succeeded but backfillId was absent in response"
            ) from exc

        if not backfill_id:
            raise DagsterGatewayError("Dagster returned an empty backfillId")

        return backfill_id

    async def launch_chunks_backfill(self, partition_keys: list[str]) -> str:
        """Launch an asset backfill for chunks over the given partition keys (F-024).

        Executes the `launchPartitionBackfill` GraphQL mutation. The backfill
        enqueues one per-partition Dagster run per key in `partition_keys`.

        Args:
            partition_keys: List of partition keys in "src_{id}" format.

        Returns:
            The backfillId (string) from LaunchBackfillSuccess.

        Raises DagsterGatewayError for ALL of the following:
        - httpx network error (ConnectError, TimeoutException, etc.) → 503 from route
        - HTTP response status is not 2xx → 503 from route
        - Response body is not valid JSON → 503 from route
        - Top-level "errors" key present (GraphQL server-side error) → 503 from route
        - __typename not "LaunchBackfillSuccess" (e.g. PythonError, UnauthorizedError,
          PartitionSetNotFoundError, PartitionKeysNotFoundError, InvalidSubsetError,
          RunConflict) → 503 from route
        - backfillId absent or empty → 503 from route
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
        try:
            response = await self._client.post(self._url, json=payload)
        except httpx.TimeoutException as exc:
            raise DagsterGatewayError("Dagster request timed out") from exc
        except httpx.ConnectError as exc:
            raise DagsterGatewayError("Cannot connect to Dagster") from exc
        except httpx.HTTPError as exc:
            raise DagsterGatewayError(f"HTTP error contacting Dagster: {exc}") from exc

        if not response.is_success:
            raise DagsterGatewayError(
                f"Dagster returned HTTP {response.status_code}"
            )

        try:
            body = response.json()
        except Exception as exc:
            raise DagsterGatewayError("Dagster response is not valid JSON") from exc

        errors = body.get("errors")
        if errors:
            msg = errors[0].get("message", "unknown GraphQL error") if isinstance(errors, list) else str(errors)
            raise DagsterGatewayError(f"Dagster GraphQL error: {msg}")

        try:
            backfill_result = body["data"]["launchPartitionBackfill"]
        except (KeyError, TypeError) as exc:
            raise DagsterGatewayError(
                "Unexpected Dagster launchPartitionBackfill response shape"
            ) from exc

        typename = backfill_result.get("__typename")
        if typename != "LaunchBackfillSuccess":
            msg = backfill_result.get("message", f"launchPartitionBackfill returned {typename}")
            raise DagsterGatewayError(f"Dagster launchPartitionBackfill failed: {msg}")

        try:
            backfill_id: str = backfill_result["backfillId"]
        except (KeyError, TypeError) as exc:
            raise DagsterGatewayError(
                "launchPartitionBackfill succeeded but backfillId was absent in response"
            ) from exc

        if not backfill_id:
            raise DagsterGatewayError("Dagster returned an empty backfillId")

        return backfill_id

    async def launch_attr_quality_backfill(self, partition_keys: list[str]) -> str:
        """Launch an asset backfill for attr_quality over the given partition keys (F-027).

        Executes the `launchPartitionBackfill` GraphQL mutation. The backfill
        enqueues one per-partition Dagster run per key in `partition_keys`.
        The attr_quality asset performs a column-mode update on existing
        producer_asset='chunks' rows — no new rows created.

        Args:
            partition_keys: List of partition keys in "src_{id}" format.

        Returns:
            The backfillId (string) from LaunchBackfillSuccess.

        Raises DagsterGatewayError for ALL of the following:
        - httpx network error (ConnectError, TimeoutException, etc.) → 503 from route
        - HTTP response status is not 2xx → 503 from route
        - Response body is not valid JSON → 503 from route
        - Top-level "errors" key present (GraphQL server-side error) → 503 from route
        - __typename not "LaunchBackfillSuccess" (e.g. PythonError, UnauthorizedError,
          PartitionSetNotFoundError, PartitionKeysNotFoundError, InvalidSubsetError,
          RunConflict) → 503 from route
        - backfillId absent or empty → 503 from route
        """
        payload = {
            "query": _LAUNCH_ATTR_QUALITY_BACKFILL_MUTATION,
            "variables": {
                "backfillParams": {
                    "assetSelection": [{"path": ["attr_quality"]}],
                    "partitionNames": partition_keys,
                    "title": "F-027 attr_quality",
                }
            },
        }
        try:
            response = await self._client.post(self._url, json=payload)
        except httpx.TimeoutException as exc:
            raise DagsterGatewayError("Dagster request timed out") from exc
        except httpx.ConnectError as exc:
            raise DagsterGatewayError("Cannot connect to Dagster") from exc
        except httpx.HTTPError as exc:
            raise DagsterGatewayError(f"HTTP error contacting Dagster: {exc}") from exc

        if not response.is_success:
            raise DagsterGatewayError(
                f"Dagster returned HTTP {response.status_code}"
            )

        try:
            body = response.json()
        except Exception as exc:
            raise DagsterGatewayError("Dagster response is not valid JSON") from exc

        errors = body.get("errors")
        if errors:
            msg = errors[0].get("message", "unknown GraphQL error") if isinstance(errors, list) else str(errors)
            raise DagsterGatewayError(f"Dagster GraphQL error: {msg}")

        try:
            backfill_result = body["data"]["launchPartitionBackfill"]
        except (KeyError, TypeError) as exc:
            raise DagsterGatewayError(
                "Unexpected Dagster launchPartitionBackfill response shape"
            ) from exc

        typename = backfill_result.get("__typename")
        if typename != "LaunchBackfillSuccess":
            msg = backfill_result.get("message", f"launchPartitionBackfill returned {typename}")
            raise DagsterGatewayError(f"Dagster launchPartitionBackfill failed: {msg}")

        try:
            backfill_id: str = backfill_result["backfillId"]
        except (KeyError, TypeError) as exc:
            raise DagsterGatewayError(
                "launchPartitionBackfill succeeded but backfillId was absent in response"
            ) from exc

        if not backfill_id:
            raise DagsterGatewayError("Dagster returned an empty backfillId")

        return backfill_id

    async def aclose(self) -> None:
        """Close the underlying httpx.AsyncClient. Called in lifespan teardown."""
        await self._client.aclose()
