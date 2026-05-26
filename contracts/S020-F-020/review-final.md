═══════════════════════════════════════════════════════════════════════════════
S020-F-020 VERIFICATION REPORT
═══════════════════════════════════════════════════════════════════════════════

SPRINT ID:        S020-F-020
FEATURE ID:       F-020
FEATURE TITLE:    List document variants: GET /api/sources/{source_id}/documents
                  returns all document_variant rows for that source

DATE:             2026-05-26
VERIFIER ROLE:    verifier
COMMIT HEAD:      d50e6fe (feat(api): F-020 GET /api/sources/{source_id}/documents — list document variants)

═══════════════════════════════════════════════════════════════════════════════
VERIFICATION CRITERIA
═══════════════════════════════════════════════════════════════════════════════

From feature_list.json:

  Criterion 1 (V1):
    After a successful extraction, GET /api/sources/{id}/documents returns 
    array with 1 item containing:
      • extractor_name
      • extractor_version
      • storage_prefix
      • is_canonical
      • materialized_at

  Criterion 2 (V2):
    GET /api/sources/99999/documents returns 404

═══════════════════════════════════════════════════════════════════════════════
LAYERED CHECKS EXECUTION
═══════════════════════════════════════════════════════════════════════════════

LAYER 1: SMOKE CHECK
─────────────────────────────────────────────────────────────────────────────

Command: bash verify/checks.sh smoke

Results:
  ✓ C1 API health — OK
  ✓ C2 DB connection (via FastAPI lifespan) — OK
  ✓ C3 MinIO connectivity — OK
  ✓ C4 Dagster connectivity — OK

Exit code: 0 (PASS)

─────────────────────────────────────────────────────────────────────────────

LAYER 2: BACKEND CHECK
─────────────────────────────────────────────────────────────────────────────

Command: bash verify/checks.sh backend

Sub-steps:
  1. Linter (ruff check):
     → All checks passed! (exit 0)
     
  2. Type checker (mypy):
     → Success: no issues found in 28 source files (exit 0)
     
  3. Unit tests (pytest):
     → 109 passed, 1 deselected, 1 warning (exit 0)

Key F-020 Tests (6/6 PASS):
  ✓ test_list_documents_returns_200_with_items
    Assertion: GET /7/documents with 1 variant → 200, array[1], 5 required fields
    present + correct extractor_name/version values
    
  ✓ test_list_documents_item_fields_match_model
    Assertion: All 10 DocumentVariantRead fields (id, extractor_name, 
    extractor_version, config_hash, storage_prefix, page_count, image_count,
    is_canonical, materialized_at, dagster_run_id) present with correct values
    
  ✓ test_list_documents_returns_empty_list_when_no_variants
    Assertion: Source exists but no variants → 200 with []
    
  ✓ test_list_documents_source_not_found_returns_404
    Assertion: GET /99999/documents → 404 (V2 unit test)
    
  ✓ test_list_documents_other_owners_source_returns_404
    Assertion: Source owned by another user → 404 (access control)
    
  ✓ test_list_documents_no_token_returns_401
    Assertion: GET without Authorization header → 401 (auth gate)

Exit code: 0 (PASS)

─────────────────────────────────────────────────────────────────────────────

LAYER 3: DOCUMENTS INTEGRATION CHECK
─────────────────────────────────────────────────────────────────────────────

Command: bash verify/checks.sh documents

Setup:
  1. Mint Bearer token (admin@example.com / testpassword123)
  2. Upload minimal PDF source → SRC_ID=8
  3. Trigger extract_mineru backfill → id=fxqgcmnm
  4. Poll to COMPLETED_SUCCESS (took 12 iterations, ~6s)

V1 Integration Test:
  Command: GET /api/sources/8/documents (with Bearer token)
  
  Response status: 200 (HTTP OK)
  Response body: JSON array
  Array length: 1
  Array[0].extractor_name: "mineru" ✓
  Array[0].extractor_version: "0.1.0" ✓
  Array[0].storage_prefix: populated ✓
  Array[0].is_canonical: True (boolean) ✓
  Array[0].materialized_at: non-null (timestamp) ✓
  
  Assertion: PASS

V2 Integration Test:
  Command: GET /api/sources/99999/documents (with Bearer token)
  
  Response status: 404 (HTTP Not Found)
  
  Assertion: PASS

Exit code: 0 (PASS)

═══════════════════════════════════════════════════════════════════════════════
IMPLEMENTATION VERIFICATION
═══════════════════════════════════════════════════════════════════════════════

Files Changed (per agreed.md §2):

  ✓ apps/api/dataplat_api/schemas/sources.py
    • DocumentVariantRead schema added with 10 fields (id, extractor_name, 
      extractor_version, config_hash, storage_prefix, page_count, image_count,
      is_canonical, materialized_at, dagster_run_id)
    • ConfigDict(from_attributes=True) for SQLAlchemy ORM → Pydantic mapping
    • Module docstring updated with F-020 reference

  ✓ apps/api/dataplat_api/routers/sources.py
    • GET /{source_id}/documents handler added
    • 2-step owner-scoping logic (existence check → access control)
    • Route registered after POST /upload, before GET /{id} catch-all
    • Handler: async def list_document_variants(source_id, current_user, session)
    • Response model: list[DocumentVariantRead]
    • Module docstring updated with F-020 reference

  ✓ apps/api/tests/test_documents_list.py
    • NEW file with 6 comprehensive unit tests
    • Tests cover: happy path (1 variant, 0 variants), 404 paths, auth gate
    • Uses AsyncMock(session.execute) with 2-stage query pattern

  ✓ packages/api-types/openapi.json
    • Regenerated by make codegen
    • New path /api/sources/{source_id}/documents with GET operation
    • Response component: DocumentVariantRead (10 properties, all required=false for nullables)
    • Committed in same commit as code changes (Invariant #6)

═══════════════════════════════════════════════════════════════════════════════
INVARIANT COMPLIANCE (per CLAUDE.md §1.2 + §11.7)
═══════════════════════════════════════════════════════════════════════════════

  Invariant #1 (Lineage mandatory)
    Status: N/A
    Notes: Read-only endpoint; no data written, no lineage needed.

  Invariant #2 (Storage separation + CAS)
    Status: N/A
    Notes: Read-only endpoint; no storage writes. Handler reads from Postgres only.

  Invariant #3 (Schema frozen post-publish)
    Status: N/A
    Notes: No schema publish occurs. DocumentVariantRead is a response schema, 
           not a repo schema.

  Invariant #4 (LLM calls via gateway)
    Status: N/A
    Notes: No LLM calls in F-020.

  Invariant #5 (Async SQLAlchemy from day one)
    Status: ✓ SATISFIED
    Notes: Handler is async def. Session is AsyncSession. Both session.execute() 
           calls are awaited. No sync sessions or session.query(). Consistent 
           with existing handlers.

  Invariant #6 (OpenAPI ↔ TS type sync)
    Status: ✓ SATISFIED
    Notes: Route + schema changes openapi.json. make codegen run. 
           packages/api-types/ diff committed in SAME commit as code.

═══════════════════════════════════════════════════════════════════════════════
EXIT CODES (GROUND TRUTH)
═══════════════════════════════════════════════════════════════════════════════

  Smoke check:   exit 0
  Backend layer: exit 0
  Documents layer: exit 0

═══════════════════════════════════════════════════════════════════════════════
FINAL VERDICT
═══════════════════════════════════════════════════════════════════════════════

✓✓✓ PASS ✓✓✓

All verification criteria met:
  ✓ Criterion 1 (V1): After extraction, GET /api/sources/{id}/documents 
    returns 200 with array[1] containing all 5 required fields with correct values
  ✓ Criterion 2 (V2): GET /api/sources/99999/documents returns 404

All layers pass with exit code 0:
  ✓ Smoke (infrastructure OK)
  ✓ Backend (6 F-020 unit tests + 103 other tests + linting + typing all pass)
  ✓ Documents (integration end-to-end: upload + extract + list endpoint works)

Implementation fully complies with agreed.md contract (§1–§7).

Sprint S020-F-020 is ready for:
  • Feature flag flip to passes: true in spec/feature_list.json
  • Progress journal entry (closing)
  • Git push to origin

═══════════════════════════════════════════════════════════════════════════════
