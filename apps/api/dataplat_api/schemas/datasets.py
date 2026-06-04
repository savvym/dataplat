"""Dataset schemas — S042-F-042 + S045-F-045.

Schemas:
  - MaterializeResponse: response body for POST /api/datasets/{recipe_id}/materialize.
  - DatasetListItem: slim response schema for a single dataset in a list context.
  - DatasetListResponse: envelope for GET /api/datasets (F-045).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MaterializeResponse(BaseModel):
    """Response body for POST /api/datasets/{recipe_id}/materialize (F-042).

    ``dataset_id`` is the Postgres-assigned integer primary key of the new Dataset row.
    ``dagster_run_id`` is the Dagster backfillId returned by launchPartitionBackfill.
    Both fields are captured as plain Python locals before the Step 6 commit
    and returned without accessing the ORM object post-expiry (M2 pattern, agreed.md §4).
    """

    dataset_id: int
    dagster_run_id: str


class DatasetListItem(BaseModel):
    """Slim response schema for a single dataset in a list context.

    Used by GET /api/datasets (F-045).  Exposes the 7 fields required by
    F-045's verification[]: id, recipe_id, version_tag, status, sample_count,
    size_bytes, materialized_at.

    Omits detail-level fields (recipe_snapshot, hf_repo_uri, dataset_card_md,
    dagster_run_id, stats, materialized_by) — those are deferred to F-046.

    ``recipe_id`` is nullable to match the DB schema (FK nullable=True).
    In practice every row has a recipe_id set by F-042; frontend/client must
    guard against None before constructing a recipe detail URL.

    ``sample_count``, ``size_bytes``, and ``materialized_at`` are nullable:
    they are None until materialization completes (F-044 sets them on status='done').
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    recipe_id: int | None
    version_tag: str
    status: str
    sample_count: int | None
    size_bytes: int | None
    materialized_at: datetime | None


class DatasetListResponse(BaseModel):
    """Envelope for GET /api/datasets (F-045).

    ``total`` is the count of ALL datasets owned by the caller.  Included for
    forward-compatibility: a future paginated version can return a subset in
    ``items`` while keeping ``total`` accurate, without a breaking schema change.
    """

    items: list[DatasetListItem]
    total: int
