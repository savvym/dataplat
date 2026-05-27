"""Pydantic response schemas for the runs API surface — S005-F-005, S018-F-018.

These schemas define the JSON shape of responses from:
  - POST /api/admin/runs/hello-world  → LaunchHelloWorldResponse
  - GET  /api/runs/{run_id}           → RunStatusResponse
  - POST /api/runs                    → RunCreate (request), RunCreateResponse (response)

The `status` field in RunStatusResponse uses a three-value Literal that maps
Dagster's RunStatus enum per the agreed.md §2.2 mapping table:
  SUCCESS                → "success"
  FAILURE, CANCELED      → "failure"
  all other states       → "running"
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


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


class RunCreate(BaseModel):
    """Request body for POST /api/runs (F-018, F-024).

    Fields:
        asset: The asset to trigger. Supported values:
               - "extract_mineru" (F-018): run MinerU PDF extraction.
               - "chunks" (F-024): run chunking on extracted documents.
               - "attr_quality" (F-027): run quality tagger (length-heuristic stub).
               Pydantic v2 raises ValidationError for any other value → FastAPI 422.
        source_ids: Non-empty list of source IDs to process.
                    min_length=1 enforces non-empty at the schema level → FastAPI 422.
    """

    asset: Literal["extract_mineru", "chunks", "attr_quality"]
    source_ids: Annotated[list[int], Field(min_length=1)]

    model_config = ConfigDict(extra="ignore")


class RunCreateResponse(BaseModel):
    """Response body for POST /api/runs (F-018, HTTP 202 Accepted).

    Fields:
        dagster_run_id: The backfillId from LaunchBackfillSuccess.
        run_id: The Postgres run.id assigned at insert.
    """

    dagster_run_id: str
    run_id: int
