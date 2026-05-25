"""Source schemas — S011-F-011 / S013-F-013 / S014-F-014.

Schemas:
  - SourceUploadResponse: response for POST /api/sources/upload (F-011).
      Contains only id and storage_uri per agreed.md §3-D8 (minimal response).
  - SourceRead: response for GET /api/sources/{id} (F-013).
      Full source record with all 10 fields.
  - SourceListResponse: response for GET /api/sources/collections/{id}/sources (F-014).
      Paginated list of SourceRead items with total count.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SourceUploadResponse(BaseModel):
    """Response schema for POST /api/sources/upload.

    Minimal per F-011 verification criteria V1:
      {"id": <int>, "storage_uri": "s3://sources/<id>/original.pdf"}

    Additional source fields (sha256, kind, mime_type, collection_id,
    original_name) are available via the F-013 GET detail endpoint.
    """

    id: int
    storage_uri: str


class SourceRead(BaseModel):
    """Response schema for GET /api/sources/{id} (F-013).

    Returns the full source record. Fields omitted (license, source_metadata,
    preferred_extractor) are optional extension columns deferred to a later sprint.
    """

    id: int
    collection_id: int | None
    kind: str
    original_name: str
    storage_uri: str
    sha256: str
    size: int | None
    mime_type: str | None
    dagster_partition_key: str
    uploaded_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class SourceListResponse(BaseModel):
    """Response for GET /api/sources/collections/{id}/sources (F-014).

    Paginated list of source records belonging to a single collection.
    Mirrors CollectionListResponse shape from F-010:
      items — the current page of SourceRead records (ordered by id ASC)
      total — count of ALL sources in the collection (not just the page)
    """

    items: list[SourceRead]
    total: int
