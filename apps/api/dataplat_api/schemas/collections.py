"""Collection schemas — S009-F-009.

Schemas:
  - SourceCollectionCreate: request body for POST /api/sources/collections (F-009)
  - SourceCollectionOut: response for POST /api/sources/collections (F-009);
      will also be used by F-010 for GET /api/sources/collections list items.
  - CollectionListResponse: response for GET /api/sources/collections (F-010 stub).
      items is intentionally list[Any] so F-010 can narrow it to list[SourceCollectionOut]
      without a breaking change to the schema field names. F-010 MUST update the
      response_model annotation and items type and regenerate packages/api-types/openapi.json
      in the same commit (hard invariant #6 / CAL-3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

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

    Used by POST /api/sources/collections (F-009) and will be used by
    GET /api/sources/collections items once F-010 narrows CollectionListResponse.
    """

    id: int
    name: str
    # Nullable at the ORM/DB level (source_collection.owner_id is a nullable FK),
    # but always populated by the POST handler via current_user.id.
    # F-010 / F-011 implementers: a null owner_id is a data-integrity anomaly,
    # not a normal case produced by the POST endpoint.
    owner_id: int | None
    dataset_card_md: str | None
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class CollectionListResponse(BaseModel):
    """Response for GET /api/sources/collections — stub body owned by F-010."""

    items: list[Any]
    total: int
