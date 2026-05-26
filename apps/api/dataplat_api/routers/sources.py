"""Sources router — S008-F-008 stub + S009-F-009 POST + S010-F-010 GET list
+ S011-F-011 POST /upload + S012-F-012 Dagster notify + S013-F-013 GET /{id}
+ S014-F-014 GET /collections/{id}/sources + S020-F-020 GET /{source_id}/documents.

Provides:
  GET  /api/sources/collections                    — paginated list of caller's collections (F-010).
  POST /api/sources/collections                    — create a source_collection row (F-009).
  GET  /api/sources/collections/{id}/sources       — paginated list of sources in a collection (F-014).
  POST /api/sources/upload                         — upload a PDF source file (F-011).
  GET  /api/sources/{source_id}/documents          — flat list of document_variant rows (F-020).
  GET  /api/sources/{id}                           — full source detail record (F-013).

Auth enforcement (Depends(get_current_user)) is the F-008 deliverable and
MUST NOT be removed from any handler.

Route-ordering note (F-013/F-014/F-020): GET /{id} is registered LAST so that the
fixed-prefix paths /collections and /upload are matched first by FastAPI's
router.  GET /collections/{id}/sources (3 segments) cannot collide with GET
/{id} (1 segment) but must still be registered before the catch-all to keep
the ordering invariant clear and maintainable.  GET /{source_id}/documents
(2 segments) is registered immediately before /{id} for the same reason.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.config import settings
from dataplat_api.dagster.dependencies import get_dagster_gateway
from dataplat_api.dagster.gateway import DagsterGateway, DagsterGatewayError
from dataplat_api.db.models import DocumentVariant, Source, SourceCollection, User
from dataplat_api.db.session import get_session
from dataplat_api.schemas.collections import (
    CollectionListResponse,
    SourceCollectionCreate,
    SourceCollectionOut,
)
from dataplat_api.schemas.sources import (
    DocumentVariantRead,
    SourceListResponse,
    SourceRead,
    SourceUploadResponse,
)
from dataplat_api.storage.s3 import get_s3_client

logger = logging.getLogger(__name__)

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


@router.get(
    "/collections/{id}/sources",
    response_model=SourceListResponse,
    summary="List Sources in Collection",
)
async def list_sources_by_collection(
    id: int,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SourceListResponse:
    """Return a paginated list of sources belonging to the specified collection.

    Owner-scoping (F-014 agreed.md §3.5):
      Step 1 — Verify the collection exists AND is owned by the caller.
               If it does not exist or belongs to another user → 404.
               Returning 404 (not 403) for both cases prevents enumeration leaks.
      Step 2 — List sources in that collection (paginated page + total count).

    The ownership check is a dedicated query that short-circuits to 404 before
    the paginated source queries are issued.  Embedding ownership as a JOIN on
    the source queries would silently return {"items":[], "total":0} (HTTP 200)
    for an unowned collection — violating the no-enumeration-leak invariant.

    Pagination:
      limit  — max items per page (1–200, default 20)
      offset — number of rows to skip (default 0)
    total    — count of ALL sources in the collection (not just the current page)
    Ordering: Source.id ASC (oldest first, stable).

    Auth required (F-008).
    """
    # Query 1: verify collection exists and is owned by the caller.
    coll_result = await session.execute(
        select(SourceCollection)
        .where(SourceCollection.id == id)
        .where(SourceCollection.owner_id == current_user.id)
    )
    collection = coll_result.scalar_one_or_none()
    if collection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Collection not found",
        )

    # Query 2: paginated source page, collection-scoped, ordered by id ASC.
    result = await session.execute(
        select(Source)
        .where(Source.collection_id == id)
        .order_by(Source.id.asc())
        .limit(limit)
        .offset(offset)
    )
    rows = result.scalars().all()

    # Query 3: total count over ALL sources in this collection (no limit/offset).
    count_result = await session.execute(
        select(func.count())
        .select_from(Source)
        .where(Source.collection_id == id)
    )
    total = count_result.scalar_one()

    items = [SourceRead.model_validate(row) for row in rows]
    return SourceListResponse(items=items, total=total)


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
    gateway: DagsterGateway = Depends(get_dagster_gateway),
) -> SourceUploadResponse:
    """Upload a PDF file as a new source.

    Stores the file in MinIO at s3://sources/{source_id}/original.pdf and
    writes a source row to Postgres with sha256, storage_uri, kind='file',
    mime_type='application/pdf'.  Returns the new source id and storage_uri.

    After commit, notifies Dagster on a best-effort basis (F-012):
      1. addDynamicPartition: registers src_{id} in the "sources" partition def.
      2. reportRunlessAssetEvents: records a materialization event for 'source'.
    If either Dagster call fails, the upload still returns 201 — the source is
    durable in DB+MinIO regardless. A Dagster outage does not roll back the upload.

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

    # F-012: Best-effort Dagster notification — AFTER commit so the source is
    # durable regardless of Dagster availability.  DagsterGatewayError is caught
    # and logged at WARNING; the handler still returns 201 because the upload
    # genuinely succeeded.  The two calls are separate try/except blocks so
    # report_source_materialization is always attempted even if add_source_partition
    # fails (agreed.md §3-D-ordering, §4).
    partition_key = source.dagster_partition_key  # e.g. "src_42"
    try:
        await gateway.add_source_partition(partition_key)
    except DagsterGatewayError as exc:
        logger.warning(
            "F-012: add_source_partition failed for %s — Dagster may be down or "
            "partition def not loaded; partition not registered. "
            "Upload still succeeds. Error: %s",
            partition_key,
            exc,
        )
    try:
        await gateway.report_source_materialization(
            partition_key=partition_key,
            storage_uri=source.storage_uri,
            size_bytes=source.size or 0,
        )
    except DagsterGatewayError as exc:
        logger.warning(
            "F-012: report_source_materialization failed for %s — "
            "materialization event not recorded. Upload still succeeds. Error: %s",
            partition_key,
            exc,
        )

    # Step 12: return minimal response per agreed.md §3-D8.
    return SourceUploadResponse(id=source.id, storage_uri=source.storage_uri)


@router.get(
    "/{source_id}/documents",
    response_model=list[DocumentVariantRead],
    summary="List Document Variants",
)
async def list_document_variants(
    source_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[DocumentVariantRead]:
    """Return a flat list of all document_variant rows for the given source.

    Owner-scoping (F-020 agreed.md §4.2):
      Step 1 — Verify the source exists AND is accessible to the caller.
               A source is accessible if it has no collection (collection_id IS NULL)
               or its collection is owned by the caller.
               If the source does not exist or belongs to another user's collection
               → 404 (prevents enumeration leaks, same as GET /{id}).
      Step 2 — Fetch all document_variant rows for the source, ordered by id ASC.
               Returns an empty list ([]) when the source exists but has no
               variants yet (i.e. extraction has not run).

    Returns a plain JSON array (not paginated) — variants per source are small
    (typically 1–3 in practice).

    Auth required (F-008).
    """
    # Step 1: source existence and accessibility check.
    result = await session.execute(
        select(Source)
        .join(SourceCollection, Source.collection_id == SourceCollection.id, isouter=True)
        .where(Source.id == source_id)
        .where(
            or_(
                SourceCollection.owner_id == current_user.id,
                Source.collection_id.is_(None),
            )
        )
    )
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source not found",
        )

    # Step 2: fetch all document_variant rows for this source, ordered by id ASC.
    variants_result = await session.execute(
        select(DocumentVariant)
        .where(DocumentVariant.source_id == source_id)
        .order_by(DocumentVariant.id.asc())
    )
    rows = variants_result.scalars().all()
    return [DocumentVariantRead.model_validate(row) for row in rows]


@router.get("/{id}", response_model=SourceRead, summary="Get Source Detail")
async def get_source(
    id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SourceRead:
    """Return the full source record for the given id.

    Owner-scoping (F-013 agreed.md §3):
      - Sources in a collection owned by the caller → visible.
      - Sources with no collection (collection_id IS NULL) → visible to any
        authenticated user (no owner_id column on source; strict scoping would
        require a migration, deferred to a later sprint).
      - Sources in another user's collection → 404 (prevents enumeration).

    Returns 404 (not 403) for both "does not exist" and "not accessible" cases
    to avoid leaking existence information (same pattern as F-010 collections).

    Auth required (F-008).
    """
    result = await session.execute(
        select(Source)
        .join(SourceCollection, Source.collection_id == SourceCollection.id, isouter=True)
        .where(Source.id == id)
        .where(
            or_(
                SourceCollection.owner_id == current_user.id,
                Source.collection_id.is_(None),
            )
        )
    )
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source not found",
        )
    return SourceRead.model_validate(source)
