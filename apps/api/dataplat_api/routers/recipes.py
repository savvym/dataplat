"""Recipes router — S037-F-037 + S038-F-038 + S039-F-039 + S040-F-040.

Provides:
  GET  /api/recipes      — paginated list of caller's recipes (F-038).
  POST /api/recipes      — create a recipe row (F-037).
  GET  /api/recipes/{id} — full recipe detail for the authenticated caller (F-039).
  PUT  /api/recipes/{id} — update recipe definition/description (F-040).

Auth enforcement (Depends(get_current_user)) MUST NOT be removed.

409 constraint name: The ``recipe`` table's unique constraint on ``name`` is
auto-named ``recipe_name_key`` by Postgres (single-column ``unique=True`` with
no explicit name kwarg in the migration).  This matches the same pattern as
``source_collection_name_key`` already in production use.

Route-ordering note: GET "" is registered BEFORE POST "" to follow the FastAPI
convention of registering read routes ahead of write routes.  Both operate on
the same path prefix so there is no collision risk, but registration order is
kept consistent with sources.py conventions.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import Dataset, Recipe, User
from dataplat_api.db.session import get_session
from dataplat_api.schemas.recipes import RecipeCreate, RecipeListItem, RecipeListResponse, RecipeOut, RecipeUpdate

router = APIRouter(prefix="/api/recipes", tags=["recipes"])


@router.get("", response_model=RecipeListResponse)
async def list_recipes(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RecipeListResponse:
    """List all recipes owned by the authenticated user.

    Returns all recipes in newest-first order (created_at DESC, id DESC).
    No pagination for MVP — recipe counts per user are expected to be small.
    ``total`` is included in the response envelope for forward-compatibility:
    a future paginated version can truncate ``items`` while keeping ``total``
    accurate without a breaking schema change.

    Auth required (F-008).
    """
    # Query 1: all rows for this owner, newest first.
    result = await session.execute(
        select(Recipe)
        .where(Recipe.owner_id == current_user.id)
        .order_by(Recipe.created_at.desc(), Recipe.id.desc())
    )
    rows = result.scalars().all()

    # Query 2: total count over the full owner-filtered set.
    count_result = await session.execute(
        select(func.count())
        .select_from(Recipe)
        .where(Recipe.owner_id == current_user.id)
    )
    total = count_result.scalar_one()

    items = [RecipeListItem.model_validate(row) for row in rows]
    return RecipeListResponse(items=items, total=total)


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


@router.get("/{id}", response_model=RecipeOut)
async def get_recipe(
    id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RecipeOut:
    """Return the full recipe record for the given id.

    Owner-scoping: combines ``id == ?`` AND ``owner_id == ?`` in one query so
    that a non-existent id and an id owned by another user both return 404
    (no-enumeration-leak, mirrors get_source / list_sources_by_collection).

    Returns ``RecipeOut`` (all 7 fields including ``definition``).

    Auth required (F-008).
    """
    result = await session.execute(
        select(Recipe)
        .where(Recipe.id == id)
        .where(Recipe.owner_id == current_user.id)
    )
    recipe = result.scalar_one_or_none()
    if recipe is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recipe not found",
        )
    return RecipeOut.model_validate(recipe)


@router.put("/{id}", response_model=RecipeOut)
async def update_recipe(
    id: int,
    body: RecipeUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RecipeOut:
    """Update an existing recipe's definition (and optionally description).

    Owner-scoping: combines ``id == ?`` AND ``owner_id == ?`` in one query so
    that a non-existent id and an id owned by another user both return 404
    (no-enumeration-leak, mirrors get_recipe).

    Freeze guard (invariant #3): if any dataset has been materialized from
    this recipe (``dataset.recipe_id == id``), the update is rejected with 409.
    The ``definition`` is the transformation contract; it must not change after
    any dataset has been produced from it.

    ``definition`` is always replaced in full (required field).
    ``description`` is only updated when explicitly present in the request body
    (uses Pydantic v2 ``model_fields_set`` — absence means "leave unchanged";
    null means "clear"; string means "update").

    ``updated_at`` is bumped app-side to a concrete UTC datetime (testability
    preference over ``func.now()`` — see agreed.md §6).

    Auth required (F-008).
    """
    # Step 1: Load recipe (owner-scoped) — collapses not-found + wrong-owner.
    result = await session.execute(
        select(Recipe)
        .where(Recipe.id == id)
        .where(Recipe.owner_id == current_user.id)
    )
    recipe = result.scalar_one_or_none()
    if recipe is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recipe not found",
        )

    # Step 2: Freeze check — reject if any dataset has been materialized.
    exists_result = await session.execute(
        select(exists().where(Dataset.recipe_id == recipe.id))
    )
    dataset_exists = exists_result.scalar_one()
    if dataset_exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Recipe is locked: a dataset has been materialized from it",
        )

    # Step 3: Apply patch.
    recipe.definition = body.definition
    if "description" in body.model_fields_set:
        recipe.description = body.description

    # Step 4: Bump updated_at (app-side UTC — trivially mockable in tests).
    recipe.updated_at = datetime.now(tz=timezone.utc)  # type: ignore[assignment]

    # Step 5: Commit and refresh.
    await session.commit()
    await session.refresh(recipe)

    return RecipeOut.model_validate(recipe)
