# S011-F-011 — Mode B Review (Post-Implementation)

**Reviewer:** independent reviewer (Claude)
**Date:** 2026-05-25
**Commit reviewed:** `8465e22` (parent `5919032`)
**Agreed contract:** `contracts/S011-F-011/agreed.md`
**Diff scope:** `apps/api/`, `verify/checks.sh`, `packages/api-types/openapi.json`

---

## Calibration sweep (CAL-1 through CAL-11)

- **CAL-1 (Async session):** PASS — `routers/sources.py:182` `session.add(source)` is synchronous (correct for AsyncSession 2.x). `routers/sources.py:186` `await session.flush()` — awaited. `routers/sources.py:207` `await session.commit()` — awaited. No `session.query()` anywhere in the diff. The pre-existing `create_collection` handler's `await session.rollback()` at line 108 is within a `try/except IntegrityError` block from a prior sprint and is correct for that handler; the new `upload_source` handler (lines 128-210) contains no `try/except` and no `session.rollback()`.

- **CAL-2 (LLM gateway):** N/A — no LLM imports in any changed file. Confirmed by grep across `storage/`, `schemas/sources.py`, `routers/sources.py`.

- **CAL-3 (OpenAPI sync):** PASS — `packages/api-types/openapi.json` is present in `git show 8465e22 --stat` (91 lines added). The diff adds the `/api/sources/upload` POST operation at the correct path and adds the `SourceUploadResponse` schema and `Body_upload_source_api_sources_upload_post` schema to `components/schemas`. Same commit as `schemas/sources.py` and `routers/sources.py`.

- **CAL-4 (Lineage completeness):** N/A — `source` row created, not a Commit or DocumentVariant. No lineage columns on `Source` model. Invariant #1 does not apply.

- **CAL-5 (CAS path discipline):** PASS — storage key is `sources/{source.id}/original.pdf` (id-keyed), per design doc line 252. `sha256` is computed (`routers/sources.py:161`) and stored on the ORM object (`sha256=sha256_hex` at line 175), satisfying the integrity-tracking requirement. Raw bytes are never stored in Postgres. CAS applies only to processed artifacts; this path is correct per spec.

- **CAL-6 (Schema freeze post-publish):** N/A — no Silver/Gold commit, no existing schema modified in place.

- **CAL-7 (Bronze faithfulness):** N/A — no Bronze adapter.

- **CAL-8 (MVP scope discipline):** PASS — no Celery, no OAuth, no MFA, no Docker-in-Docker, no granular ACL, no self-registration. `dagster_partition_key` is set on the ORM row (`routers/sources.py:192`) but no Dagster GraphQL call is made — that is correctly deferred to F-012.

- **CAL-9 (Plugin isolation):** N/A — no plugin code.

- **CAL-10 (Test coverage):** PASS — 14 tests covering all four F-011 verification criteria, auth gate, 415/422 edge cases, atomicity (S3 failure → no commit), and flush-before-S3 ordering.

- **CAL-11 (Bias check):** Applied — specific file:line evidence cited for every check below. One NIT found and reported.

---

## Contract criteria (from agreed.md)

### §4 Flush-then-set ordering and uuid4 temp token

PASS — verified at `routers/sources.py:142-210`.

Exact sequence as prescribed:
1. `file.content_type` check at line 153 — raises 415 before any DB or byte work.
2. `await file.read()` at line 160.
3. `hashlib.sha256(content).hexdigest()` at line 161.
4. `Source(... storage_uri="__pending__", dagster_partition_key=f"src_tmp_{uuid.uuid4().hex}")` at lines 172-181. Both `import hashlib` (line 15) and `import uuid` (line 16) present.
5. `session.add(source)` at line 182 — synchronous.
6. `await session.flush()` at line 186 — awaited, no commit yet.
7. `source.storage_uri = f"s3://sources/{source.id}/original.pdf"` at line 191.
8. `source.dagster_partition_key = f"src_{source.id}"` at line 192.
9. `await s3.put_object(...)` at lines 198-203 — BEFORE commit.
10. `await session.commit()` at line 207.
11. Return `SourceUploadResponse(id=source.id, storage_uri=source.storage_uri)` at line 210.

No `try/except` wraps the S3 call. No `session.rollback()` is called in `upload_source`. The handler comment at lines 194-196 explicitly documents this: "Do NOT wrap in a swallowing try/except; do NOT call session.rollback()."

### §3-D3 Anti-orphan atomicity (S3 before commit)

PASS — S3 `put_object` at line 198 precedes `session.commit()` at line 207. A failed `put_object` propagates the exception; `get_session()` context manager calls `session.close()` (not rollback), the uncommitted transaction is implicitly rolled back by the pool. No committed DB row without a corresponding MinIO object.

### §4 Status codes

PASS:
- 201: happy path confirmed via `status_code=status.HTTP_201_CREATED` on decorator (line 125).
- 415: `HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, ...)` at lines 154-157.
- 401: handled by `Depends(get_current_user)` at line 131 — unchanged from F-008.
- 422: FastAPI auto-generates for missing `File(...)` field.

### §3-D8 Response schema

PASS — `SourceUploadResponse` in `schemas/sources.py:15-26` contains exactly `id: int` and `storage_uri: str`, no additional fields. Minimal per contract.

### §3-D1/D7 Storage module and testability

PASS — `storage/s3.py` implements `get_s3_client()` as an async generator dependency (line 29). Per-request `aioboto3.Session()` at line 43. `endpoint_url=f"http://{settings.MINIO_ENDPOINT}"` at line 46. Tests override via `app.dependency_overrides[get_s3_client]`.

### §3-D2 Config additions

PASS — `config.py:40-43` adds all four settings with correct soft defaults matching compose. The docstring at lines 10-15 correctly documents which are compose-injected and which rely on Python defaults.

### §3-D5 dagster_partition_key format

PASS — `source.dagster_partition_key = f"src_{source.id}"` at line 192. Matches the DECIDED format from §3-D5.

### §9 (D3 comment) original_name fallback

PASS — `original_name = file.filename or "upload.pdf"` at line 163. Empty/None filename falls back to `"upload.pdf"`.

### §4 optional collection_id

PASS — `collection_id: int | None = Form(default=None)` at line 130. Stored directly at line 178. No FK existence check (acceptable for MVP per contract §3-D10).

### §6 Test plan — all four F-011 verification criteria

| Criterion | Unit test | Verdict |
|---|---|---|
| V1: 201 + `{id, storage_uri}` shape | `test_upload_pdf_returns_201_with_id_and_storage_uri` (line 157), `test_upload_pdf_storage_uri_shape` (line 176) | PASS |
| V2: file exists in MinIO (S3 put_object called correctly) | `test_upload_pdf_s3_put_object_called` (line 335) | PASS |
| V3: sha256 on row == sha256 of bytes | `test_upload_pdf_sha256_on_session_add_object` (line 219) asserts `captured[0].sha256 == _MINIMAL_PDF_SHA256` | PASS |
| V4: kind='file', mime_type='application/pdf' | `test_upload_pdf_kind_file_mime_pdf_set` (line 256) | PASS |

checks.sh `sources)` layer (lines 554-1087 in the diff):
- UPLOAD-V1: curl POST → 201 + regex + id/uri consistency check.
- UPLOAD-V2: `docker compose exec fastapi python` → `boto3.head_object`.
- UPLOAD-V3: `psql SELECT sha256` compared to computed `EXPECTED_SHA256`.
- UPLOAD-V4: `psql SELECT kind, mime_type` with `grep -q`.

All four criteria are covered by both unit tests and integration checks.

### §7 checks.sh `all)` chain

PASS — `verify/checks.sh` lines 663-674 show the full 12-entry chain with `bash "$0" sources` inserted between `bash "$0" collections` and `bash "$0" buckets`.

### Hard Invariant #6 — OpenAPI in same commit

PASS — `packages/api-types/openapi.json` appears in `git show 8465e22 --stat` (91 lines added). The diff adds `/api/sources/upload` POST operation with `SourceUploadResponse` and `Body_upload_source_api_sources_upload_post` schemas. Regen command confirmed as the established `cd apps/api && uv run python -c 'import json; ...'` pattern.

---

## Implementer deviation assessment

### D1: 13 → 14 tests (sha256 test split)

`test_upload_pdf_sha256_computed_correctly` (line 195) was split into that test (which now only verifies 201 and id, effectively a structural smoke check) plus `test_upload_pdf_sha256_on_session_add_object` (line 219) which performs the actual sha256 capture-and-assert via a `captured` list capturing the ORM object. The latter test directly asserts `captured[0].sha256 == _MINIMAL_PDF_SHA256` at line 253. This is additive coverage — the sha256 assertion is now more robust (direct ORM object inspection) than the original plan. The module docstring header lists 13 names (the original agreed list) but 14 functions exist; the 14th (`test_upload_pdf_sha256_on_session_add_object`) is clearly labelled and correctly covers V3. **Approved deviation.**

### D2: `raise_server_exceptions=False` in `test_upload_s3_failure_does_not_commit`

`test_upload_s3_failure_does_not_commit` (line 493) uses `TestClient(app, raise_server_exceptions=False)` so the unhandled `Exception("MinIO down")` surfaces as HTTP 500 in the test response rather than re-raising through the test runner. The handler code itself is unchanged — there is no `try/except` around the S3 call and no `session.rollback()`. The test correctly asserts `response.status_code == 500` and `captured_sessions[0].commit.assert_not_called()`. **Approved deviation** — test-infrastructure-only change; handler behaviour is exactly as contracted.

---

## Additional findings

### Finding 1 — NIT: Comment inaccuracy in test file line 53

`apps/api/tests/test_sources_upload.py:53`: the comment reads:

```
# Byte-identical to the checks.sh fixture (both use %%EOF = literal %EOF).
```

In a Python byte literal, `%%EOF` produces two literal `%` characters — the byte sequence `%%EOF`, not `%EOF`. The comment says "literal `%EOF`" (one percent sign) when it should say "literal `%%EOF`" (two percent signs, which is also the correct PDF end-of-file marker). The actual bytes in both fixtures are correct and byte-identical; this is a comment-only inaccuracy with no runtime impact. **NIT — no code change required.**

---

## DECISION: APPROVED

**Rationale per criterion:**

- **Flush-then-set ordering:** Exact 12-step sequence from agreed.md §4 implemented at `routers/sources.py:152-210`. uuid4().hex temp token at line 180. No `try/except` swallowing, no explicit `rollback()`.
- **Anti-orphan atomicity:** S3 upload at line 198 precedes commit at line 207. Exception propagation is the only path out.
- **F-011 V1 (201 + shape):** 201 status code on decorator; response schema is `{id: int, storage_uri: str}`; shape verified by two unit tests and UPLOAD-V1 in checks.sh.
- **F-011 V2 (file in MinIO):** `put_object` called with correct Bucket/Key/Body/ContentType; verified by `test_upload_pdf_s3_put_object_called` and UPLOAD-V2 `head_object` check.
- **F-011 V3 (sha256 matches):** sha256 computed at line 161, stored on ORM object at line 175; verified by `test_upload_pdf_sha256_on_session_add_object` (direct capture) and UPLOAD-V3 psql check.
- **F-011 V4 (kind='file', mime_type):** hard-set at lines 173-177; verified by `test_upload_pdf_kind_file_mime_pdf_set` and UPLOAD-V4 psql check.
- **Invariant #2 (CAS non-conflict):** id-keyed path `sources/{id}/original.pdf`; sha256 in Postgres; bytes in MinIO only.
- **Invariant #5 (async SQLAlchemy):** `flush` and `commit` awaited; `add` synchronous; no `session.query()`.
- **Invariant #6 (OpenAPI sync):** `openapi.json` regenerated and committed in same commit `8465e22`; contains `/api/sources/upload` operation and `SourceUploadResponse` schema.
- **Scope discipline:** No F-012/F-013/F-014 logic. `dagster_partition_key` stored on row, not registered with Dagster.
- **Both implementer deviations** are additive/test-only and approved.
- **One NIT** (comment inaccuracy at test line 53) has no runtime impact.
