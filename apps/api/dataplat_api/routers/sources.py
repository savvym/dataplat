"""Sources router — S008-F-008 stub + S009-F-009 POST.

Provides:
  GET  /api/sources/collections — stub; real body owned by F-010.
  POST /api/sources/collections — create a source_collection row (F-009).

F-010 will replace the GET stub body with a paginated DB query and narrow
CollectionListResponse.items from list[Any] to list[SourceCollectionOut].
Auth enforcement (Depends(get_current_user)) is the F-008 deliverable and
MUST NOT be removed when F-010 replaces the GET body.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
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
    current_user: User = Depends(get_current_user),
) -> CollectionListResponse:
    """List source collections.

    Stub — body owned by F-010. Auth enforcement is the F-008 deliverable.
    F-010 will add pagination parameters and a real DB query here.
    """
    return CollectionListResponse(items=[], total=0)


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
