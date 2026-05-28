"""Chunks router — S032-F-032.

POST /api/chunks/query — execute a DataFusion SQL filter on the Lance chunks
table and return matching chunks with a total count.
"""
from __future__ import annotations  # N2 fix

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import User
from dataplat_api.schemas.chunks import ChunkQueryRequest, ChunkQueryResponse, ChunkRead
from dataplat_api.storage.lance import get_or_create_chunks_table

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chunks", tags=["chunks"])


class LanceQueryError(Exception):  # N1 fix: Exception, not ValueError
    """Raised when Lance/DataFusion rejects a query; converted to HTTP 400."""


@router.post("/query", response_model=ChunkQueryResponse)
async def query_chunks(
    body: ChunkQueryRequest,
    current_user: User = Depends(get_current_user),
) -> ChunkQueryResponse:
    """Execute a DataFusion SQL filter on the Lance chunks table.

    Auth required (F-008).

    IMPORTANT — no per-user row scoping:
      The handler requires a valid bearer token but does NOT inject owner-scoping
      into the Lance filter.  Callers are responsible for scoping via the filter
      field (e.g. "source_id = 42").  Repository-level ACL on Lance is deferred
      to post-MVP (design doc §11.6).

    Returns:
      items — up to `limit` matching chunk rows (all 24 fields; unselected
              fields are None when `columns` is specified).
      total — count of ALL rows matching the filter (ignores limit/offset).
    """

    def _execute() -> tuple[list[dict], int]:
        """Synchronous Lance I/O, run via asyncio.to_thread()."""
        try:  # B1 fix: get_or_create_chunks_table INSIDE try
            table = get_or_create_chunks_table()
            # Total count (M1 fix: unconditional filter= argument).
            total: int = table.count_rows(filter=body.filter)
            # Paginated data.
            q = table.search()
            if body.filter:
                q = q.where(body.filter)
            if body.columns:
                q = q.select(body.columns)
            q = q.limit(body.limit).offset(body.offset)
            arrow_tbl = q.to_arrow()
        except Exception as exc:
            raise LanceQueryError(str(exc)) from exc
        return arrow_tbl.to_pylist(), total

    try:
        rows, total = await asyncio.to_thread(_execute)
    except LanceQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lance query error: {exc}",
        ) from exc

    items = [ChunkRead(**row) for row in rows]
    return ChunkQueryResponse(items=items, total=total)
