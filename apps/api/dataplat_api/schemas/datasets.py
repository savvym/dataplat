"""Dataset schemas — S042-F-042.

Schemas:
  - MaterializeResponse: response body for POST /api/datasets/{recipe_id}/materialize.
"""

from __future__ import annotations

from pydantic import BaseModel


class MaterializeResponse(BaseModel):
    """Response body for POST /api/datasets/{recipe_id}/materialize (F-042).

    ``dataset_id`` is the Postgres-assigned integer primary key of the new Dataset row.
    ``dagster_run_id`` is the Dagster backfillId returned by launchPartitionBackfill.
    Both fields are captured as plain Python locals before the Step 6 commit
    and returned without accessing the ORM object post-expiry (M2 pattern, agreed.md §4).
    """

    dataset_id: int
    dagster_run_id: str
