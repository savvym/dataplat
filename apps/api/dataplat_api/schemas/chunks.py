"""Chunk query schemas — S032-F-032 / S033-F-033.

Schemas:
  - ChunkQueryRequest: body for POST /api/chunks/query.
  - ChunkRead: one chunk row, all 24 CHUNKS_SCHEMA fields (all nullable except chunk_id).
  - ChunkQueryResponse: paginated response {items, total}.
  - ChunkAggregateRequest: body for POST /api/chunks/aggregate.
  - ChunkAggregateResponse: grouped statistics response {groups}.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ChunkQueryRequest(BaseModel):
    """Request body for POST /api/chunks/query.

    filter   — DataFusion SQL predicate fragment applied to the Lance chunks
               table (e.g. "source_id = 42", "attr_quality_score > 0.8").
               None / omitted means no filter (return all rows, subject to
               limit/offset).  Max 1000 chars.
    columns  — Optional list of column names to project.  None = all 24 columns.
               Unknown column names cause a 400 (DataFusion parse error).
    limit    — Max rows per page (1–1000, default 100).
    offset   — Row offset for pagination (default 0).
    """

    filter: str | None = Field(default=None, max_length=1000)
    columns: list[str] | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class ChunkRead(BaseModel):
    """One chunk row returned from the Lance chunks table.

    All 24 CHUNKS_SCHEMA fields are present and all nullable except chunk_id.
    Fields not included in a column-projection request will be None.
    """

    # Identifiers
    chunk_id: str
    source_id: int | None = None
    source_collection_id: int | None = None
    producer_asset: str | None = None
    producer_version: str | None = None

    # Content
    text: str | None = None
    token_count: int | None = None
    docling_refs: str | None = None
    source_refs: str | None = None

    # Provenance
    augmented_from: str | None = None
    augmenter_id: str | None = None
    augmenter_config_hash: str | None = None

    # Attribute columns
    attr_quality_score: float | None = None
    attr_quality_provider: str | None = None
    attr_lang_code: str | None = None
    attr_lang_confidence: float | None = None
    attr_minhash_signature: list[int] | None = None
    attr_minhash_cluster_id: int | None = None
    attr_minhash_is_head: bool | None = None
    attr_pii_has_pii: bool | None = None
    attr_pii_categories: list[str] | None = None
    attr_embed_vector: list[float] | None = None

    # Timestamps
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ChunkQueryResponse(BaseModel):
    """Paginated response for POST /api/chunks/query."""

    items: list[ChunkRead]
    total: int


class ChunkAggregateRequest(BaseModel):
    """Request body for POST /api/chunks/aggregate.

    filter   — DataFusion SQL predicate fragment applied to the Lance chunks
               table before grouping (e.g. "source_id = 42").
               None / omitted means group over all rows.  Max 1000 chars.
    group_by — Name of a single Lance column to group by (e.g. "attr_lang_code",
               "producer_asset").  Must be a valid CHUNKS_SCHEMA column name;
               unknown names cause a 400 at PyArrow grouping time.
    metrics  — Non-empty list of metric specifiers (max 20).  Two forms:
                 "count"          — count rows per group (no target column needed)
                 "op:COLNAME"     — apply op ∈ {sum, mean, min, max} to COLNAME
                                    e.g. "sum:attr_quality_score"
               Unknown ops or columns produce HTTP 400.
               NOTE: PyArrow silently upcasts integer columns to float for
               "mean"; "min"/"max" on string columns returns lexicographic order.
    """

    filter: str | None = Field(default=None, max_length=1000)
    group_by: str = Field(..., min_length=1, max_length=128)
    metrics: list[str] = Field(..., min_length=1, max_length=20)


class ChunkAggregateResponse(BaseModel):
    """Response for POST /api/chunks/aggregate.

    groups — one dict per distinct value of group_by.  Each dict contains:
               - the group_by column key/value pair
               - one key per requested metric, named as follows:
                   "count"          metric → key "count"
                   "op:COLNAME"     metric → key "{op}_{colname}"
                                    e.g. "sum:attr_quality_score"
                                         → key "sum_attr_quality_score"
    Null-key groups: if rows have NULL in the group_by column, they form a
    separate group with key value null. The "count" metric correctly counts
    all rows in that group (using PyArrow's count_all).

    Example (group_by="attr_lang_code", metrics=["count"]):
      {"groups": [
        {"attr_lang_code": "zh", "count": 42},
        {"attr_lang_code": "en", "count": 17},
      ]}
    """

    groups: list[dict[str, Any]]
