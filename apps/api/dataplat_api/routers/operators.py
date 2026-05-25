"""Operators router — S016-F-016 / S017-F-017.

Provides:
  GET /api/operators               — list all active operators (optional ?category= filter)
  GET /api/operators/{operator_id} — full operator detail record (F-017)

Auth enforcement (Depends(get_current_user)) is the F-008 deliverable and
MUST NOT be removed from any handler.

Active semantics (agreed.md §4):
  Filter on `is_active IS NOT FALSE` — includes rows with is_active=true (the
  server default) AND is_active=NULL; excludes only explicit is_active=false.
  This correctly handles the seeded MinerU row which relies on the server default
  and may not have is_active re-fetched into the Python-side ORM object.

Category filter (agreed.md §3.2):
  Optional. If omitted, returns all active operators across all categories.
  If provided, further filters WHERE category = <value>.
  Unknown categories return HTTP 200 with an empty array, not 404.

Response (agreed.md §3.3):
  Plain JSON array of OperatorRead objects. Not paginated — the operator registry
  is a small bounded catalogue; the verification criteria expect a plain array.

Ordering (agreed.md §5):
  id ASC — same convention as all other list endpoints in this codebase.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import Operator, User
from dataplat_api.db.session import get_session
from dataplat_api.schemas.operators import OperatorDetail, OperatorRead

router = APIRouter(prefix="/api/operators", tags=["operators"])


@router.get("", response_model=list[OperatorRead])
async def list_operators(
    category: str | None = Query(default=None, description="Filter by operator category (e.g. 'extractor', 'tagger'). Optional. Unknown categories return an empty array."),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[OperatorRead]:
    """List active operators, optionally filtered by category.

    Returns a plain JSON array of OperatorRead objects. All authenticated users
    can list operators — no ownership scoping applies (operators are global
    registry entries, not user-owned resources).

    Active scoping:
      `is_active IS NOT FALSE` — rows with is_active=true OR is_active=NULL are
      included; rows with is_active=false are excluded. This is the correct
      expression when the column is nullable with a server-side default of true.

    Category filter:
      If ?category= is provided, further restricts to operators with that exact
      category value. An unknown category value returns HTTP 200 + [].

    Ordering: id ASC (stable, oldest-first, consistent with all other list endpoints).

    Auth required (F-008).
    """
    stmt = (
        select(Operator)
        .where(Operator.is_active.isnot(False))
        .order_by(Operator.id.asc())
    )
    if category is not None:
        stmt = stmt.where(Operator.category == category)

    result = await session.execute(stmt)
    rows = result.scalars().all()

    return [OperatorRead.model_validate(row) for row in rows]


@router.get("/{operator_id}", response_model=OperatorDetail, summary="Get Operator Detail")
async def get_operator(
    operator_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> OperatorDetail:
    """Return the full operator record for the given id.

    Operators are a global registry — there is no owner_id column and no
    ownership scoping. Any authenticated user can retrieve any operator by id.
    This differs from sources (owner-scoped) and collections (owner-scoped).

    Returns 404 if no operator row with operator_id exists.
    Returns 401 if the Bearer token is absent, invalid, or expired.
    Returns 422 if operator_id is not a valid integer (FastAPI default).

    Auth required (F-008).
    """
    result = await session.execute(
        select(Operator).where(Operator.id == operator_id)
    )
    operator = result.scalar_one_or_none()
    if operator is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Operator not found",
        )
    return OperatorDetail.model_validate(operator)
