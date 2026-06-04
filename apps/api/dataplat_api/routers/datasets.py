"""Datasets router — S042-F-042 + S045-F-045 + S046-F-046 + S047-F-047.

Provides:
  GET  /api/datasets               — list all caller-owned datasets (F-045).
  GET  /api/datasets/{id}          — full dataset record for one dataset (F-046).
  GET  /api/datasets/{id}/download — presigned URL list for 5 dataset artifacts (F-047).
  POST /api/datasets/{recipe_id}/materialize — create a Dataset row and launch a
      Dagster backfill for the stub ``dataset`` asset (F-042).

Auth enforcement (Depends(get_current_user)) MUST NOT be removed.

Route flow (10 steps, per agreed.md §4):
  1.  Auth gate (401 if missing/invalid token).
  2.  Load recipe owner-scoped — 404 if not found or wrong owner (no enumeration leak).
  3.  Compute version_tag and partition_key from COUNT(*) including failed rows.
  4.  INSERT Dataset row with status='pending', recipe_snapshot, hf_repo_uri='__pending__'.
  5.  await session.flush() to obtain dataset.id; update hf_repo_uri in-transaction;
      capture dataset_id, version_tag, partition_key as plain Python locals.
  6.  await session.commit() — row is now durable.
      Rollback boundary: Steps 7-9 are Dagster side-effects; failures → tombstone.
  7.  gateway.add_dataset_partition(partition_key) — registers in "dataset_versions".
      On DagsterGatewayError: UPDATE status='failed', commit, return 503.
  8.  gateway.launch_dataset_backfill([partition_key]) — returns backfillId.
      On DagsterGatewayError: UPDATE status='failed', commit, return 503.
  9.  UPDATE dataset SET dagster_run_id=backfill_id, commit.
  10. Return HTTP 202 {"dataset_id": dataset_id, "dagster_run_id": backfill_id}.

IntegrityError on INSERT (uq_dataset_recipe_version race) → 409 Conflict.
"""

from __future__ import annotations

import copy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.config import settings
from dataplat_api.dagster.dependencies import get_dagster_gateway
from dataplat_api.dagster.gateway import DagsterGateway, DagsterGatewayError
from dataplat_api.db.models import Dataset, Recipe, User
from dataplat_api.db.session import get_session
from dataplat_api.schemas.datasets import (
    DatasetDetailResponse,
    DatasetDownloadFile,
    DatasetDownloadResponse,
    DatasetListItem,
    DatasetListResponse,
    MaterializeResponse,
)
from dataplat_api.storage.s3 import get_s3_client

router = APIRouter(prefix="/api/datasets", tags=["datasets"])

# Module-level constant (NIT-1 resolution): avoids magic number in 5 call sites.
# 1 hour TTL; configurable TTL deferred to post-MVP.
_PRESIGN_TTL_SECONDS: int = 3600


@router.get("", response_model=DatasetListResponse)
async def list_datasets(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DatasetListResponse:
    """List all datasets owned by the authenticated user.

    Returns all dataset rows where ``materialized_by == current_user.id``,
    ordered newest-completed-first: ``materialized_at DESC NULLS LAST, id DESC``.
    ``NULLS LAST`` pushes in-flight (status='pending'/'running') datasets to the
    bottom, since their ``materialized_at`` is NULL until F-044 flips status='done'.
    Failed rows (status='failed') are included as audit tombstones.

    No pagination for MVP — dataset counts per user are expected to be small.
    ``total`` is included in the response envelope for forward-compatibility.

    Auth required (F-008).
    """
    # Query 1: all rows for this owner, newest completed first.
    result = await session.execute(
        select(Dataset)
        .where(Dataset.materialized_by == current_user.id)
        .order_by(Dataset.materialized_at.desc().nulls_last(), Dataset.id.desc())
    )
    rows = result.scalars().all()

    # Query 2: total count over the full owner-filtered set.
    count_result = await session.execute(
        select(func.count())
        .select_from(Dataset)
        .where(Dataset.materialized_by == current_user.id)
    )
    total = count_result.scalar_one()

    items = [DatasetListItem.model_validate(row) for row in rows]
    return DatasetListResponse(items=items, total=total)


@router.get("/{id}", response_model=DatasetDetailResponse)
async def get_dataset(
    id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DatasetDetailResponse:
    """Return the full dataset record for the authenticated owner.

    Owner-scoping: combines ``id == ?`` AND ``materialized_by == ?`` in one
    query so that a non-existent id and an id owned by another user both
    return 404 (no-enumeration-leak, mirrors get_recipe).
    ``materialized_by`` is the owner FK on Dataset (analogous to
    ``owner_id`` on Recipe).

    MAINTENANCE NOTE: Do NOT substitute ``owner_id`` for ``materialized_by``
    here.  The ``Dataset`` ORM model has no ``owner_id`` column; the owner FK
    is ``Dataset.materialized_by`` (BigInteger FK → users.id), set by
    ``materialize_dataset()`` as ``materialized_by=current_user.id``.
    Using ``owner_id`` would cause an AttributeError at runtime.
    """
    result = await session.execute(
        select(Dataset)
        .where(Dataset.id == id)
        .where(Dataset.materialized_by == current_user.id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )
    return DatasetDetailResponse.model_validate(row)


@router.get("/{id}/download", response_model=DatasetDownloadResponse)
async def download_dataset(
    id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    s3: Any = Depends(get_s3_client),
) -> DatasetDownloadResponse:
    """Return presigned MinIO GET URLs for all five dataset artifacts (F-047).

    Owner-scoping: combines ``id == ?`` AND ``materialized_by == ?`` in one
    query so that a non-existent id and an id owned by another user both
    return 404 (no-enumeration-leak, mirrors get_dataset).

    The five objects written by F-044's HFDatasetIOManager are:
      {prefix}/data/train-00000.parquet
      {prefix}/data/validation-00000.parquet
      {prefix}/recipe.json
      {prefix}/README.md
      {prefix}/dataset_infos.json

    where ``prefix = f"{row.id}_{row.version_tag}"``.

    Presigned URLs use ExpiresIn=_PRESIGN_TTL_SECONDS (3600 s = 1 hour).
    The TTL is echoed in ``expires_in_seconds`` so clients can cache-invalidate.

    Auth required (F-008).  No status gate — frontend (F-069) gates the
    Download button; if objects don't exist, MinIO returns 404 on fetch.
    """
    result = await session.execute(
        select(Dataset)
        .where(Dataset.id == id)
        .where(Dataset.materialized_by == current_user.id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )

    prefix = f"{row.id}_{row.version_tag}"
    object_keys = [
        f"{prefix}/data/train-00000.parquet",
        f"{prefix}/data/validation-00000.parquet",
        f"{prefix}/recipe.json",
        f"{prefix}/README.md",
        f"{prefix}/dataset_infos.json",
    ]

    files: list[DatasetDownloadFile] = []
    for key in object_keys:
        url = await s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.MINIO_DATASETS_BUCKET, "Key": key},
            ExpiresIn=_PRESIGN_TTL_SECONDS,
        )
        # Relative name is the part after the prefix + "/"
        name = key[len(prefix) + 1 :]
        files.append(DatasetDownloadFile(name=name, presigned_url=url))

    return DatasetDownloadResponse(
        dataset_id=row.id,
        files=files,
        expires_in_seconds=_PRESIGN_TTL_SECONDS,
    )


@router.post(
    "/{recipe_id}/materialize",
    response_model=MaterializeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def materialize_dataset(
    recipe_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    gateway: DagsterGateway = Depends(get_dagster_gateway),
) -> MaterializeResponse | JSONResponse:
    """Create a Dataset row and launch a Dagster backfill for the ``dataset`` asset.

    Owner-scoping: combines ``id == ?`` AND ``owner_id == ?`` in one query so
    that a non-existent recipe_id and a recipe owned by another user both return
    404 (no-enumeration-leak, mirrors recipes.py).

    Version tagging: ``COUNT(*)`` over ALL existing Dataset rows for this recipe
    (including status='failed' rows) so that a failed v1 attempt causes the next
    attempt to produce v2, never reusing a version_tag that was already registered
    in Dagster's partition definition.

    Rollback boundary: the Dataset row is committed before any Dagster calls.
    If Dagster calls fail, the row is tombstoned (status='failed') — not deleted.
    A 'failed' row does NOT lock the recipe (see H1 fix in recipes.py).

    Auth required (F-008).
    """
    # ── Step 1: Auth ─────────────────────────────────────────────────────────
    # Handled by Depends(get_current_user) — 401 if token absent/invalid.

    # ── Step 2: Load recipe (owner-scoped) ───────────────────────────────────
    result = await session.execute(
        select(Recipe)
        .where(Recipe.id == recipe_id)
        .where(Recipe.owner_id == current_user.id)
    )
    recipe = result.scalar_one_or_none()
    if recipe is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recipe not found",
        )

    # ── Step 3: Compute version_tag and partition_key ─────────────────────────
    # COUNT(*) includes status='failed' rows intentionally — prevents reuse of a
    # version_tag that was already registered in Dagster's partition definition.
    count_result = await session.execute(
        select(func.count()).select_from(Dataset).where(Dataset.recipe_id == recipe_id)
    )
    count = count_result.scalar_one()
    n = count + 1
    version_tag: str = f"v{n}"
    partition_key: str = f"ds_{recipe_id}_v{n}"

    # ── Step 4: INSERT Dataset row ────────────────────────────────────────────
    dataset = Dataset(
        recipe_id=recipe_id,
        recipe_snapshot=copy.deepcopy(recipe.definition),
        version_tag=version_tag,
        hf_repo_uri="__pending__",  # placeholder; replaced after flush (Step 5)
        status="pending",
        materialized_by=current_user.id,
        dagster_run_id=None,
    )
    try:
        session.add(dataset)

        # ── Step 5: flush to get DB-assigned id; update hf_repo_uri in-transaction ──
        await session.flush()
        dataset.hf_repo_uri = f"s3://datasets/{dataset.id}_{version_tag}"

        # Capture primitives before commit (M2: avoids post-expire ORM access).
        # After session.commit() SQLAlchemy expires non-PK attributes on dataset.
        # Capture all values needed in post-commit steps as plain Python locals NOW.
        dataset_id: int = dataset.id  # PK — DB-assigned on flush

        # ── Step 6: commit — row is now durable ──────────────────────────────
        await session.commit()

    except IntegrityError:
        await session.rollback()
        # uq_dataset_recipe_version UNIQUE constraint violation — concurrent race
        # or (very unlikely) exact same version_tag already exists.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Concurrent materialize conflict: version_tag already exists",
        )

    # ── Rollback boundary ─────────────────────────────────────────────────────
    # Steps 7-9 are Dagster side-effects. If any fail, tombstone the row
    # (UPDATE status='failed') and return 503. Do NOT delete — tombstone approach
    # preserves audit trail and avoids TOCTOU issues if add_dataset_partition
    # succeeded but launch_dataset_backfill failed.
    #
    # Residual risk (L2): if the tombstone UPDATE itself fails (DB dropout between
    # Step 6 commit and error-path UPDATE), a status='pending' row with
    # dagster_run_id=NULL persists indefinitely. Recovery: ops runs direct SQL:
    #   UPDATE dataset SET status='failed'
    #   WHERE dagster_run_id IS NULL AND status='pending';
    # Risk accepted: requires double DB failure; row is inert (no MinIO data).

    # ── Step 7: register partition in Dagster ────────────────────────────────
    try:
        await gateway.add_dataset_partition(partition_key)
    except DagsterGatewayError as exc:
        await session.execute(
            update(Dataset)
            .where(Dataset.id == dataset_id)  # use captured local int
            .values(status="failed")
        )
        await session.commit()
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    # ── Step 8: launch Dagster backfill ──────────────────────────────────────
    try:
        backfill_id: str = await gateway.launch_dataset_backfill([partition_key])
    except DagsterGatewayError as exc:
        await session.execute(
            update(Dataset)
            .where(Dataset.id == dataset_id)  # use captured local int
            .values(status="failed")
        )
        await session.commit()
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    # ── Step 9: write backfill_id back to the dataset row ────────────────────
    await session.execute(
        update(Dataset)
        .where(Dataset.id == dataset_id)  # use captured local int
        .values(dagster_run_id=backfill_id)
    )
    await session.commit()

    # ── Step 10: return 202 ──────────────────────────────────────────────────
    # Use captured locals — do NOT access dataset.id or dataset.dagster_run_id
    # after the Step 6 commit, as those attributes are expired.
    return MaterializeResponse(dataset_id=dataset_id, dagster_run_id=backfill_id)
