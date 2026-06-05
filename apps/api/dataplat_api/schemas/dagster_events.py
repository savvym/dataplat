"""Pydantic schemas for POST /api/dagster/events — S050-F-050 / S052-F-052.

DagsterRunEventPayload: incoming webhook event from the Dagster run-status sensor
                        (extended in S052 to support ASSET_MATERIALIZATION events).
DagsterEventResponse:   response returned to the sensor for every event.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class DagsterRunEventPayload(BaseModel):
    """Webhook event payload posted by the Dagster sensors.

    Fields
    ------
    event_type:
        One of the four run-lifecycle transitions or ASSET_MATERIALIZATION.
        FastAPI/Pydantic raises HTTP 422 automatically on any other value.
    dagster_run_id:
        The Dagster backfill ID (stored in Run.dagster_run_id).  For backfill
        runs this is context.dagster_run.tags["dagster/backfill"]; for plain
        runs it is context.dagster_run.run_id (which will produce processed=False
        since no matching Run row exists).
    timestamp:
        Event time as reported by the sensor (UTC ISO-8601 string; Pydantic
        parses it to a timezone-aware datetime).  Used to set Run.started_at
        (RUN_START) or Run.ended_at (terminal events).
    asset_key:
        Populated for ASSET_MATERIALIZATION events only.  The Dagster asset key
        string (e.g. "extract_mineru", "chunks").  None for run-status events.
    partition_key:
        Populated for ASSET_MATERIALIZATION events.  The partition key string
        (e.g. "src_42").  None for run-status events.
    metadata:
        Arbitrary metadata dict from asset materialization.  Used by the chunks
        sensor to pass {"chunk_count": N} (real count from Dagster metadata).
        Empty dict for run-status events and extract_mineru events.
    """

    event_type: Literal[
        "RUN_START",
        "RUN_SUCCESS",
        "RUN_FAILURE",
        "RUN_CANCELED",
        "ASSET_MATERIALIZATION",  # NEW — S052-F-052
    ]
    dagster_run_id: str
    timestamp: datetime
    asset_key: str | None = None        # NEW — populated for ASSET_MATERIALIZATION
    partition_key: str | None = None    # NEW — populated for ASSET_MATERIALIZATION
    metadata: dict[str, Any] = {}       # NEW — e.g. {"chunk_count": 42} for chunks

    # Silently drop any additional Dagster fields added in the future —
    # prevents 422 when the sensor payload evolves before this schema does.
    model_config = ConfigDict(extra="ignore")


class DagsterEventResponse(BaseModel):
    """Response body returned for every POST /api/dagster/events call.

    processed=True  → the matching Run row was found and updated (run-status events)
                      or the notification was routed (ASSET_MATERIALIZATION events).
    processed=False → no matching Run row (reason="unknown_run"); the sensor
                      should treat this as a success (no retry needed).
    """

    processed: bool
    reason: str | None = None
