# F-022 Investigation Documentation Index

**Investigation Date:** 2026-05-26  
**Feature:** GET /api/documents/{variant_id}/render (F-022)  
**Status:** Complete — Ready for proposed.md drafting

## Document Organization

This investigation spans 4 comprehensive reports totaling ~45KB of analysis. Choose your reading path based on your need:

### 🎯 Start Here (5 min read)
**File:** [INVESTIGATION_F022.md](./INVESTIGATION_F022.md)
- Executive summary
- What needs to be built
- Key implementation points
- Error handling matrix
- Files to create/modify
- Next steps

### 📋 For Implementation Details (15 min reference)
**File:** [INVESTIGATION_F022_QUICK_REFERENCE.md](./INVESTIGATION_F022_QUICK_REFERENCE.md)
- Essential file locations with line numbers
- Copy-paste code snippets
- Data flow diagram
- Response format patterns
- Complete imports list
- Test mocking patterns
- Verification checklist

**Use when:** Writing routers/tests and need exact code patterns

### 🏗️ For Architecture Understanding (20 min read)
**File:** [INVESTIGATION_F022_ARCHITECTURE.md](./INVESTIGATION_F022_ARCHITECTURE.md)
- Why F-022 depends on F-019 (dependency chain)
- 5 key architectural patterns in Dataplat
- Query optimization insights
- S3 path construction logic
- 6 test cases with setup/assertions
- Error scenarios table
- Future extension ideas
- Code quality checklist

**Use when:** Need to understand design decisions and testing strategy

### 📚 For Deep Research (45 min comprehensive read)
**File:** [INVESTIGATION_F022_REPORT.md](./INVESTIGATION_F022_REPORT.md)
- 13 detailed sections:
  1. Document models & schema
  2. Existing routers & endpoints
  3. Object storage (MinIO/S3) layout
  4. DoclingDocument handling
  5. Content retrieval patterns
  6. Test patterns
  7. Verification infrastructure
  8. Configuration & DI
  9. Response formats
  10. Hard invariants
  11. File checklist
  12. Dependencies to consider
  13. Architectural summary

**Use when:** Need exhaustive reference or reviewing contracts

---

## Key Findings Summary

### What Exists (Ready to Use)

| Component | Location | Status |
|-----------|----------|--------|
| DocumentVariant model | `db/models.py:111-152` | ✓ Ready |
| Source/SourceCollection | `db/models.py:72-107` | ✓ Ready |
| S3 client dependency | `storage/s3.py` | ✓ Ready (aioboto3) |
| Auth gate pattern | `auth/dependencies.py` | ✓ Ready |
| Ownership-scoping pattern | `routers/sources.py:370-382` | ✓ Ready to copy |
| Mock fixtures | `tests/conftest.py` | ✓ Ready |
| MinIO buckets | F-003 created `documents` | ✓ Ready |

### What Needs to Be Built

| Item | Complexity | Est. LOC |
|------|-----------|---------|
| documents.py router | Medium | 60-80 |
| test_documents_render.py | Medium | 150-200 |
| Schema (if any) | Low | 0-30 |
| Router wiring in main.py | Trivial | 2 |
| checks.sh layer | Low | 5-10 |

### Critical Constraints (From CLAUDE.md)

1. **Async-first:** All DB ops must use AsyncSession (no sync queries)
2. **Ownership:** Return 404 for both "not found" AND "not accessible" (prevents enumeration)
3. **Storage separation:** Metadata in Postgres, content in MinIO
4. **CodeGen:** After API change, run `make codegen` and commit OpenAPI diff

---

## Implementation Roadmap

### Phase 1: Planning (You are here)
- [x] Investigate infrastructure
- [x] Document findings (4 reports)
- [ ] Draft contracts/S{NN}-F-022/proposed.md (next step)

### Phase 2: Proposal & Feedback
- [ ] Reviewer Mode A: Give feedback on proposed.md
- [ ] Iterate until APPROVED → agreed.md

### Phase 3: Implementation
- [ ] Create apps/api/dataplat_api/routers/documents.py
- [ ] Create apps/api/tests/test_documents_render.py
- [ ] Wire router in main.py
- [ ] Add documents layer to verify/checks.sh
- [ ] Update configs/pyproject.toml

### Phase 4: Review & Verification
- [ ] Reviewer Mode B: Review implementation diff
- [ ] Verifier: Run all checks (smoke, backend, documents, all)
- [ ] Flip F-022 passes:true in spec/feature_list.json

---

## How to Use These Documents

### Scenario 1: "I'm drafting proposed.md"
**Read in order:**
1. INVESTIGATION_F022.md (overview)
2. INVESTIGATION_F022_REPORT.md §1-8 (detailed context)
3. INVESTIGATION_F022_ARCHITECTURE.md §1-5 (design rationale)

**Then write:** proposed.md with specific file changes, error handling, and verification criteria

### Scenario 2: "I'm implementing the router"
**Read in order:**
1. INVESTIGATION_F022_QUICK_REFERENCE.md (code patterns)
2. INVESTIGATION_F022_ARCHITECTURE.md §3-4 (S3/query patterns)
3. Copy from: routers/sources.py lines 370-382 (ownership pattern)

**Then code:** documents.py using exact patterns from quick reference

### Scenario 3: "I'm writing the tests"
**Read in order:**
1. INVESTIGATION_F022_ARCHITECTURE.md §6 (test cases)
2. INVESTIGATION_F022_QUICK_REFERENCE.md (mocking patterns)
3. Reference: tests/test_documents_set_canonical.py (mock fixtures)

**Then code:** 6 test cases with async mocks for session + s3

### Scenario 4: "I'm reviewing implementation"
**Read:**
1. INVESTIGATION_F022_ARCHITECTURE.md §10 (code quality checklist)
2. INVESTIGATION_F022_REPORT.md §10 (hard invariants)
3. Check against: agreed.md + diff

---

## Key Code Patterns (At a Glance)

### Ownership-Scoping Pattern
```python
result = await session.execute(
    select(Source)
    .join(SourceCollection, Source.collection_id == SourceCollection.id, isouter=True)
    .where(Source.id == source_id)
    .where(or_(SourceCollection.owner_id == current_user.id, Source.collection_id.is_(None)))
)
source = result.scalar_one_or_none()
if source is None:
    raise HTTPException(status_code=404, detail="Source not found")
```

### S3 Fetch Pattern
```python
s3_key = f"{variant.storage_prefix}doc.docling.json"
response = await s3.get_object(
    Bucket=settings.MINIO_DOCUMENTS_BUCKET,
    Key=s3_key.replace("s3://documents/", "")  # Remove scheme
)
content = await response["Body"].read()
docling_dict = json.loads(content)
```

### Custom Response Pattern
```python
from fastapi.responses import Response

return Response(
    content=markdown_string,
    status_code=200,
    media_type="text/markdown",
)
```

---

## Investigation Metrics

| Metric | Value |
|--------|-------|
| Files analyzed | 28 |
| Code locations identified | 50+ |
| Test patterns documented | 6 |
| Architecture patterns extracted | 5 |
| Error scenarios mapped | 8 |
| Dependencies checked | 15 |
| Total pages (4 docs) | ~45 |
| Time to read (all) | 45-60 min |
| Time to reference (quick) | 5-10 min |

---

## Staying in Sync with Project

These investigation documents capture the **current state** (2026-05-26) of:
- Document infrastructure
- Test patterns
- Configuration
- Dependencies

If future sprints modify:
- Schema (new document_variant columns)
- Storage layout (new buckets)
- Auth patterns (new middleware)
- Test fixtures (new conftest)

**Revisit these docs and update them.** They serve as project memory.

---

## Questions Not Answered (Out of Scope)

- [ ] Should we support rendering multiple formats (PDF, HTML, JSON)?
  - **Answer:** F-022 scope: markdown only. Future feature for phase 2.
  
- [ ] Should we cache rendered markdown?
  - **Answer:** No — F-022 is MVP. Add Redis caching in phase 2.
  
- [ ] Should we stream very large documents?
  - **Answer:** No — Use `Response` with full content. Streaming is phase 2.
  
- [ ] Should we extract images separately?
  - **Answer:** No — Out of scope for F-022. Separate endpoint in future.
  
- [ ] Should we support render-on-demand (compute if missing)?
  - **Answer:** No — Assume Dagster job completed. If missing, return 404.

---

## Contact & References

**Investigation by:** Claude (AI assistant)  
**Date:** 2026-05-26  
**Project:** Dataplat (LLM Training Data Management Platform)  
**Design doc:** `docs/data_platform_design.md` (canonical source of truth)  
**Feature:** F-022 in `spec/feature_list.json`

---

## Next Action

1. Read [INVESTIGATION_F022.md](./INVESTIGATION_F022.md) (5 min)
2. Draft `contracts/S{NN}-F-022/proposed.md` using investigation results
3. Follow the sprint workflow from CLAUDE.md §"Sprint workflow"

Good luck! The infrastructure is solid and the patterns are well-established. 🚀
