"""Collection schemas — S009-F-009 / S010-F-010.

Schemas:
  - SourceCollectionCreate: request body for POST /api/sources/collections (F-009)
  - SourceCollectionOut: response for POST /api/sources/collections (F-009);
      also used by F-010 for GET /api/sources/collections list items.
  - CollectionListResponse: response for GET /api/sources/collections (F-010).
      items narrowed from list[Any] to list[SourceCollectionOut] in F-010
      (hard invariant #6 / CAL-3 — openapi.json regenerated in same commit).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints


# Validated name type: strip whitespace, require 1–255 chars after stripping.
# Using Annotated + StringConstraints because Field() does not accept
# strip_whitespace in pydantic v2.
CollectionName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]


class SourceCollectionCreate(BaseModel):
    """Request body for POST /api/sources/collections."""

    name: CollectionName
    dataset_card_md: str | None = None

    model_config = ConfigDict(extra="ignore")


class SourceCollectionOut(BaseModel):
    """Response schema for a single source_collection row.

    Used by POST /api/sources/collections (F-009) and by
    GET /api/sources/collections list items (F-010).
    """

    id: int
    name: str
    # Nullable at the ORM/DB level (source_collection.owner_id is a nullable FK),
    # but always populated by the POST handler via current_user.id.
    # A null owner_id is a data-integrity anomaly, not a normal case.
    owner_id: int | None
    dataset_card_md: str | None
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class CollectionListResponse(BaseModel):
    """Response for GET /api/sources/collections (F-010)."""

    items: list[SourceCollectionOut]
    total: int
