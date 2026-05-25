"""Sources router — S008-F-008 stub + S009-F-009 POST + S010-F-010 GET list.

Provides:
  GET  /api/sources/collections — paginated list of caller's collections (F-010).
  POST /api/sources/collections — create a source_collection row (F-009).

Auth enforcement (Depends(get_current_user)) is the F-008 deliverable and
MUST NOT be removed from either handler.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import SourceCollection, User
from dataplat_api.db.session import get_session
from dataplat_api.schemas.collections import (
    CollectionListResponse,
    SourceCollectionCreate,
    SourceCollectionOut,
)

router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.get("/collections", response_model=CollectionListResponse)
async def list_collections(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CollectionListResponse:
    """List source collections owned by the authenticated user.

    Returns a paginated list scoped to the caller's owner_id.
    `total` is the count of ALL the caller's collections (not just the current
    page), enabling the client to compute page counts without a second request.

    Pagination:
      limit  — max items per page (1–200, default 20)
      offset — number of rows to skip (default 0)

    Ordering: id ASC (oldest first, stable).
    Auth required (F-008).
    """
    # Query 1: paginated page, owner-filtered, ordered by id ASC.
    result = await session.execute(
        select(SourceCollection)
        .where(SourceCollection.owner_id == current_user.id)
        .order_by(SourceCollection.id.asc())
        .limit(limit)
        .offset(offset)
    )
    rows = result.scalars().all()

    # Query 2: total count over the full owner-filtered set (no limit/offset).
    count_result = await session.execute(
        select(func.count())
        .select_from(SourceCollection)
        .where(SourceCollection.owner_id == current_user.id)
    )
    total = count_result.scalar_one()

    items = [SourceCollectionOut.model_validate(row) for row in rows]
    return CollectionListResponse(items=items, total=total)


@router.post(
    "/collections",
    response_model=SourceCollectionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create Source Collection",
)
async def create_collection(
    body: SourceCollectionCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SourceCollectionOut:
    """Create a new source collection.

    Creates a source_collection row in Postgres and returns the new record.
    Duplicate names return 409 (detected via the Postgres UNIQUE constraint
    `source_collection_name_key`). Auth required (F-008).
    """
    collection = SourceCollection(
        name=body.name,
        owner_id=current_user.id,
        dataset_card_md=body.dataset_card_md,
    )
    try:
        session.add(collection)
        await session.commit()
        await session.refresh(collection)
    except IntegrityError as exc:
        await session.rollback()
        # Match the exact auto-generated UNIQUE constraint name so only a name
        # collision produces a 409; any other IntegrityError (e.g. FK violation)
        # is re-raised to surface as a 500.
        if "source_collection_name_key" in str(exc.orig):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Collection name already exists",
            )
        raise

    return SourceCollectionOut.model_validate(collection)
