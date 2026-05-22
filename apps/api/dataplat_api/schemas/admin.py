"""Pydantic schemas for admin routes — S004-F-004."""

from pydantic import BaseModel


class DagsterStatusResponse(BaseModel):
    """Response body for GET /api/admin/dagster-status.

    When packages/api-types/ codegen is wired (first web-facing sprint),
    this model will automatically produce the corresponding TypeScript type
    via `make codegen` — no changes needed here at that point.
    """

    dagster_version: str
