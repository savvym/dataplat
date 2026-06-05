"""Pydantic response schemas for the runs API surface — S005-F-005, S018-F-018, S048-F-048, S049-F-049.

These schemas define the JSON shape of responses from:
  - POST /api/admin/runs/hello-world       → LaunchHelloWorldResponse
  - GET  /api/runs/dagster/{dagster_run_id} → RunStatusResponse
  - POST /api/runs                         → RunCreate (request), RunCreateResponse (response)
  - GET  /api/runs                         → RunListResponse (list of RunListItem)
  - GET  /api/runs/{id}                    → RunDetailResponse

The `status` field in RunStatusResponse uses a three-value Literal that maps
Dagster's RunStatus enum per the agreed.md §2.2 mapping table:
  SUCCESS                → "success"
  FAILURE, CANCELED      → "failure"
  all other states       → "running"
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class LaunchHelloWorldResponse(BaseModel):
    """Response body for POST /api/admin/runs/hello-world (HTTP 201 Created).

    Fields:
        dagster_run_id: The Dagster run UUID assigned by the webserver.
            Matches design doc §4.1 column name `dagster_run_id TEXT UNIQUE NOT NULL`.
    """

    dagster_run_id: str


class RunStatusResponse(BaseModel):
    """Response body for GET /api/runs/dagster/{dagster_run_id} (HTTP 200 OK).

    Fields:
        dagster_run_id: The Dagster run UUID (echoed from the path parameter).
        status: Three-value internal status derived from Dagster's RunStatus enum.
            "running"  — run is queued or in progress
            "success"  — run completed successfully
            "failure"  — run failed or was canceled
    """

    dagster_run_id: str
    status: Literal["running", "success", "failure"]


class RunCreate(BaseModel):
    """Request body for POST /api/runs (F-018, F-024).

    Fields:
        asset: The asset to trigger. Supported values:
               - "extract_mineru" (F-018): run MinerU PDF extraction.
               - "chunks" (F-024): run chunking on extracted documents.
               - "attr_quality" (F-027): run quality tagger (length-heuristic stub).
               - "attr_lang" (F-029): run lang_fasttext tagger.
               - "attr_minhash" (F-030): run minhash dedup tagger.
               Pydantic v2 raises ValidationError for any other value → FastAPI 422.
        source_ids: Non-empty list of source IDs to process.
                    min_length=1 enforces non-empty at the schema level → FastAPI 422.
    """

    asset: Literal[
        "extract_mineru", "chunks", "attr_quality", "attr_lang", "attr_minhash"
    ]
    source_ids: Annotated[list[int], Field(min_length=1)]

    model_config = ConfigDict(extra="ignore")


class RunCreateResponse(BaseModel):
    """Response body for POST /api/runs (F-018, HTTP 202 Accepted).

    Fields:
        dagster_run_id: The backfillId from LaunchBackfillSuccess.
        run_id: The Postgres run.id assigned at insert.
    """

    dagster_run_id: str
    run_id: int


class RunDetailResponse(BaseModel):
    """Full run record for GET /api/runs/{id} (F-048).

    Exposes all 14 ORM-mapped columns of the ``run`` table.
    ``dagster_run_id`` is the Dagster backfill UUID (TEXT UNIQUE NOT NULL).
    ``kind`` is the run type string set by the trigger handler.
    ``config`` is a nullable JSONB dict; currently None for all trigger paths.
    ``started_at`` / ``ended_at`` are nullable datetimes (None until state
    transitions fire in Dagster sensor callbacks, if any).
    ``triggered_by`` is the owner FK; doubles as the owner-scope filter.
    ``trigger_context`` is nullable JSONB; currently None for all trigger paths.
    ``asset_keys`` / ``partition_keys`` are Postgres ARRAY(Text) columns.
    """

    model_config = ConfigDict(from_attributes=True)

    # ── Identity ──────────────────────────────────────────────────────────────
    id: int  # Run.id              BigInteger PK
    dagster_run_id: str  # Run.dagster_run_id  Text NOT NULL UNIQUE

    # ── Run classification ────────────────────────────────────────────────────
    kind: str  # Run.kind            Text NOT NULL
    asset_keys: list[str]  # Run.asset_keys      ARRAY(Text) NOT NULL
    partition_keys: list[str] | None  # Run.partition_keys  ARRAY(Text) nullable

    # ── FK context ────────────────────────────────────────────────────────────
    source_collection_id: int | None  # Run.source_collection_id FK nullable
    dataset_id: int | None  # Run.dataset_id           FK nullable
    recipe_id: int | None  # Run.recipe_id            FK nullable

    # ── Configuration ─────────────────────────────────────────────────────────
    config: dict | None  # Run.config          JSONB nullable

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    status: str  # Run.status          Text NOT NULL
    started_at: datetime | None  # Run.started_at      DateTime tz nullable
    ended_at: datetime | None  # Run.ended_at        DateTime tz nullable
    triggered_by: int | None  # Run.triggered_by    FK → users.id nullable
    trigger_context: dict | None  # Run.trigger_context JSONB nullable


class RunListItem(BaseModel):
    """Slim run record for GET /api/runs list endpoint (F-049).

    Exposes 10 of the 14 ORM-mapped columns — enough to render a run list and
    navigate to detail.  The 4 excluded columns (``asset_keys``,
    ``partition_keys``, ``config``, ``trigger_context``) are ARRAY or JSONB
    blobs that are bulky and detail-level only; they are available via
    ``GET /api/runs/{id}`` (RunDetailResponse).

    Fields:
        id:                   BigInteger PK — required for item-level navigation.
        dagster_run_id:       TEXT UNIQUE NOT NULL — used to poll
                              GET /dagster/{dagster_run_id} for live status.
        kind:                 TEXT NOT NULL — e.g. "extract", "chunk",
                              "attr_quality", "attr_lang", "attr_minhash".
        status:               TEXT NOT NULL — e.g. "pending", "running",
                              "success", "failure".
        started_at:           Null for ``status='pending'`` rows (set by
                              Dagster sensor on run start).
        ended_at:             Null until run completes.
        triggered_by:         FK → users.id; the owner; nullable per ORM but
                              always populated for application-created rows.
        dataset_id:           FK → dataset.id; null for most run types.
        recipe_id:            FK → recipe.id; null for extract/attr runs.
        source_collection_id: FK → source_collection.id; null unless run
                              was triggered over a collection.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    dagster_run_id: str
    kind: str
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    triggered_by: int | None
    dataset_id: int | None
    recipe_id: int | None
    source_collection_id: int | None


class RunListResponse(BaseModel):
    """Paginated envelope for GET /api/runs (F-049).

    Fields:
        items: Ordered ``started_at DESC NULLS LAST, id DESC`` list of
               RunListItem records owned by the authenticated caller.
        total: Owner-scoped COUNT (and optionally status-filtered).
               Equals ``len(items)`` for unpaginated MVP; included for
               forward-compatibility when ``limit``/``offset`` are added.
    """

    items: list[RunListItem]
    total: int
