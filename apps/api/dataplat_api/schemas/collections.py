"""Collection schemas — S008-F-008 stub; body owned by F-010.

CollectionListResponse.items is intentionally list[Any] so F-010 can narrow it to
list[SourceCollectionOut] without a breaking change to the schema field names.
F-010 MUST update the response_model annotation and items type to a proper Pydantic
schema and regenerate packages/api-types/openapi.json in the same commit (CAL-3).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class CollectionListResponse(BaseModel):
    items: list[Any]
    total: int
