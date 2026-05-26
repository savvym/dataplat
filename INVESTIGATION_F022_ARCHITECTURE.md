# F-022 Architecture & Design Insights

## 1. Why F-022 Comes AFTER F-019 (MinerU Extractor)

**F-022 Dependency Chain:**
```
F-019: MinerU Extractor (creates doc.docling.json in MinIO)
  ↓
F-020: List document variants (GET /sources/{id}/documents)
  ↓
F-021: Set canonical variant (POST .../set-canonical)
  ↓
F-022: Render document preview (GET /documents/{variant_id}/render) ← HERE
```

**Implication:** F-022 cannot execute without F-019's DoclingDocument JSON existing in MinIO. The endpoint will fail gracefully with 404 if the document doesn't exist.

## 2. Key Architectural Patterns in Dataplat

### Pattern 1: Metadata + Storage Separation (Hard Invariant #2)
```
Postgres (Metadata)                  MinIO (Content)
──────────────────────────────────  ────────────────
document_variant table              s3://documents/
  ├─ id (PK)                          ├─ {source_id}/
  ├─ source_id (FK)                   │  ├─ extract_mineru/
  ├─ extractor_name                   │  │  ├─ doc.docling.json  ← TARGET
  ├─ storage_prefix (pointer)    ────→│  │  ├─ images/
  ├─ is_canonical                     │  │  └─ manifest.json
  └─ dagster_run_id                   │
                                      └─ extract_docling/
                                         └─ ...
```

**For F-022:** Use `document_variant.storage_prefix` to construct the S3 key.

### Pattern 2: Owner-Scoping (Prevents Enumeration Leaks)
Return 404 (NOT 403) for both:
- Variant doesn't exist
- Variant exists but user can't access the parent source

**Why?** If you return different status codes, attackers can enumerate valid resource IDs.

```python
# Step 1: Check variant exists
variant = await session.execute(select(DocumentVariant).where(...))
if not variant:
    raise HTTPException(404, "Document variant not found")

# Step 2: Check source ownership (via LEFT JOIN + OR)
result = await session.execute(
    select(Source)
    .join(SourceCollection, ..., isouter=True)
    .where(Source.id == variant.source_id)
    .where(or_(SourceCollection.owner_id == current_user.id, ...))
)
if not result:
    raise HTTPException(404, "Document variant not found")  # ← Same message!
```

### Pattern 3: Async-First Everywhere (Hard Invariant #5)
```python
# ✓ Correct
async def handler(..., session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(...))
    return result.scalars().one_or_none()

# ✗ Wrong
def handler(..., session: Session):  # Sync session not allowed
    return session.query(...).one_or_none()
```

### Pattern 4: Dependencies as Coroutine Generators
```python
# Storage layer pattern
async def get_s3_client() -> AsyncGenerator[Any, None]:
    session = aioboto3.Session()
    async with session.client(...) as client:
        yield client  # ← Injected into route handlers

# Usage
async def handler(s3: Any = Depends(get_s3_client)):
    await s3.get_object(...)  # ← Async call
    # Context manager auto-closes when handler returns
```

### Pattern 5: HTTPException for Errors
```python
# ✓ Correct: HTTPException with status_code + detail
raise HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Document variant not found"
)

# ✗ Wrong: Returning error dict
return {"error": "not found"}  # Status defaults to 200!

# ✗ Wrong: Raising exception without status mapping
raise ValueError("not found")  # Returns 500
```

## 3. Query Optimization Insights

### Why Multiple Queries is OK
```python
# Step 1: Get variant
variant = await session.execute(select(DocumentVariant).where(...))

# Step 2: Get source for ownership check
source = await session.execute(select(Source).where(...))

# Step 3: Get collection for owner_id
collection = await session.execute(
    select(SourceCollection).where(...)
)
```

**Why not JOIN?** Existing pattern in the codebase uses LEFT JOIN + OR logic:
```python
# Existing pattern (sources.py line 373):
select(Source)
.join(SourceCollection, Source.collection_id == SourceCollection.id, isouter=True)
.where(Source.id == source_id)
.where(or_(SourceCollection.owner_id == current_user.id, ...))
```

This is **clearer than a complex subquery** and the minor performance cost is negligible (indexed lookups).

## 4. S3 Path Construction

**StoragePrefix Format:** `s3://documents/{source_id}/{extractor}/`
- Example: `s3://documents/7/extract_mineru/`

**DoclingDocument Location:** `{storage_prefix}doc.docling.json`
- Full path: `s3://documents/7/extract_mineru/doc.docling.json`
- S3 Key (without scheme): `documents/7/extract_mineru/doc.docling.json`

**Code Pattern:**
```python
variant = DocumentVariant(...)
s3_key = f"{variant.storage_prefix}doc.docling.json"
# Becomes: "s3://documents/7/extract_mineru/doc.docling.json"

# But S3 client needs path without scheme:
bucket = settings.MINIO_DOCUMENTS_BUCKET  # "documents"
key = s3_key.replace("s3://documents/", "")  # "7/extract_mineru/doc.docling.json"

response = await s3.get_object(Bucket=bucket, Key=key)
```

## 5. DoclingDocument JSON Structure

Expected structure (from Docling library):
```json
{
  "format_version": "1.0.0",
  "version_info": {...},
  "pages": [
    {
      "page_num": 0,
      "size": {"width": 612, "height": 792},
      "children": [
        {
          "type": "heading",
          "text": "Chapter 1"
        },
        {
          "type": "paragraph",
          "text": "Lorem ipsum...",
          "children": [...]
        }
      ]
    }
  ],
  "body": {...}  # Top-level body node with all content
}
```

**Rendering Strategy:**
1. Parse JSON → deserialize to Pydantic model (or keep as dict)
2. Traverse pages → blocks → extract text by type
3. Construct markdown with appropriate heading levels (#, ##, ###)
4. Handle nested structures (lists, tables, code blocks)

## 6. Testing Strategy for F-022

### Unit Test Case 1: Happy Path (200)
```python
# Setup:
# - Mock session: returns DocumentVariant + Source (owned by current_user)
# - Mock S3: returns valid DoclingDocument JSON
# Assertions:
# - Response.status_code == 200
# - Response.headers["content-type"] == "text/markdown"
# - Response.content is non-empty
# - Response.content contains markdown (check for "#" or other markdown markers)
```

### Unit Test Case 2: Variant Not Found (404)
```python
# Setup:
# - Mock session: returns None for variant lookup
# Assertions:
# - Response.status_code == 404
# - Response.json()["detail"] == "Document variant not found"
```

### Unit Test Case 3: No Access (404, same as above)
```python
# Setup:
# - Mock session: returns DocumentVariant but Source is owned by different user
# Assertions:
# - Response.status_code == 404
# - Response.json()["detail"] == "Document variant not found"
```

### Unit Test Case 4: S3 Object Not Found (404)
```python
# Setup:
# - Mock session: returns valid DocumentVariant + Source (owned)
# - Mock S3: raises NoSuchKey exception
# Assertions:
# - Response.status_code == 404
# - Response.json()["detail"] == "Document not found in storage"
```

### Unit Test Case 5: Invalid JSON (500)
```python
# Setup:
# - Mock session: returns valid variant
# - Mock S3: returns non-JSON bytes
# Assertions:
# - Response.status_code == 500
# - Or: gracefully fall back to raw text
```

### Unit Test Case 6: No Auth Token (401)
```python
# Setup:
# - No Authorization header
# Assertions:
# - Response.status_code == 401
# - Response.json()["detail"] contains "Not authenticated"
```

## 7. Deployment Considerations

### MinIO Configuration
Buckets created by F-003:
- `sources` — Raw PDFs
- `documents` — DoclingDocument JSON + images
- `documents-vlm` — VLM-enhanced documents (Phase 2)
- `lance` — Vector store
- `datasets` — Dataset materialization

**For F-022:** Use `documents` bucket (already exists).

### Environment Variables
```bash
# Already in docker-compose.dev.yml:
MINIO_ENDPOINT=minio:9000
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=devpassword
MINIO_DOCUMENTS_BUCKET=documents
```

### Performance Notes
- First request to a variant: S3 fetch + JSON parse (latency ~100-500ms)
- MinIO is local in dev (no network overhead)
- Production can add caching (Redis) later (Phase 2)

## 8. Error Scenarios & Recovery

| Scenario | Response | Recovery |
|----------|----------|----------|
| Variant ID not found | 404 | No retry needed |
| Source owned by different user | 404 | Return same as not found (prevent enumeration) |
| S3 bucket unreachable | 503 | Retry after service recovery |
| S3 object missing | 404 | Document extraction may have failed; check dagster_run_id |
| Malformed JSON in S3 | 500 | Log error; may indicate Dagster job failure |
| Invalid JWT token | 401 | Redirect to login |

## 9. Future Extensions (Phase 2+)

### Potential Improvements
1. **Caching:** Cache rendered markdown in Redis (TTL 1 hour)
2. **Streaming:** Use `StreamingResponse` for very large documents
3. **Markdown Variants:** Add `?format=text` for plain text extraction
4. **Table of Contents:** Return TOC in separate endpoint
5. **Search Within Document:** Full-text index on rendered markdown
6. **Image Extraction:** Separate endpoint for `doc.docling.json?image={id}`

### Not in F-022 Scope
- VLM-enhanced documents (Phase 2, F-102)
- Image descriptions (Phase 2)
- Internationalization (future)
- Webhook notifications (future)

## 10. Code Quality Checklist

Before submitting F-022 for review:

- [ ] All DB operations use AsyncSession (no sync queries)
- [ ] All S3 operations are awaited (no blocking I/O)
- [ ] Owner-scoping returns 404 for both "not found" AND "not accessible"
- [ ] Error messages don't leak internal details
- [ ] Tests mock all external dependencies (DB, S3)
- [ ] Response Content-Type is `text/markdown` (not `application/json`)
- [ ] 404 responses return valid HTTPException (not raise ValueError)
- [ ] No hardcoded S3 keys (use config settings)
- [ ] No raw httpx/boto3 calls (only through dependencies)
- [ ] Tests cover: 200, 404 (not found), 404 (no access), 401, 500
- [ ] Markdown output is valid (e.g., headings start with #)

Generated: 2026-05-26
