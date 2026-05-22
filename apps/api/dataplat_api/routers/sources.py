"""Sources router — S008-F-008 stub.

Provides:
  GET /api/sources/collections — stub; real body owned by F-010.

F-010 will replace the stub body with a paginated DB query and narrow
CollectionListResponse.items from list[Any] to list[SourceCollectionOut].
Auth enforcement (Depends(get_current_user)) is the F-008 deliverable and
MUST NOT be removed when F-010 replaces the body.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import User
from dataplat_api.schemas.collections import CollectionListResponse

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
