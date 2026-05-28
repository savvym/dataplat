"""Chunks router — S032-F-032 / S033-F-033 / S034-F-034.

POST /api/chunks/query   — execute a DataFusion SQL filter on the Lance chunks
                           table and return matching chunks with a total count.
POST /api/chunks/aggregate — compute grouped statistics over the Lance chunks
                             table using PyArrow group_by.
POST /api/chunks/distribution — compute a value distribution histogram for one
                                column (numeric or categorical), auto-detected
                                from the PyArrow schema.
"""
from __future__ import annotations  # N2 fix

import asyncio
import logging
from typing import Literal

import numpy as np
import pyarrow as pa
from fastapi import APIRouter, Depends, HTTPException, Path as FPath, status

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import User
from dataplat_api.schemas.chunks import (
    ChunkAggregateRequest,
    ChunkAggregateResponse,
    ChunkDistributionRequest,
    ChunkDistributionResponse,
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


# ── Distribution helpers ───────────────────────────────────────────────────────


def _compute_numeric_distribution(
    col_array: pa.ChunkedArray,
    bins: int,
) -> list[dict]:
    """Compute equal-width histogram buckets for a numeric column.

    Returns [] if the column contains no non-null values.
    Returns a single bucket [val, val] if all values are identical.
    Null values are excluded before binning.
    """
    valid = col_array.drop_null()
    if len(valid) == 0:
        return []

    values = np.array(valid.to_pylist(), dtype=np.float64)
    col_min = float(values.min())
    col_max = float(values.max())

    # numpy.histogram raises ValueError when range=(v, v); handle separately.
    if col_min == col_max:
        return [{"range": [col_min, col_max], "count": int(len(values))}]

    counts, edges = np.histogram(values, bins=bins)
    return [
        {"range": [float(edges[i]), float(edges[i + 1])], "count": int(counts[i])}
        for i in range(len(counts))
    ]


def _compute_categorical_distribution(
    tbl: pa.Table,
    column: str,
) -> list[dict]:
    """Compute value counts for a categorical (string) column.

    Returns [] if tbl has 0 rows.
    Null values form their own bucket: {"value": null, "count": N}.
    Results are sorted by count descending.
    """
    if len(tbl) == 0:
        return []

    result = tbl.group_by(column).aggregate([([], "count_all")])
    # Rename PyArrow output column "count_all" → "count".
    new_names = ["count" if n == "count_all" else n for n in result.column_names]
    result = result.rename_columns(new_names)
    result = result.sort_by([("count", "descending")])

    rows = result.to_pylist()
    return [{"value": row[column], "count": row["count"]} for row in rows]


@router.post("/distribution", response_model=ChunkDistributionResponse)
async def distribution_chunks(
    body: ChunkDistributionRequest,
    current_user: User = Depends(get_current_user),
) -> ChunkDistributionResponse:
    """Compute a value distribution histogram for one column.

    Auth required (F-008).  No per-user row scoping (§11.6 deferred).

    Column type is auto-detected from the PyArrow schema:
      - Floating-point / integer → numeric equal-width histogram (bins buckets).
      - String (utf8/large_utf8) → categorical value counts, count descending.
      - Other types (bool, list, timestamp, …) → HTTP 400.

    All matching rows for the target column are loaded into process memory —
    callers should apply a filter to avoid full-table scans on large datasets.
    Post-MVP: push aggregation to DuckDB/DataFusion SQL.
    """

    def _execute() -> tuple[Literal["numeric", "categorical"], list[dict]]:
        """Synchronous Lance I/O + distribution computation, via asyncio.to_thread()."""
        try:
            table = get_or_create_chunks_table()
            q = table.search()
            if body.filter:
                q = q.where(body.filter)
            q = q.select([body.column])
            # No .limit() — all matching rows are needed for the histogram.
            arrow_tbl = q.to_arrow()
        except Exception as exc:
            raise LanceQueryError(str(exc)) from exc

        try:
            col_type = arrow_tbl.schema.field(body.column).type
            dist_type: Literal["numeric", "categorical"]
            if pa.types.is_floating(col_type) or pa.types.is_integer(col_type):
                dist_type = "numeric"
                buckets = _compute_numeric_distribution(
                    arrow_tbl.column(body.column), body.bins
                )
            elif pa.types.is_string(col_type) or pa.types.is_large_string(col_type):
                dist_type = "categorical"
                buckets = _compute_categorical_distribution(arrow_tbl, body.column)
            else:
                raise LanceQueryError(
                    f"Column {body.column!r} has unsupported type {col_type!r}; "
                    f"supported: floating-point, integer, string"
                )
        except LanceQueryError:
            raise  # re-raise without wrapping
        except Exception as exc:
            raise LanceQueryError(f"Distribution error: {exc}") from exc

        return dist_type, buckets

    try:
        dist_type, buckets = await asyncio.to_thread(_execute)
    except LanceQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lance query error: {exc}",
        ) from exc

    return ChunkDistributionResponse(
        column=body.column,
        type=dist_type,
        buckets=buckets,
    )


@router.get("/{chunk_id}", response_model=ChunkRead)
async def get_chunk_by_id(
    chunk_id: str = FPath(..., max_length=256),
    current_user: User = Depends(get_current_user),
) -> ChunkRead:
    """Fetch a single chunk by its chunk_id.

    Auth required (F-008).  No per-user row scoping (§11.6 deferred).

    Returns HTTP 404 if no chunk with the given chunk_id exists in the Lance
    table.  Returns HTTP 400 if Lance/DataFusion raises any error.
    """

    def _execute() -> dict | None:
        try:
            table = get_or_create_chunks_table()
            safe_id = chunk_id.replace("'", "''")
            arrow_tbl = (
                table.search()
                .where(f"chunk_id = '{safe_id}'")
                .limit(1)
                .to_arrow()
            )
            rows = arrow_tbl.to_pylist()
            return rows[0] if rows else None
        except Exception as exc:
            raise LanceQueryError(str(exc)) from exc

    try:
        row = await asyncio.to_thread(_execute)
    except LanceQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lance query error: {exc}",
        ) from exc

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Chunk {chunk_id!r} not found",
        )
    return ChunkRead(**row)
