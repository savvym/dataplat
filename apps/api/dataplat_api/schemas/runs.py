"""Pydantic response schemas for the runs API surface — S005-F-005.

These schemas define the JSON shape of responses from:
  - POST /api/admin/runs/hello-world  → LaunchHelloWorldResponse
  - GET  /api/runs/{run_id}           → RunStatusResponse

The `status` field in RunStatusResponse uses a three-value Literal that maps
Dagster's RunStatus enum per the agreed.md §2.2 mapping table:
  SUCCESS                → "success"
  FAILURE, CANCELED      → "failure"
  all other states       → "running"

Future sprint F-018 will add a `run_id: int` field to LaunchHelloWorldResponse
(the Postgres business run ID). F-005 intentionally omits it because F-005
does not write to the `run` Postgres table.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class LaunchHelloWorldResponse(BaseModel):
    """Response body for POST /api/admin/runs/hello-world (HTTP 201 Created).

    Fields:
        dagster_run_id: The Dagster run UUID assigned by the webserver.
            Matches design doc §4.1 column name `dagster_run_id TEXT UNIQUE NOT NULL`.
    """

    dagster_run_id: str


class RunStatusResponse(BaseModel):
    """Response body for GET /api/runs/{run_id} (HTTP 200 OK).

    Fields:
        dagster_run_id: The Dagster run UUID (echoed from the path parameter).
        status: Three-value internal status derived from Dagster's RunStatus enum.
            "running"  — run is queued or in progress
            "success"  — run completed successfully
            "failure"  — run failed or was canceled
    """

    dagster_run_id: str
    status: Literal["running", "success", "failure"]
