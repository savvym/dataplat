# F-022 Implementation Quick Reference

## Essential File Locations & Code Snippets

### 1. DocumentVariant Model & Schema
**File:** `apps/api/dataplat_api/db/models.py` (lines 111-152)
```python
class DocumentVariant(Base):
    __tablename__ = "document_variant"
    id: Mapped[int]                        # ← Query parameter: {variant_id}
    source_id: Mapped[Optional[int]]       # ← For ownership check via join to Source
    extractor_name: Mapped[str]
    storage_prefix: Mapped[str]            # ← e.g. "s3://documents/7/extract_mineru/"
    is_canonical: Mapped[Optional[bool]]
    materialized_at: Mapped[Optional[sa.DateTime]]
    dagster_run_id: Mapped[Optional[str]]
```

### 2. S3 Client Dependency
**File:** `apps/api/dataplat_api/storage/s3.py`
```python
async def get_s3_client() -> AsyncGenerator[Any, None]:
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=f"http://{settings.MINIO_ENDPOINT}",
        aws_access_key_id=settings.MINIO_ROOT_USER,
        aws_secret_access_key=settings.MINIO_ROOT_PASSWORD,
    ) as client:
        yield client
```

### 3. Existing Ownership-Scoping Pattern (Copy This!)
**File:** `apps/api/dataplat_api/routers/sources.py` (lines 370-382)
```python
# Step 1: source existence and accessibility check
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
```

### 4. Response Format Pattern (Custom Media Type)
**Reference:** Standard FastAPI pattern for non-JSON responses
```python
from fastapi.responses import Response

@router.get("/path", responses={200: {"content": {"text/markdown": {}}}})
async def handler() -> Response:
    return Response(
        content=markdown_string,
        status_code=200,
        media_type="text/markdown",
    )
```

### 5. Router Setup Pattern
**File:** `apps/api/dataplat_api/main.py` (lines 43-51)
```python
app.include_router(health_router)
app.include_router(admin_router)
# ... add documents_router here
```

### 6. Config Settings
**File:** `apps/api/dataplat_api/config.py`
```python
class Settings(BaseSettings):
    MINIO_DOCUMENTS_BUCKET: str = "documents"  # ← Bucket name
    # ... other settings
```

### 7. Test Pattern (Mock Session)
**File:** `apps/api/tests/test_documents_set_canonical.py` (lines 108-182)
Shows how to mock:
- Session with multiple execute() calls
- ORM objects with MagicMock(spec=DocumentVariant)
- Auth override: `app.dependency_overrides[get_current_user] = override_func`

### 8. Test Pattern (Async Generator for S3)
**Pattern for mocking S3:**
```python
async def _override_s3() -> AsyncGenerator[AsyncMock, None]:
    s3 = AsyncMock()
    s3.get_object = AsyncMock(return_value={
        "Body": AsyncMock(read=AsyncMock(return_value=json_bytes))
    })
    yield s3

# In test:
app.dependency_overrides[get_s3_client] = _override_s3
```

## Data Flow

```
Client Request
  ↓
GET /api/documents/{variant_id}/render
  ↓
1. Auth: Depends(get_current_user)
2. DB: SELECT variant WHERE id = {variant_id}
3. DB: Check ownership via Source.collection_id → SourceCollection.owner_id
4. If not found or not owned: raise 404
5. S3: get_object(Bucket="documents", Key=f"{variant.storage_prefix}doc.docling.json")
6. JSON parse → DoclingDocument
7. Render to markdown
8. Return Response(content=markdown, media_type="text/markdown")
```

## Response Examples

### Success (200)
```
HTTP/1.1 200 OK
Content-Type: text/markdown; charset=utf-8
Content-Length: 4521

# Document Title

## Section 1

Lorem ipsum dolor sit amet...

### Subsection 1.1

More content...
```

### Not Found (404)
```
HTTP/1.1 404 Not Found
Content-Type: application/json

{"detail":"Document variant not found"}
```

### Unauthorized (401)
```
HTTP/1.1 401 Unauthorized
Content-Type: application/json

{"detail":"Not authenticated"}
```

## Key Imports Needed

```python
# FastAPI
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

# SQLAlchemy
from sqlalchemy import select, or_, text
from sqlalchemy.ext.asyncio import AsyncSession

# Local
from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import DocumentVariant, Source, SourceCollection, User
from dataplat_api.db.session import get_session
from dataplat_api.storage.s3 import get_s3_client
from dataplat_api.config import settings

# Optional for DoclingDocument
# from docling_core import DocumentConverter  # if using docling-core
# import json  # for manual parsing
```

## Verification Checklist (verify/checks.sh)

Add to checks.sh:
```bash
documents) # New layer for F-022
  curl -s -H "Authorization: Bearer $(fake_token)" \
       http://localhost:8000/api/documents/1/render \
       | grep -q "^#"  # Check for markdown heading
  ;;
```

## Files to Create
1. `apps/api/dataplat_api/routers/documents.py` — Main endpoint
2. `apps/api/tests/test_documents_render.py` — Tests (3+ test cases)

## Files to Modify
1. `apps/api/dataplat_api/main.py` — Wire router
2. `apps/api/pyproject.toml` — Add deps if needed
3. `verify/checks.sh` — Add documents layer
4. `docker/docker-compose.dev.yml` — Ensure MINIO_DOCUMENTS_BUCKET set
5. `apps/api/uv.lock` — Regenerate after deps

## Hard Invariants to Follow
- ✓ Async SQLAlchemy only (no sync queries)
- ✓ All DB sessions must be AsyncSession
- ✓ 404 for both "not found" AND "not accessible" (no enumeration leaks)
- ✓ After API change: run `make codegen` and commit diff with OpenAPI JSON

Generated: 2026-05-26
