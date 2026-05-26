# F-022 Document Render Endpoint — Complete Infrastructure Investigation

**Created:** 2026-05-26  
**Feature:** GET /api/documents/{variant_id}/render  
**Status:** Investigation phase (ready for proposal.md planning)

This file contains the complete infrastructure investigation results. For implementation details, see the three detailed reports linked below.

## Reports Generated

1. **[Full Investigation Report](INVESTIGATION_F022_REPORT.md)** — Complete 13-section deep dive covering:
   - Document models & schema
   - Existing routers & patterns
   - MinIO/S3 storage layout
   - DoclingDocument handling
   - Test patterns
   - Verification infrastructure
   - Hard invariants

2. **[Quick Reference Guide](INVESTIGATION_F022_QUICK_REFERENCE.md)** — Code snippets and essentials:
   - Key file locations
   - Essential code patterns (copy-paste ready)
   - Data flow diagram
   - Response examples
   - Imports needed
   - Files to create/modify

3. **[Architecture & Design Insights](INVESTIGATION_F022_ARCHITECTURE.md)** — Deep architectural understanding:
   - Dependency chains
   - Key patterns (metadata separation, owner-scoping, async-first, etc.)
   - Query optimization
   - S3 path construction
   - Testing strategy (6 test cases)
   - Error scenarios
   - Code quality checklist

## Executive Summary

### What Needs to Be Built

**Endpoint:** `GET /api/documents/{variant_id}/render`
- **Input:** DocumentVariant ID (URL path parameter)
- **Auth:** Required (JWT bearer token)
- **Output:** Markdown representation of DoclingDocument
- **Content-Type:** `text/markdown`

### Key Implementation Points

1. **Query DocumentVariant by ID** → validate existence
2. **Owner-scoping check** → via Source.collection_id → SourceCollection.owner_id
3. **S3 path construction** → `{variant.storage_prefix}doc.docling.json`
4. **Fetch + deserialize** → JSON → DoclingDocument → Markdown
5. **Return custom response** → `Response(content=markdown, media_type="text/markdown")`

### Error Handling

| Case | Status | Message |
|------|--------|---------|
| Variant not found | 404 | "Document variant not found" |
| No access to source | 404 | "Document variant not found" (prevent enumeration) |
| S3 object missing | 404 | "Document not found in storage" |
| No auth token | 401 | "Not authenticated" |
| Invalid JSON | 500 | Server error |

### Database Context

**Table:** `document_variant` (8 fields relevant to F-022)
```sql
CREATE TABLE document_variant (
  id BIGSERIAL PRIMARY KEY,
  source_id BIGINT REFERENCES source(id) ON DELETE CASCADE,
  extractor_name TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  storage_prefix TEXT NOT NULL,              -- Key: "s3://documents/{source_id}/{extractor}/"
  page_count INT,
  image_count INT,
  is_canonical BOOLEAN DEFAULT FALSE,
  materialized_at TIMESTAMPTZ DEFAULT NOW(),
  dagster_run_id TEXT,
  UNIQUE (source_id, extractor_name, config_hash),
  UNIQUE (source_id) WHERE is_canonical
);
```

### MinIO Storage Layout

```
s3://documents/                               # Bucket
  {source_id}/
    extract_mineru/
      doc.docling.json                        # ← TARGET FILE
      images/
        0.png
        1.jpg
      manifest.json
```

**F-022 will retrieve:** `s3://documents/{source_id}/{extractor}/doc.docling.json`

## Files Summary

### To Create (2 files)
```
apps/api/dataplat_api/routers/documents.py           # Main route handler
apps/api/tests/test_documents_render.py              # Unit tests (6+ cases)
```

### To Modify (6-8 files)
```
apps/api/dataplat_api/main.py                        # Wire documents_router
apps/api/dataplat_api/config.py                      # Add bucket name (if needed)
docker/docker-compose.dev.yml                        # Ensure env vars set
docker/.env.example                                  # Document bucket
verify/checks.sh                                     # Add documents) layer
apps/api/pyproject.toml                              # Add deps if using docling-core
apps/api/uv.lock                                     # Regenerate after deps
packages/api-types/openapi.json                      # Auto-generated after route
```

### To Reference (Read-Only)
```
docs/data_platform_design.md                         # §4.1 schema, §4.3 storage
spec/feature_list.json                               # F-022 definition
apps/api/dataplat_api/routers/sources.py             # Copy ownership-scoping pattern
apps/api/dataplat_api/storage/s3.py                  # S3 client usage
apps/api/tests/test_documents_set_canonical.py       # Mock patterns
```

## Dependencies

### Already Available
- `fastapi==0.115.12` — routing, responses
- `sqlalchemy[asyncio]==2.0.41` — async DB
- `asyncpg==0.30.0` — postgres driver
- `aioboto3==15.5.0` — async S3 client
- `httpx==0.28.1` — (if needed for testing)

### Candidates to Add
- `docling-core>=1.0.0` — Lightweight DoclingDocument model
  - Alternative: manual JSON parsing (no new deps)
  - NOT: `docling` (too heavy, includes PDF extraction)

## Architecture Overview

```
┌─────────────────────────────────────────┐
│ GET /api/documents/{variant_id}/render  │
└──────────────────┬──────────────────────┘
                   ↓
    ┌──────────────────────────────────┐
    │ 1. Auth Check (get_current_user) │
    └──────────────────┬───────────────┘
                       ↓
    ┌────────────────────────────────────────┐
    │ 2. Query DocumentVariant from Postgres │
    └──────────────────┬─────────────────────┘
                       ↓
    ┌────────────────────────────────────────┐
    │ 3. Check Ownership (LEFT JOIN pattern) │
    └──────────────────┬─────────────────────┘
                       ↓
    ┌────────────────────────────────────────┐
    │ 4. Fetch doc.docling.json from MinIO   │
    └──────────────────┬─────────────────────┘
                       ↓
    ┌────────────────────────────────────────┐
    │ 5. Deserialize & Render to Markdown    │
    └──────────────────┬─────────────────────┘
                       ↓
    ┌────────────────────────────────────────┐
    │ 6. Return Response(content=md,         │
    │    media_type="text/markdown")         │
    └────────────────────────────────────────┘
```

## Hard Invariants to Follow

From CLAUDE.md — these MUST be respected:

1. **Storage separation + CAS** — Content in MinIO (by storage_prefix), metadata in Postgres ✓
2. **Async SQLAlchemy from day one** — All DB ops use AsyncSession ✓
3. **OpenAPI ↔ TS type sync** — After API change, run `make codegen` ✓
4. **No enumeration leaks** — Return 404 for both "not found" AND "not accessible" ✓

## Testing Coverage (6 Test Cases)

All tests use mocked session + S3 (no live DB/MinIO required):

1. **Happy path (200)** — Valid variant, owned by user, returns markdown
2. **Variant not found (404)** — Returns 404 "Document variant not found"
3. **No access (404)** — Variant exists but owned by different user (same error message as #2)
4. **S3 missing (404)** — Variant exists but document not in storage
5. **Invalid JSON (500)** — Malformed JSON in S3 object
6. **No auth (401)** — Missing Bearer token returns 401

## Design Decisions Captured

### Why AsyncSession Only?
Hard Invariant #5 from CLAUDE.md — all FastAPI code uses async SQLAlchemy to avoid blocking I/O.

### Why 404 for Both "Not Found" AND "Not Accessible"?
Prevents enumeration attacks — attackers can't distinguish between non-existent resources and those they can't access.

### Why Multiple Queries vs. Complex JOIN?
Existing codebase pattern (sources.py line 373) uses LEFT JOIN + OR logic for clarity. Minor performance cost is negligible on indexed fields.

### Why Not Cache Markdown?
F-022 is Phase 1 (MVP). Caching deferred to Phase 2 (can add Redis later without API changes).

## Next Steps

1. Draft `contracts/S{NN}-F-022/proposed.md` using these investigation results
2. Get Mode A approval from reviewer
3. Implement per agreed.md
4. Add unit tests covering all 6 cases
5. Wire router in main.py
6. Update verify/checks.sh with documents) layer
7. Regenerate OpenAPI + TS types with `make codegen`
8. Submit for Mode B review
9. Run verifier checks
10. Flip `F-022 passes: true` in spec/feature_list.json

## Key Files to Reference During Implementation

**Must Read (In Order):**
1. apps/api/dataplat_api/routers/sources.py (lines 370-382) — Copy ownership-scoping pattern
2. apps/api/dataplat_api/storage/s3.py — S3 client pattern
3. apps/api/tests/test_documents_set_canonical.py — Mock pattern
4. INVESTIGATION_F022_QUICK_REFERENCE.md — Code snippets

**Must Understand:**
- DocumentVariant model (db/models.py)
- DagsterGateway pattern (dagster/gateway.py) — reference only, don't use for F-022
- Auth dependencies (auth/dependencies.py)
- AsyncSession usage (db/session.py)

Generated: 2026-05-26
