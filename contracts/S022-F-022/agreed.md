# S022-F-022: GET /api/documents/{variant_id}/render — Document Preview

**Feature**: Render document preview: GET /api/documents/{variant_id}/render returns a markdown representation of the DoclingDocument for display in the UI.

**Status**: PROPOSED

**Dependencies**: F-019 (extract_mineru) — PASSED ✓

---

## Overview

Users need a way to preview the extracted document content in the UI without downloading the full DoclingDocument JSON or navigating to MinIO directly. This endpoint returns a **markdown rendering** of a `DocumentVariant`, suitable for display in a browser-based preview pane.

### Verification criteria (from feature_list.json)

1. ✓ `GET /api/documents/{variant_id}/render` returns 200 with `Content-Type: text/markdown` and non-empty body
2. ✓ The rendered markdown contains text extracted from the source PDF
3. ✓ `GET /api/documents/99999/render` returns 404

---

## What changes

### Files to create

1. **`apps/api/dataplat_api/schemas/documents.py`** (NEW)
   - Pydantic schema for the markdown response (or use plain `str` since we're returning raw markdown with special Content-Type)

2. **`apps/api/dataplat_api/routers/documents.py`** (NEW)
   - Router with single endpoint: `GET /api/documents/{variant_id}/render`
   - Implements ownership-scoping check
   - Fetches DoclingDocument from MinIO
   - Renders to markdown
   - Returns markdown text with `Content-Type: text/markdown`

3. **`apps/api/tests/test_documents_render.py`** (NEW)
   - Unit tests:
     - `test_render_returns_200_with_markdown_content_type`
     - `test_render_contains_extracted_text`
     - `test_render_nonexistent_variant_returns_404`
     - `test_render_requires_auth_returns_401`
     - `test_render_retrieves_docling_document_from_s3`

### Files to modify

1. **`apps/api/dataplat_api/main.py`**
   - Import and wire the new `documents` router

2. **`apps/api/dataplat_api/db/models.py`**
   - *Optional:* Add a relationship or accessor method on `DocumentVariant` if needed (likely not required — route just needs `source_id` for ownership check)

### Files unchanged

- `apps/api/dataplat_api/routers/sources.py` — Keep as-is; this router is separate
- `apps/api/dataplat_api/schemas/sources.py` — Keep; reuse or extend if needed
- Database migrations — No schema changes; we're just reading existing tables and MinIO

---

## Implementation notes

### Ownership-scoping pattern (critical)

The endpoint MUST validate that the caller can access the document variant. Use the same LEFT JOIN ownership check as F-020 and F-021:

```python
# Step 1: Find the variant and validate ownership
result = await session.execute(
    select(DocumentVariant)
    .join(Source)
    .join(SourceCollection, Source.collection_id == SourceCollection.id, isouter=True)
    .where(DocumentVariant.id == variant_id)
    .where(
        or_(
            SourceCollection.owner_id == current_user.id,
            Source.collection_id.is_(None),
        )
    )
)
variant = result.scalar_one_or_none()
if variant is None:
    raise HTTPException(status_code=404, detail="Document variant not found")
```

Return 404 for both "not found" and "not accessible" (prevents enumeration).

### MinIO retrieval

- Construct the S3 key from `variant.storage_prefix` + `"doc.docling.json"`
  - Example: `s3://documents/7/extract_mineru/` → key = `documents/7/extract_mineru/doc.docling.json`
- Use the same `get_s3_client` dependency as other endpoints
- Deserialization: use `json.loads()` to parse the JSON file

### DoclingDocument to Markdown rendering

**Strategy**: Use the `docling` library (already a dependency from F-019).

The `DoclingDocument` object has a `.to_markdown()` method (or similar text export). Fallback if not available:
- Iterate over document structure (sections, tables, lists, images)
- Linearize to markdown format
- Include text + structural hints (headers, code blocks, tables as markdown)
- *Skip raw images* (they're binary; use image placeholders like `[Image 0]` or drop them entirely)

**Example logic (pseudocode)**:

```python
from docling.models import DoclingDocument
import json

# 1. Load DoclingDocument from S3
docling_json = await s3.get_object(Bucket=bucket, Key=key)
doc_dict = json.loads(docling_json['Body'].read())
doc = DoclingDocument.model_validate(doc_dict)

# 2. Render to markdown
markdown_text = doc.to_markdown()  # If method exists
# OR: markdown_text = _render_docling_to_markdown(doc)  # Custom function

# 3. Return with correct Content-Type
return Response(content=markdown_text, media_type="text/markdown")
```

If `DoclingDocument` does NOT have a built-in `to_markdown()` method, implement a custom linearizer:
- Iterate `doc.document` (root element)
- Recursively walk the node tree (TableElement, TextElement, SectionHeader, etc.)
- Emit markdown-formatted text

**No external markdown renderer needed** — keep it simple and linear for MVP.

### Response format

**Status 200**:
```
HTTP/1.1 200 OK
Content-Type: text/markdown
Content-Length: 5432

# Document Title

## Section 1
This is extracted text from the PDF...

### Subsection 1.1
More content...

[Image 0]

| Column 1 | Column 2 |
|----------|----------|
| Data     | Value    |

...
```

**Status 404**:
```json
{"detail": "Document variant not found"}
```

**Status 401**:
```json
{"detail": "Not authenticated"}
```

### Error handling

1. **Variant not found** (ownership check fails or variant doesn't exist) → 404
2. **S3 retrieval fails** (doc.docling.json missing from MinIO) → 500 (log warning — indicates a corrupt/incomplete extraction)
3. **JSON parse fails** (malformed DoclingDocument) → 500
4. **No auth token** → 401 (auth gate catches before reaching handler)

---

## Test plan

### Unit tests (backend layer, mocked S3)

1. **test_render_returns_200_with_markdown_content_type**
   - Mock session to return a valid variant + source
   - Mock S3 to return a sample DoclingDocument JSON
   - Assert response status == 200
   - Assert `Content-Type` header == `"text/markdown"`

2. **test_render_contains_extracted_text**
   - Mock a DoclingDocument with known text content
   - Render it
   - Assert response body contains the expected text substring

3. **test_render_nonexistent_variant_returns_404**
   - Mock session to return None (variant not found)
   - Assert response status == 404
   - Assert detail message

4. **test_render_requires_auth_returns_401**
   - No Authorization header
   - Assert response status == 401

5. **test_render_retrieves_docling_document_from_s3**
   - Verify that S3 `get_object()` is called with correct bucket and key
   - Verify key is constructed from `variant.storage_prefix + "doc.docling.json"`

6. **test_render_variant_not_owned_by_caller_returns_404**
   - Mock session to return None (ownership check fails)
   - Assert 404 (not 403 — no enumeration leak)

### Integration tests (not required for MVP, but good to have)

- Spin up a real document variant with MinIO (from F-019 extraction)
- Call the endpoint
- Verify rendered markdown contains expected text

---

## Implementation checklist

- [ ] Create `apps/api/dataplat_api/routers/documents.py` with GET endpoint
- [ ] Create `apps/api/dataplat_api/schemas/documents.py` (may be minimal or empty)
- [ ] Implement ownership-scoping query (LEFT JOIN + OR logic)
- [ ] Implement S3 retrieval with `get_s3_client` dependency
- [ ] Implement DoclingDocument → Markdown conversion
- [ ] Wire router in `main.py`
- [ ] Write unit tests in `apps/api/tests/test_documents_render.py`
- [ ] Run `make codegen` to sync OpenAPI schema with frontend types
- [ ] Verify `bash verify/checks.sh backend` passes
- [ ] Test auth gate (missing Authorization header → 401)
- [ ] Test 404 paths (variant not found, not owned, etc.)

---

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| DoclingDocument schema unfamiliar; rendering logic buggy | Reference docling library docs; start with simple text extraction; test with real F-019 variants |
| S3 retrieval slow for large DoclingDocuments | Markdown is typically <100 KB for most PDFs; acceptable for MVP preview. Cache in future sprint if needed. |
| Response size unbounded | No cap on markdown size for MVP; future sprint can add max-preview-lines or truncation if UI needs it. |
| Encoding issues in markdown (unicode, special chars) | Use UTF-8 throughout; FastAPI's Response() handles encoding automatically. |

---

## Success metrics

- ✓ Endpoint responds 200 with correct Content-Type
- ✓ Rendered markdown is readable and contains source text
- ✓ 404 for non-existent or inaccessible variants
- ✓ All unit tests pass
- ✓ `verify/checks.sh backend` passes
- ✓ No new Postgres schema changes (uses existing tables only)

---

## References

- Design doc §4.3 (MinIO storage layout): `s3://documents/{source_id}/{extractor}/doc.docling.json`
- Design doc §9.1 (API routes): `/api/documents/` section
- F-020 (GET list variants): Ownership-scoping pattern reference
- F-021 (POST set-canonical): Ownership-scoping pattern reference
- `docling` library: https://github.com/docling-project/docling
- FastAPI Response with custom Content-Type: https://fastapi.tiangolo.com/advanced/response-directly/

