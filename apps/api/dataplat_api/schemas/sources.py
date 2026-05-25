"""Source schemas — S011-F-011.

Schemas:
  - SourceUploadResponse: response for POST /api/sources/upload (F-011).
      Contains only id and storage_uri per agreed.md §3-D8 (minimal response).
      Full source detail (sha256, kind, mime_type, etc.) will be defined by
      F-013 (GET /api/sources/{id}).
"""

from __future__ import annotations

from pydantic import BaseModel


class SourceUploadResponse(BaseModel):
    """Response schema for POST /api/sources/upload.

    Minimal per F-011 verification criteria V1:
      {"id": <int>, "storage_uri": "s3://sources/<id>/original.pdf"}

    Additional source fields (sha256, kind, mime_type, collection_id,
    original_name) are deferred to the F-013 GET detail endpoint.
    """

    id: int
    storage_uri: str
