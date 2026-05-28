"""Chunks router — S032-F-032 / S033-F-033.

POST /api/chunks/query   — execute a DataFusion SQL filter on the Lance chunks
                           table and return matching chunks with a total count.
POST /api/chunks/aggregate — compute grouped statistics over the Lance chunks
                             table using PyArrow group_by.
"""
from __future__ import annotations  # N2 fix

import asyncio
import logging

import pyarrow as pa
from fastapi import APIRouter, Depends, HTTPException, status

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import User
from dataplat_api.schemas.chunks import (
    ChunkAggregateRequest,
    ChunkAggregateResponse,
    ChunkQueryRequest,
    ChunkQueryResponse,
    ChunkRead,
)
from dataplat_api.storage.lance import get_or_create_chunks_table

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chunks", tags=["chunks"])


class LanceQueryError(Exception):  # N1 fix: Exception, not ValueError
    """Raised when Lance/DataFusion rejects a query; converted to HTTP 400."""


# ── Aggregate helpers ─────────────────────────────────────────────────────────

_VALID_BINARY_OPS = frozenset({"sum", "mean", "min", "max"})


def _parse_metrics(metrics: list[str]) -> list[tuple[str, str | None]]:
    """Return list of (op, column_or_None) tuples.

    Raises LanceQueryError for any malformed or unknown op.
    """
    parsed: list[tuple[str, str | None]] = []
    for m in metrics:
        if m == "count":
            parsed.append(("count_all", None))
        elif ":" in m:
            op, col = m.split(":", 1)
            if op not in _VALID_BINARY_OPS:
                raise LanceQueryError(f"Unknown metric op: {op!r}")
            if not col:
                raise LanceQueryError(f"Metric {m!r}: column name is empty")
            parsed.append((op, col))
        else:
            raise LanceQueryError(f"Invalid metric specifier: {m!r}")
    return parsed


def _build_columns(
    group_by: str,
    parsed_metrics: list[tuple[str, str | None]],
) -> list[str]:
    """Build minimal column set to fetch from Lance."""
    columns: list[str] = [group_by]
    for op, col in parsed_metrics:
        if col is not None and col not in columns:
            columns.append(col)
    return columns


def _aggregate(
    tbl: pa.Table,
    group_by: str,
    parsed_metrics: list[tuple[str, str | None]],
) -> list[dict]:
    """Group tbl by group_by and compute metrics using PyArrow.

    Uses count_all (not count) for row-count-per-group to correctly handle
    null-key groups.
    """
    agg_specs: list[tuple] = []
    rename_map: dict[str, str] = {}  # PyArrow output col → desired output key

    for op, col in parsed_metrics:
        if op == "count_all":
            # count_all counts ALL rows in each group regardless of nullity.
            # PyArrow syntax: ([], "count_all") → output column "count_all"
            agg_specs.append(([], "count_all"))
            rename_map["count_all"] = "count"
        else:
            agg_specs.append((col, op))
            rename_map[f"{col}_{op}"] = f"{op}_{col}"

    result = tbl.group_by(group_by).aggregate(agg_specs)

    # Rename PyArrow-generated column names to user-friendly output names.
    new_names = [rename_map.get(n, n) for n in result.column_names]
    result = result.rename_columns(new_names)

    return result.to_pylist()


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


@router.post("/aggregate", response_model=ChunkAggregateResponse)
async def aggregate_chunks(
    body: ChunkAggregateRequest,
    current_user: User = Depends(get_current_user),
) -> ChunkAggregateResponse:
    """Compute grouped statistics over the Lance chunks table.

    Auth required (F-008).  No per-user row scoping (§11.6 deferred).

    All matching rows (subject to filter) are loaded into process memory for
    grouping — callers should apply a filter to avoid full-table scans on
    large datasets.  Post-MVP: push GROUP BY to DuckDB/DataFusion SQL.
    """
    try:
        parsed_metrics = _parse_metrics(body.metrics)
    except LanceQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lance query error: {exc}",
        ) from exc

    columns_to_fetch = _build_columns(body.group_by, parsed_metrics)

    def _execute() -> list[dict]:
        """Synchronous Lance I/O + PyArrow aggregation, run via asyncio.to_thread()."""
        try:
            table = get_or_create_chunks_table()
            q = table.search()
            if body.filter:
                q = q.where(body.filter)
            q = q.select(columns_to_fetch)
            # No .limit() — we need ALL matching rows for correct GROUP BY.
            arrow_tbl = q.to_arrow()
        except Exception as exc:
            raise LanceQueryError(str(exc)) from exc

        try:
            groups = _aggregate(arrow_tbl, body.group_by, parsed_metrics)
        except Exception as exc:
            raise LanceQueryError(f"Aggregation error: {exc}") from exc

        return groups

    try:
        groups = await asyncio.to_thread(_execute)
    except LanceQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lance query error: {exc}",
        ) from exc

    return ChunkAggregateResponse(groups=groups)
