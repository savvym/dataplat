"""Recipes router — S037-F-037.

Provides:
  POST /api/recipes — create a recipe row (F-037).

Auth enforcement (Depends(get_current_user)) MUST NOT be removed.

409 constraint name: The ``recipe`` table's unique constraint on ``name`` is
auto-named ``recipe_name_key`` by Postgres (single-column ``unique=True`` with
no explicit name kwarg in the migration).  This matches the same pattern as
``source_collection_name_key`` already in production use.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import Recipe, User
from dataplat_api.db.session import get_session
from dataplat_api.schemas.recipes import RecipeCreate, RecipeOut

router = APIRouter(prefix="/api/recipes", tags=["recipes"])


@router.post("", response_model=RecipeOut, status_code=status.HTTP_201_CREATED)
async def create_recipe(
    body: RecipeCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RecipeOut:
    """Create a new recipe.

    Creates a recipe row in Postgres and returns the new record.
    Duplicate names return 409 (detected via the Postgres UNIQUE constraint
    ``recipe_name_key``).  Auth required (F-008).

    ``owner_id`` is set to the authenticated user's id.  ``definition`` is
    stored as-is (any JSON object); structural validation is deferred to
    synthesis-time (F-082).
    """
    recipe = Recipe(
        name=body.name,
        description=body.description,
        owner_id=current_user.id,
        definition=body.definition,
    )
    try:
        session.add(recipe)
        await session.commit()
        await session.refresh(recipe)
    except IntegrityError as exc:
        await session.rollback()
        # Match the exact auto-generated UNIQUE constraint name so only a name
        # collision produces a 409; any other IntegrityError (e.g. FK violation)
        # is re-raised to surface as a 500.
        if "recipe_name_key" in str(exc.orig):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Recipe name already exists",
            )
        raise
    return RecipeOut.model_validate(recipe)
