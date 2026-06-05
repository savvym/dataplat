"""Pydantic schemas for POST /api/dagster/events — S050-F-050.

DagsterRunEventPayload: incoming webhook event from the Dagster run-status sensor.
DagsterEventResponse:   response returned to the sensor for every event.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DagsterRunEventPayload(BaseModel):
    """Webhook event payload posted by the Dagster run_status_sensor.

    Fields
    ------
    event_type:
        One of the four lifecycle transitions the sensor can fire on.
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
    """

    event_type: Literal["RUN_START", "RUN_SUCCESS", "RUN_FAILURE", "RUN_CANCELED"]
    dagster_run_id: str
    timestamp: datetime

    # Silently drop any additional Dagster fields added in the future —
    # prevents 422 when the sensor payload evolves before this schema does.
    model_config = ConfigDict(extra="ignore")


class DagsterEventResponse(BaseModel):
    """Response body returned for every POST /api/dagster/events call.

    processed=True  → the matching Run row was found and updated.
    processed=False → no matching Run row (reason="unknown_run"); the sensor
                      should treat this as a success (no retry needed).
    """

    processed: bool
    reason: str | None = None
