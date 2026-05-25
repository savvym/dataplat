"""Sources router — S008-F-008 stub + S009-F-009 POST + S010-F-010 GET list
+ S011-F-011 POST /upload.

Provides:
  GET  /api/sources/collections — paginated list of caller's collections (F-010).
  POST /api/sources/collections — create a source_collection row (F-009).
  POST /api/sources/upload      — upload a PDF source file (F-011).

Auth enforcement (Depends(get_current_user)) is the F-008 deliverable and
MUST NOT be removed from any handler.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.config import settings
from dataplat_api.db.models import Source, SourceCollection, User
from dataplat_api.db.session import get_session
from dataplat_api.schemas.collections import (
    CollectionListResponse,
    SourceCollectionCreate,
    SourceCollectionOut,
)
from dataplat_api.schemas.sources import SourceUploadResponse
from dataplat_api.storage.s3 import get_s3_client

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


@router.post(
    "/upload",
    response_model=SourceUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload PDF Source",
)
async def upload_source(
    file: UploadFile = File(...),
    collection_id: int | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    s3: Any = Depends(get_s3_client),
) -> SourceUploadResponse:
    """Upload a PDF file as a new source.

    Stores the file in MinIO at s3://sources/{source_id}/original.pdf and
    writes a source row to Postgres with sha256, storage_uri, kind='file',
    mime_type='application/pdf'.  Returns the new source id and storage_uri.

    Atomicity (agreed.md §3-D3):
      flush (get id) → set uri/partition_key → S3 upload → commit.
      If S3 upload fails, the exception propagates; the open DB transaction is
      implicitly rolled back when the connection returns to the pool (the
      get_session() context manager calls session.close(), NOT rollback()).
      Do NOT add explicit session.rollback() here — let exceptions propagate.
      Known acceptable leak: if commit() fails after a successful S3 upload,
      the MinIO object persists with no corresponding DB row (agreed.md §3-D6).

    Auth required (F-008).
    """
    # Step 1: validate content-type before touching any bytes or the DB.
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only application/pdf uploads are accepted",
        )

    # Step 2-5: read bytes + compute sha256 + size + original_name.
    content: bytes = await file.read()
    sha256_hex = hashlib.sha256(content).hexdigest()
    size_bytes = len(content)
    original_name = file.filename or "upload.pdf"

    # Step 6: build Source ORM object with placeholder values for the two
    # NOT NULL id-dependent fields.
    # - storage_uri: constant placeholder is safe (no UNIQUE constraint).
    # - dagster_partition_key: MUST be unique per request (UNIQUE constraint);
    #   uuid4().hex is cryptographically random and collision-proof under
    #   concurrent async requests.  Do NOT use id(source)/id(object()) —
    #   CPython reuses freed object addresses (agreed.md §3-D3).
    source = Source(
        kind="file",
        original_name=original_name,
        sha256=sha256_hex,
        size=size_bytes,
        mime_type="application/pdf",
        collection_id=collection_id,
        storage_uri="__pending__",
        dagster_partition_key=f"src_tmp_{uuid.uuid4().hex}",
    )
    session.add(source)

    # Step 4 (agreed.md §3-D3 sequence): flush to get DB-assigned id.
    # No COMMIT yet — transaction remains open.
    await session.flush()

    # Steps 7-9: now that source.id is populated, derive the final values.
    # session.commit() auto-flushes dirty attrs, so these overwrites will
    # persist without a second flush.
    source.storage_uri = f"s3://sources/{source.id}/original.pdf"
    source.dagster_partition_key = f"src_{source.id}"

    # Step 10: upload to MinIO BEFORE commit.  If this raises, the exception
    # propagates and the uncommitted transaction is implicitly rolled back.
    # Do NOT wrap in a swallowing try/except; do NOT call session.rollback().
    s3_key = f"sources/{source.id}/original.pdf"
    await s3.put_object(
        Bucket=settings.MINIO_SOURCES_BUCKET,
        Key=s3_key,
        Body=content,
        ContentType="application/pdf",
    )

    # Step 11: commit — row is now durable with correct storage_uri and
    # dagster_partition_key.
    await session.commit()

    # Step 12: return minimal response per agreed.md §3-D8.
    return SourceUploadResponse(id=source.id, storage_uri=source.storage_uri)
