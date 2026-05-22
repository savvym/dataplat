"""DagsterGateway — single chokepoint for all FastAPI → Dagster GraphQL calls.

ENFORCEMENT BOUNDARY (S004-F-004, agreed.md §3.3):
All FastAPI → Dagster GraphQL calls MUST go through
`apps/api/dataplat_api/dagster/gateway.py`.
No other module in `apps/api/` — and no plugin — may import `httpx` to call
Dagster directly.

Use the FastAPI dependency `get_dagster_gateway()` from
`dataplat_api.dagster.dependencies` to receive an instance in route handlers.

Future methods (NOT this sprint — add in the named feature sprint):
    async def launch_run(self, ...) -> str              # F-005
    async def get_run_status(self, run_id: str) -> ...  # F-012
    async def add_dynamic_partition(self, ...) -> None  # F-018
    async def reload_code_location(self, ...) -> None   # F-005
"""

from __future__ import annotations

import httpx


class DagsterGatewayError(Exception):
    """Raised when Dagster is unreachable or returns an unexpected response.

    Never exposed as HTTPException directly — route handlers catch this and
    return JSONResponse(status_code=503, content={"detail": "<message>"}).
    Keeping this as a plain Exception (not HTTPException) preserves the
    gateway's independence from FastAPI internals.
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

    async def aclose(self) -> None:
        """Close the underlying httpx.AsyncClient. Called in lifespan teardown."""
        await self._client.aclose()
