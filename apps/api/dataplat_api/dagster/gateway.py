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
    add_dynamic_partition(...)  -> None             # F-018 (future)
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

    async def aclose(self) -> None:
        """Close the underlying httpx.AsyncClient. Called in lifespan teardown."""
        await self._client.aclose()
