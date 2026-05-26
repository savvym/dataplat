# FastAPI Document Infrastructure Investigation Report

## Executive Summary

This report documents the existing document infrastructure in the Dataplat FastAPI project to enable planning of a new endpoint: `GET /api/documents/{variant_id}/render` that returns a markdown representation of a DoclingDocument.

**Feature Context:** F-022 in `spec/feature_list.json`
- **Status:** `passes: false` (not yet implemented)
- **Dependency:** Blocked by F-019 (MinerU extractor must produce DoclingDocument)
- **Verification Criteria:**
  1. GET returns 200 with Content-Type text/markdown and non-empty body
  2. Rendered markdown contains text extracted from source PDF
  3. GET on non-existent variant returns 404

---

## 1. Document Models & Schema

### Location
`apps/api/dataplat_api/db/models.py` (lines 111-152)

### DocumentVariant ORM Model
```python
class DocumentVariant(Base):
    __tablename__ = "document_variant"
    
    # Key fields for document retrieval:
    id: Mapped[int]                                    # Primary key (variant identifier)
    source_id: Mapped[Optional[int]]                   # FK to source
    extractor_name: Mapped[str]                        # e.g. "mineru", "docling"
    extractor_version: Mapped[str]                     # Semantic version
    config_hash: Mapped[str]                           # SHA-256 of operator config
    storage_prefix: Mapped[str]                        # KEY: s3://documents/{source_id}/{extractor}/
    
    # Metadata
    page_count: Mapped[Optional[int]]
    image_count: Mapped[Optional[int]]
    is_canonical: Mapped[Optional[bool]]               # Marks preferred variant
    materialized_at: Mapped[Optional[sa.DateTime]]     # Extraction completion time
    dagster_run_id: Mapped[Optional[str]]              # Run that produced this variant
    
    # Constraints
    __table_args__ = (
        sa.UniqueConstraint("source_id", "extractor_name", "config_hash", ...),
        sa.Index("idx_doc_variant_source", "source_id"),
        sa.Index("idx_doc_canonical", "source_id", unique=True, 
                 postgresql_where=text("is_canonical")),
    )
```

### Database Schema (from Alembic migration 0001)
```sql
CREATE TABLE document_variant (
  id BIGSERIAL PRIMARY KEY,
  source_id BIGINT REFERENCES source(id) ON DELETE CASCADE,
  extractor_name TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  storage_prefix TEXT NOT NULL,              -- e.g. s3://documents/7/extract_mineru/
  page_count INT,
  image_count INT,
  is_canonical BOOLEAN DEFAULT FALSE,
  materialized_at TIMESTAMPTZ DEFAULT NOW(),
  dagster_run_id TEXT,
  UNIQUE (source_id, extractor_name, config_hash),
  UNIQUE (source_id) WHERE is_canonical
);
```

### Related Source Model (lines 72-107)
```python
class Source(Base):
    __tablename__ = "source"
    id: Mapped[int]
    collection_id: Mapped[Optional[int]]     # FK to source_collection for ownership
    kind: Mapped[str]                        # "file"
    original_name: Mapped[str]               # Original filename
    storage_uri: Mapped[str]                 # s3://sources/{id}/original.pdf
    sha256: Mapped[str]
    # ... other fields
```

---

## 2. Existing Document-Related Routers

### Location
`apps/api/dataplat_api/routers/sources.py`

### Existing Document Endpoints

#### GET /api/sources/{source_id}/documents (F-020) — List Variants
```python
@router.get("/{source_id}/documents", response_model=list[DocumentVariantRead])
async def list_document_variants(
    source_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[DocumentVariantRead]:
    """Return flat list of all document_variant rows for given source."""
    # 1. Owner-scoping check (source accessibility)
    # 2. SELECT all DocumentVariant rows for source_id
    # 3. Return as list (not paginated)
```

**Response Schema:** `DocumentVariantRead` (from `schemas/sources.py` lines 69-100)
```python
class DocumentVariantRead(BaseModel):
    id: int
    extractor_name: str
    extractor_version: str
    config_hash: str
    storage_prefix: str                      # ← KEY: pointer to s3://documents/...
    page_count: int | None
    image_count: int | None
    is_canonical: bool | None
    materialized_at: datetime | None
    dagster_run_id: str | None
    
    model_config = ConfigDict(from_attributes=True)
```

#### POST /api/sources/{source_id}/documents/{extractor_name}/set-canonical (F-021)
```python
@router.post("/{source_id}/documents/{extractor_name}/set-canonical", 
             response_model=DocumentVariantRead)
async def set_canonical_document_variant(
    source_id: int,
    extractor_name: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DocumentVariantRead:
    """Atomically set canonical variant (exactly 1 per source)."""
    # 1. Owner-scoping check
    # 2. Find latest DocumentVariant for (source_id, extractor_name)
    # 3. UPDATE: set is_canonical=TRUE on target, FALSE on all others
    # 4. Commit and return
```

### Key Pattern Observations

1. **Auth Gate:** All routes require `Depends(get_current_user)` (F-008)
2. **Owner-Scoping:** Sources in user's collection or unclaimed sources (collection_id IS NULL) are accessible
3. **404 Handling:** Returns 404 for both "not found" AND "not accessible" to prevent enumeration leaks
4. **Async SQLAlchemy:** Hard Invariant #5 — all DB sessions are AsyncSession; no sync queries

---

## 3. Object Storage (MinIO/S3) Layout

### Location of S3 Client
`apps/api/dataplat_api/storage/s3.py`

### Storage Structure (from design doc §4.3)
```
s3://documents/                                # Document extraction products
  {source_id}/
    extract_mineru/                            # One directory per extractor
      doc.docling.json                         # ← TARGET FILE: DoclingDocument JSON
      images/                                  # Referenced image assets
        0.png
        1.jpg
      manifest.json                            # source_refs + version info
    extract_docling/
      doc.docling.json
      ...
    _canonical -> extract_mineru/              # Soft link/pointer to current canonical
```

### DocumentVariant.storage_prefix Format
- Example: `s3://documents/7/extract_mineru/`
- Derive DoclingDocument path: `{storage_prefix}doc.docling.json`
  - e.g. `s3://documents/7/extract_mineru/doc.docling.json`

### S3 Client Dependency
```python
async def get_s3_client() -> AsyncGenerator[Any, None]:
    """FastAPI dependency — yields aioboto3 S3 client."""
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=f"http://{settings.MINIO_ENDPOINT}",  # "minio:9000"
        aws_access_key_id=settings.MINIO_ROOT_USER,        # "minioadmin"
        aws_secret_access_key=settings.MINIO_ROOT_PASSWORD, # "devpassword"
    ) as client:
        yield client
```

### Document Bucket Name
- Bucket: `documents` (per F-003 implementation; note the hyphen variant issue at line 81 of progress notes)
- Actually created as: `documents-vlm` for VLM-enhanced, `documents` for base
- Confirmed in docker-compose: `MINIO_DOCUMENTS_BUCKET=documents`

---

## 4. DoclingDocument Handling

### Current Status
- **NO existing DoclingDocument imports** in the codebase (`grep -r "docling"` returns nothing)
- **NO existing deserialization code** — F-022 will be the first to interact with DoclingDocument JSON
- DoclingDocument is produced by F-019 (MinerU extractor) as raw JSON

### Expected DoclingDocument Structure
From the design doc and typical Docling usage:
```json
{
  "version": "0.3.0",
  "pages": [
    {
      "page_num": 0,
      "children": [
        {
          "type": "text",
          "text": "Page content here..."
        },
        {
          "type": "title",
          "text": "Section Title"
        }
      ]
    }
  ]
}
```

**Implication for F-022:** Must handle JSON deserialization + traversal to extract markdown representation. Can use:
- `docling-core` library (lightweight, just models)
- Manual JSON parsing + recursive traversal
- Docling library itself (heavier dependency)

---

## 5. How Existing Content Retrieval Endpoints Work

### Pattern: GET /api/sources/{id} (F-013) — Get Source Detail
**Source Location:** `apps/api/dataplat_api/routers/sources.py` lines 495-532

```python
@router.get("/{id}", response_model=SourceRead)
async def get_source(
    id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SourceRead:
    """Return the full source record."""
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return SourceRead.model_validate(source)
```

**Key Points:**
- NO interaction with object store (just metadata from DB)
- Owner-scoping is inline (LEFT JOIN + OR condition)
- 404 raised as HTTPException with detail string
- Returns Pydantic model validated from ORM object

### Pattern: POST /api/sources/upload (F-011) — Retrieve & Store
**Source:** `apps/api/dataplat_api/routers/sources.py` lines 214-340

```python
@router.post("/upload", response_model=SourceUploadResponse, status_code=201)
async def upload_source(
    file: UploadFile = File(...),
    collection_id: int | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    s3: Any = Depends(get_s3_client),
    gateway: DagsterGateway = Depends(get_dagster_gateway),
) -> SourceUploadResponse:
    """Upload PDF and write source row."""
    # 1. Validate content-type
    # 2. Read bytes from UploadFile
    # 3. Compute sha256, size
    # 4. Create Source ORM object with placeholder values
    # 5. session.add(source) + session.flush() → get DB id
    # 6. Derive final storage_uri + dagster_partition_key
    # 7. Upload to MinIO: await s3.put_object(Bucket=..., Key=..., Body=...)
    # 8. session.commit()
    # 9. Best-effort Dagster notification
    # 10. Return minimal response
```

**Lessons for GET /documents/{variant_id}/render:**
- Use `s3.get_object()` to retrieve from MinIO (async)
- Handle 404 from S3 (object not found)
- Handle JSON parsing errors
- Return custom response (not JSON model)

---

## 6. Test Patterns for Document Endpoints

### Location
`apps/api/tests/`

### Existing Test: test_documents_set_canonical.py
Shows patterns for:
- **Mock session setup** (lines 108-182)
- **Mock ORM objects** using MagicMock with spec (lines 61-102)
- **Multiple execute() calls** on happy path (4 calls = ownership check + variant SELECT + CLEAR UPDATE + SET UPDATE)
- **404 short-circuit paths** (1-2 execute calls)
- **Auth gate test** — requests without Authorization header return 401

### Autouse Fixtures in conftest.py
Located: `apps/api/tests/conftest.py` lines 46-92

```python
@pytest.fixture(autouse=True)
def _patch_httpx_no_ssl() -> pytest.FixtureRequest:
    """Patch httpx.AsyncClient to use MockTransport (SSL workaround)."""
    original_init = httpx.AsyncClient.__init__
    def patched_init(self, *args, **kwargs):
        if "transport" not in kwargs:
            kwargs["transport"] = httpx.MockTransport(_no_op_transport)
        original_init(self, *args, **kwargs)
    with patch.object(httpx.AsyncClient, "__init__", patched_init):
        yield

@pytest.fixture(autouse=True)
def _patch_engine_begin() -> Iterator[None]:
    """Patch engine.begin() so tests don't need live Postgres."""
    @asynccontextmanager
    async def fake_begin(self=None) -> AsyncGenerator[MagicMock, None]:
        conn = MagicMock()
        conn.execute = AsyncMock(return_value=None)
        yield conn
    with patch.object(AsyncEngine, "begin", fake_begin):
        yield
```

### Test for Content Retrieval
**Mock S3 pattern needed for F-022:**
```python
# Would need to mock s3.get_object() to return:
# {"Body": AsyncIterable[bytes] | Awaitable[bytes]}
```

---

## 7. Verification Infrastructure

### Location
`verify/checks.sh`

### Layer Structure
The project uses layered verification (each exit 0 on success):
- `smoke` — API health + DB + MinIO + Dagster connectivity
- `backend` — pytest suite
- `infra` — compose config validation
- `contract` — grep enforcement of module boundaries
- `migration` — alembic upgrade/downgrade idempotence
- `dagster` — job/asset definitions
- `runs` — boundary enforcement on import httpx
- `auth` — F-007 specific (seed-admin, token generation)
- `buckets` — MinIO bucket existence
- `all` — full chain in order

### Expected Test Coverage for F-022
Should add a `documents` layer that verifies:
1. GET /api/documents/{variant_id}/render returns 200 (variant exists)
2. Response Content-Type is `text/markdown`
3. Response body is non-empty and valid markdown
4. GET /api/documents/99999/render returns 404

---

## 8. Configuration & Dependency Injection

### Settings
**Location:** `apps/api/dataplat_api/config.py` (lines 21-48)

```python
class Settings(BaseSettings):
    DATABASE_URL: str                              # Required; no default
    DAGSTER_GRAPHQL_URL: str = "http://dagster-webserver:3000/graphql"
    SECRET_KEY: str                                # Required; no default
    JWT_ALGORITHM: str = "HS256"
    JWT_TTL_SECONDS: int = 3600
    
    # MinIO / S3 settings
    MINIO_ENDPOINT: str = "minio:9000"            # host:port, no scheme
    MINIO_ROOT_USER: str = "minioadmin"
    MINIO_ROOT_PASSWORD: str = "devpassword"
    MINIO_SOURCES_BUCKET: str = "sources"
    
    # Note: MINIO_DOCUMENTS_BUCKET not in Settings (would need to add)
```

### Common Dependencies Pattern
```python
from fastapi import Depends
from dataplat_api.db.session import get_session
from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.storage.s3 import get_s3_client

@router.get("/endpoint")
async def handler(
    param: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    s3: Any = Depends(get_s3_client),
) -> ResponseModel:
    pass
```

---

## 9. Response Format Patterns

### JSON Response (Typical)
```python
@router.get("/api/endpoint", response_model=SomeSchema)
async def handler(...) -> SomeSchema:
    return SomeSchema(field=value)
```

### Custom Content-Type Response (Needed for F-022)
**Pattern from FastAPI docs:**
```python
from fastapi.responses import Response

@router.get("/api/endpoint", responses={200: {"content": {"text/markdown": {}}}})
async def handler(...) -> Response:
    return Response(
        content=markdown_string,
        status_code=200,
        media_type="text/markdown",
        headers={"Content-Type": "text/markdown; charset=utf-8"}
    )
```

### Error Response
```python
from fastapi import HTTPException, status

if not found:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Document variant not found"
    )
```

---

## 10. Key Hard Invariants Relevant to F-022

From CLAUDE.md (project instructions):

1. **Lineage is mandatory.** ✓ (Not applicable — F-022 reads existing lineage)
2. **Storage separation + CAS.** ✓ (Documents stored in MinIO, addressed by storage_prefix; metadata in Postgres)
3. **Schema frozen post-publish.** ✓ (Not applicable — F-022 doesn't alter schema)
4. **LLM calls go through the gateway.** ✓ (Not applicable — F-022 doesn't call LLM)
5. **Async SQLAlchemy from day one.** ✓ **MUST USE AsyncSession for any DB queries**
6. **OpenAPI ↔ TS type sync.** ✓ After any API schema change, run `make codegen` and commit the diff in the SAME commit

---

## 11. File Checklist for F-022 Implementation

### Files to Create
```
apps/api/dataplat_api/routers/documents.py          # Main router with GET /api/documents/{variant_id}/render
apps/api/dataplat_api/schemas/documents.py          # Response schemas (if needed; might use plain Response)
apps/api/tests/test_documents_render.py             # Unit tests
```

### Files to Modify
```
apps/api/dataplat_api/main.py                       # Wire documents_router
apps/api/dataplat_api/config.py                     # Add MINIO_DOCUMENTS_BUCKET if not present
docker/docker-compose.dev.yml                       # Add MINIO_DOCUMENTS_BUCKET if needed
docker/.env.example                                 # Document the new bucket name
verify/checks.sh                                    # Add documents) layer
apps/api/pyproject.toml                             # Add docling-core or similar dep if needed
apps/api/uv.lock                                    # Regenerate after deps change
packages/api-types/openapi.json                     # Regenerate after route added (make codegen)
```

### Files to Check (Read-Only)
```
docs/data_platform_design.md                        # §4.1 (document_variant schema)
                                                    # §4.3 (storage layout)
spec/feature_list.json                              # F-022 definition
contracts/S005-F-005/agreed.md                      # Prior sprint context
```

---

## 12. Dependencies to Consider

### Already Available
- `fastapi==0.115.12` — routing, responses
- `sqlalchemy[asyncio]==2.0.41` — async DB queries
- `asyncpg==0.30.0` — async Postgres driver
- `aioboto3==15.5.0` — async S3 client

### Candidates for Adding
- `docling-core>=1.0.0` — Lightweight DoclingDocument model (no heavy extractors)
  - Provides `DoclingDocument` Pydantic model + `to_markdown()` / `to_text()` methods
  - Alternative: `docling` (heavier, includes extraction)
  - Alternative: Manual JSON parsing + recursive traversal (no extra deps)

---

## 13. Architectural Summary

```
┌─────────────────────────────────────────────────────────────┐
│ GET /api/documents/{variant_id}/render (F-022)              │
└─────────────────────────────────────────────────────────────┘
                           ↓
         ┌─────────────────┼─────────────────┐
         ↓                 ↓                 ↓
    Postgres           Auth Gate         MinIO
    ─────────────────  (JWT)             ────────
    1. Get Variant    2. Check           3. Get
       by ID             current_user       doc.docling.json
    2. Ownership      3. Calc             4. Deserialize
       check             storage_prefix       JSON
                                          5. Render
                                             Markdown
         ↓                 ↓                 ↓
         └─────────────────┼─────────────────┘
                           ↓
                    Response (200/404)
                    Content-Type: text/markdown
```

---

## Summary of Key Implementation Points

1. **Query DocumentVariant by ID** → validate variant exists
2. **Owner-scoping check** → variant accessible to current_user via source ownership
3. **Construct S3 path** → `{variant.storage_prefix}doc.docling.json`
4. **Fetch DoclingDocument JSON** from MinIO using `s3.get_object()`
5. **Deserialize & render** → JSON to Pydantic model to Markdown (or custom traversal)
6. **Return Response** → `Response(content=markdown, media_type="text/markdown")`
7. **Error handling:**
   - Variant not found → 404
   - S3 object not found → 404 (variant exists but document missing)
   - Invalid JSON → 500 or graceful fallback
   - No access → 404 (prevent enumeration)

---

Generated: 2026-05-26
