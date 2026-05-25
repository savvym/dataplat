# S011-F-011 — Proposed Contract

**Status:** PROPOSED
**Date drafted:** 2026-05-25
**Author:** Leader (Claude)
**Sprint-id:** S011-F-011

---

## §1 Objective & Scope

**Goal:** Implement `POST /api/sources/upload` — a multipart/form-data endpoint that accepts a PDF file and an optional `collection_id`, stores the file in MinIO at `s3://sources/{source_id}/original.pdf`, writes a `source` row to Postgres with the correct `sha256`, `storage_uri`, `kind='file'`, `mime_type='application/pdf'`, and returns `{"id": <int>, "storage_uri": "s3://sources/<id>/original.pdf"}` with HTTP 201.

This sprint introduces the first S3/MinIO production write path and the first `source` table row ever inserted. It also introduces the S3 client module and extends `Settings`.

### Dependency confirmation

| Dependency | Required state | Evidence |
|---|---|---|
| F-008 (auth gate) | `passes: true` | Commits `5919032` + prior; `feature_list.json` confirms. |
| F-009 (POST /api/sources/collections) | `passes: true` | Commit `594356d`; `feature_list.json` confirms. |
| F-010 (GET /api/sources/collections) | `passes: true` | Commit `2d7d93f`; `feature_list.json` confirms. |

### Explicit non-goals (out of scope for this sprint)

- F-012 (Dagster partition-ingest notification) — no job launch; source row is created but no Dagster trigger is fired.
- F-013 (GET /api/sources/{id}) — no individual-source detail route.
- F-014 (list sources in a collection) — no list endpoint.
- Non-PDF file types — only `application/pdf` is accepted in MVP.
- Content-addressed storage for source raw files — the `sources/{id}/original.pdf` key is id-keyed by design (see §3-D4).
- Compensation / cleanup logic for orphan MinIO objects — acceptable known leak for MVP (see §3-D6).
- Any migration — the `source` table and its columns already exist from a prior migration (confirmed by `migration` layer passing). No schema change is needed.

---

## §2 Files Changed

| Path | New / Modified | Summary of change |
|---|---|---|
| `apps/api/dataplat_api/config.py` | MODIFIED | Add `MINIO_ENDPOINT`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_SOURCES_BUCKET` to `Settings`. |
| `apps/api/dataplat_api/storage/s3.py` | NEW | Async S3 client module: `get_s3_client()` FastAPI dependency backed by `aioboto3`; builds client from settings. |
| `apps/api/dataplat_api/storage/__init__.py` | NEW | Empty package marker. |
| `apps/api/dataplat_api/schemas/sources.py` | NEW | `SourceUploadResponse` Pydantic schema (`id: int`, `storage_uri: str`). |
| `apps/api/dataplat_api/routers/sources.py` | MODIFIED | Add `POST /upload` handler; add imports for `File`, `Form`, `UploadFile`, `Source` ORM model, `SourceUploadResponse`, `get_s3_client`. |
| `apps/api/tests/test_sources_upload.py` | NEW | Unit tests for F-011 (listed in §6). |
| `verify/checks.sh` | MODIFIED | Add new `sources)` layer; insert it after `collections)` in the `all)` chain. |
| `packages/api-types/openapi.json` | MODIFIED | Regenerated in the same commit as schema + router changes (hard invariant #6). |

**Files NOT touched:**

- `apps/api/dataplat_api/db/models.py` — `Source` ORM model already complete; no columns added.
- `apps/api/dataplat_api/db/session.py` — no change.
- `apps/api/dataplat_api/auth/dependencies.py` — no change.
- `apps/api/dataplat_api/schemas/collections.py` — no change.
- Any migration file — `source` table already exists.

---

## §3 Design Decisions

### D1 — Storage module location and shape

New package: `apps/api/dataplat_api/storage/` with two files:
- `__init__.py` — empty, makes it a package.
- `s3.py` — contains:
  - `_make_s3_client()`: an async context manager (using `aioboto3`) that yields a boto3-compatible async S3 client. Uses `settings.MINIO_ENDPOINT`, `settings.MINIO_ROOT_USER`, `settings.MINIO_ROOT_PASSWORD`.
  - `get_s3_client()`: a FastAPI async generator dependency that wraps `_make_s3_client()` and yields the client. Tests override this dependency exactly as they override `get_session`.

The `aioboto3` session is created per-request (not a module-level singleton) to avoid resource leaks and to stay simple for MVP. A shared session pool is a future optimisation.

`endpoint_url` is constructed as `f"http://{settings.MINIO_ENDPOINT}"` — the env var `MINIO_ENDPOINT` is `minio:9000` (host:port, no scheme), matching the docker-compose injection.

### D2 — Config additions (matching existing style)

Add four new fields to `Settings` in `config.py`:

```
MINIO_ENDPOINT: str = "minio:9000"
MINIO_ROOT_USER: str = "minioadmin"
MINIO_ROOT_PASSWORD: str = "devpassword"
MINIO_SOURCES_BUCKET: str = "sources"
```

All four have defaults that match the docker-compose dev values so the service starts without requiring explicit env-var changes. In production, operators override via environment. This mirrors the existing `DAGSTER_GRAPHQL_URL` default pattern.

`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` have soft defaults (not hard-fail) because MinIO credentials are not as security-critical as `SECRET_KEY` for JWT signing; MinIO itself enforces access control. Flag as an open question if the reviewer prefers no-default (fast-fail) for credentials.

`MINIO_ENDPOINT`, `MINIO_ROOT_USER`, and `MINIO_ROOT_PASSWORD` are already injected by docker-compose.dev.yml lines 223-225 (environment block on the `fastapi` service), so no compose file change is needed for those three. `MINIO_SOURCES_BUCKET` is NOT injected by docker-compose; it relies entirely on the Python default `"sources"`, which matches the bucket name created by the minio-init one-shot service. No compose change is needed for `MINIO_SOURCES_BUCKET` either.

### D3 — The flush-then-set ordering (key design problem)

`storage_uri` and `dagster_partition_key` are NOT NULL but depend on the DB-generated `id`. The exact operation sequence is:

```
1. Read UploadFile bytes into memory; compute sha256.
2. Construct a Source ORM object with placeholder values for the two NOT-NULL
   id-dependent fields (see note below) and all other known fields.
3. session.add(source)
4. await session.flush()         ← DB assigns id; no COMMIT yet.
5. Derive storage_uri = f"s3://sources/{source.id}/original.pdf"
6. Derive dagster_partition_key = f"src_{source.id}"
7. Set source.storage_uri = storage_uri
8. Set source.dagster_partition_key = dagster_partition_key
9. Upload file bytes to MinIO at key f"sources/{source.id}/original.pdf"
   in bucket settings.MINIO_SOURCES_BUCKET.
10. await session.commit()        ← Row is now durable with correct fields.
```

**Placeholder approach for step 2:** Because `storage_uri` and `dagster_partition_key` are NOT NULL at the DB level but we must flush to get the id, we use a two-step approach: set temporary placeholder strings before `session.add()` so SQLAlchemy accepts the object, then overwrite both fields after `session.flush()` assigns the id but before `session.commit()` persists them.

- `storage_uri` has NO UNIQUE constraint, so the constant placeholder `"__pending__"` is safe under concurrent requests — multiple in-flight transactions can each hold `"__pending__"` in their own uncommitted transaction without colliding.
- `dagster_partition_key` HAS a UNIQUE constraint. A constant placeholder like `"__pending__"` would collide between concurrent uploads at flush time (each flush issues its INSERT to Postgres within the still-open transaction, where the UNIQUE index is enforced). The placeholder MUST be unique per request. Use `dagster_partition_key=f"src_tmp_{uuid.uuid4().hex}"` (requires `import uuid` at the top of the handler module). A `uuid4().hex` is cryptographically random and collision-proof. Do NOT use `id(source)` or `id(object())` — CPython reuses freed object addresses, making these unsafe under concurrent async requests.

`session.commit()` auto-flushes all dirty attributes before committing, so the overwritten `storage_uri` and `dagster_partition_key` values set in steps 7–8 persist in the same commit with no second explicit `await session.flush()` needed. The implementer MUST set both fields after flush and before commit, or the row persists with the placeholder values.

**Atomicity analysis:**
- MinIO upload at step 9, before `commit()` at step 10: if the upload fails (network error, MinIO down), the exception propagates out of the handler. `get_session()` in `db/session.py` is `async with SessionLocal() as session: yield session`. SQLAlchemy's `AsyncSession.__aexit__` calls `session.close()`, NOT `session.rollback()`. The open transaction is never committed, and it is implicitly rolled back when the connection is returned to the pool with an uncommitted transaction. No orphan DB row is created. The handler MUST NOT wrap the S3 `put_object()` call in a `try/except` that swallows the exception, and MUST NOT add an explicit `await session.rollback()` — letting the exception propagate is the correct and only required pattern.
- If `session.commit()` at step 10 fails (very rare — Postgres went away after the flush), the MinIO object at `sources/{id}/original.pdf` will persist as an orphan object. This is acceptable for MVP: flag it as a known, tolerable leak (MinIO objects are cheap; a future cleanup job can be added). No compensation logic is built in this sprint.

**Why MinIO upload before commit (not after):** The design choice to upload before commit ensures that if the upload fails, no DB row is written. The alternative (commit first, then upload) would leave an orphan DB row with a `storage_uri` pointing to a non-existent object. A DB row that points to missing content is a worse inconsistency than a MinIO object with no corresponding DB row.

### D4 — Storage path is id-keyed, not CAS. This is NOT a hard invariant violation.

Hard invariant #2 states: "content lives in MinIO/S3 addressed by `sha256(content)`." This applies to processed artifacts (`document_variant`, Silver/Gold commits). Raw source files are explicitly excluded. Design doc line 252 reads `storage_uri TEXT NOT NULL, -- s3://sources/{id}` and line 425 reads `s3://sources/  # 原始文件 (raw files)`. The `sha256` IS computed and stored in the Postgres `source.sha256` column (for integrity verification, dedup detection in future features), but the S3 key itself is id-keyed for raw uploads. This is correct per the design doc and does NOT violate invariant #2.

### D5 — dagster_partition_key format: `src_{source_id}`

DECIDED: `dagster_partition_key = f"src_{source.id}"`. Design doc §5.3 line 526 (`src_<sha256[:12]>`) is superseded by the F-012 feature_list.json text for MVP. The F-012 feature entry explicitly states the partition key is `src_{source_id}` and is authoritative. Use `src_{source.id}` (populated after `await session.flush()` assigns the id).

### D6 — Orphan MinIO objects on commit failure

If `session.commit()` fails after the MinIO upload has succeeded, the file at `sources/{id}/original.pdf` in MinIO has no corresponding DB row. This is an acceptable, known inconsistency for MVP. No delete-on-rollback compensation is implemented. Document it in code comments. Future sprints can add a reconciliation job if needed.

### D7 — S3 dependency injection for testability

`get_s3_client()` is a FastAPI async generator dependency, exactly like `get_session()`. Tests override it via `app.dependency_overrides[get_s3_client] = _mock_s3_override`. The mock S3 client is a `MagicMock` (or `AsyncMock` for async methods like `put_object`). This pattern ensures unit tests never touch real MinIO and mirrors the established `get_session` override pattern.

### D8 — Response schema shape

`SourceUploadResponse` in `schemas/sources.py` contains:
- `id: int`
- `storage_uri: str`

These two fields are the minimum required by the F-011 verification criteria. Additional fields (`sha256`, `kind`, `mime_type`, `collection_id`, `original_name`) are explicitly NOT included in this sprint's response to keep the schema minimal. F-013 (GET detail) will define the full `SourceOut` schema. `SourceUploadResponse` is a narrow, purpose-built response type for the upload endpoint only.

### D9 — MIME validation: trust caller + validate Content-Type header, reject non-PDF with 415

The handler checks `file.content_type == "application/pdf"`. If the caller sends a non-PDF content type, return HTTP 415 Unsupported Media Type with `{"detail": "Only application/pdf uploads are accepted"}`. The `mime_type` column is then hard-set to `"application/pdf"` on the DB row regardless (the caller's content_type is validated, not trusted as the column value). This is simpler than file-level sniffing (magic bytes) and appropriate for MVP. Rationale: the spec says `mime_type='application/pdf'` on the row, and rejecting at the API boundary prevents junk from reaching storage.

The `original_name` column is populated from `file.filename` (the browser-supplied filename from the multipart form). If absent (empty string or None), use `"upload.pdf"` as a fallback.

### D10 — `collection_id` parameter handling

`collection_id` is sent as a `Form(...)` field in the multipart request. It is typed as `Optional[int] = Form(default=None)`. The column is nullable in the DB (`Source.collection_id` is `Mapped[Optional[int]]`). If provided, it is stored as-is; no FK existence check is performed in MVP (a FK violation at the DB level will surface as a 500, not a 422 — this is acceptable for MVP). If the reviewer wants a 422 for invalid `collection_id`, this can be added, but it requires an extra async DB query and is out of scope per feature_list.json V1-V4.

---

## §4 Upload Handler Contract

### Signature

```
POST /api/sources/upload
Content-Type: multipart/form-data

Form fields:
  file          : UploadFile (required) — the PDF file
  collection_id : int | null (optional, default null) — FK to source_collection
```

### FastAPI route decorator

```python
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
```

The handler lives in `apps/api/dataplat_api/routers/sources.py` (same file as the collections handlers). The router prefix is `/api/sources`, so the full path is `/api/sources/upload`.

### Exact operation sequence (see also §3-D3)

1. Check `file.content_type == "application/pdf"`. If not, raise `HTTPException(status_code=415, detail="Only application/pdf uploads are accepted")`.
2. Read all bytes: `content = await file.read()`.
3. Compute `sha256_hex = hashlib.sha256(content).hexdigest()`.
4. Compute `size_bytes = len(content)`.
5. Determine `original_name = file.filename or "upload.pdf"`.
6. Construct the `Source` object with a UUID-based temporary partition key (requires `import uuid`):
     ```python
     source = Source(kind="file", original_name=original_name, sha256=sha256_hex,
                     size=size_bytes, mime_type="application/pdf",
                     collection_id=collection_id,
                     storage_uri="__pending__",
                     dagster_partition_key=f"src_tmp_{uuid.uuid4().hex}")
     session.add(source)
     await session.flush()
     ```
   `uuid.uuid4().hex` is cryptographically random and collision-proof under concurrent async requests. Do NOT use `id(source)` or `id(object())` — CPython reuses freed object addresses.
7. After `await session.flush()`: `source.id` is now populated.
8. Set `source.storage_uri = f"s3://sources/{source.id}/original.pdf"`.
9. Set `source.dagster_partition_key = f"src_{source.id}"`.
10. Upload to MinIO:
    ```python
    s3_key = f"sources/{source.id}/original.pdf"
    await s3.put_object(
        Bucket=settings.MINIO_SOURCES_BUCKET,
        Key=s3_key,
        Body=content,
        ContentType="application/pdf",
    )
    ```
    If this raises an exception, allow it to propagate — do NOT catch it or call `session.rollback()`. The `get_session()` context manager calls `session.close()` on exit; the uncommitted transaction is implicitly rolled back when the connection returns to the pool. No orphan DB row is created.
11. `await session.commit()`.
12. Return `SourceUploadResponse(id=source.id, storage_uri=source.storage_uri)`.

### Status codes

| Code | Condition |
|---|---|
| 201 | Success — source row created, file uploaded. |
| 401 | Missing or invalid Bearer token (oauth2_scheme auto_error=True). |
| 415 | `file.content_type` is not `application/pdf`. |
| 422 | FastAPI validation failure (e.g., `file` field missing from multipart body). |
| 500 | Unexpected error (S3 upload failure, Postgres error). |

No 409 is defined: the `source` table has no UNIQUE constraint on sha256 (duplicate uploads of the same file are allowed — each gets its own row and storage path).

---

## §5 Error & Edge Semantics

### Missing file field
FastAPI returns 422 automatically when `File(...)` is absent from the multipart body.

### Non-PDF content-type
Handler checks `file.content_type == "application/pdf"`. Returns 415 with a human-readable detail string. This check fires before any bytes are read or any DB row is created.

**Decision — trust caller vs. sniff:** We trust the multipart `Content-Type` header on the file part rather than sniffing magic bytes. Rationale: sniffing requires a third-party library (`python-magic` / libmagic) not currently in `pyproject.toml`, and adding it is out of scope. Content-Type checking is sufficient for MVP. The reviewer may request magic-byte sniffing; if so, flag as an OQ.

### Invalid / non-existent `collection_id`
If `collection_id` refers to a non-existent `source_collection` row, the INSERT will fail at the DB level with a foreign-key violation (IntegrityError). This surfaces as an unhandled exception → HTTP 500. A future sprint may add a 422 check. Acceptable for MVP per feature_list.json scope.

### S3 upload failure (MinIO down / network error)
The aioboto3 `put_object()` call raises an exception. The handler MUST NOT catch it — let it propagate. `get_session()` in `db/session.py` is `async with SessionLocal() as session: yield session`. SQLAlchemy's `AsyncSession.__aexit__` calls `session.close()`, NOT `session.rollback()`. The open transaction is never committed; it is implicitly rolled back when the connection returns to the pool. No DB row is committed. No orphan row exists. The handler MUST NOT add an explicit `await session.rollback()` call — propagating the exception is the correct and only required pattern. The client receives HTTP 500.

### Commit failure after S3 upload
The MinIO object at `sources/{id}/original.pdf` persists. No DB row exists. This is a known, acceptable MVP leak documented in §3-D6 and in code comments.

### Empty file
An empty PDF (`len(content) == 0`) is accepted — sha256 of empty bytes is a valid string, size=0 is valid BigInteger. The route does not validate that the file is a well-formed PDF beyond content-type. This is acceptable for MVP.

### Large files
No explicit size limit is imposed at the application level. The underlying uvicorn and FastAPI defaults apply. For MVP, we do not cap file size — a future sprint can add a `max_upload_size` setting.

---

## §6 Test Plan

All tests live in `apps/api/tests/test_sources_upload.py`. All are pure unit tests using `TestClient(app)` with the `conftest.py` autouse fixtures. No live Postgres or MinIO required.

### Mock S3 client pattern

```python
from dataplat_api.storage.s3 import get_s3_client

def _make_s3_dep() -> Any:
    """Returns a get_s3_client override with a mock async put_object."""
    async def _override():
        s3_mock = AsyncMock()
        s3_mock.put_object = AsyncMock(return_value={})
        yield s3_mock
    return _override
```

Override: `app.dependency_overrides[get_s3_client] = _make_s3_dep()`.

### Mock session pattern for upload tests

The upload handler calls: `session.add()`, `await session.flush()`, and `await session.commit()`. It does NOT call `session.refresh()`. The `flush()` side effect must populate `source.id`. Pattern:

```python
def _make_session_dep(flush_id: int = 7) -> Any:
    async def _override():
        session = AsyncMock()
        session.add = MagicMock()

        def _flush_side_effect():
            # find the Source object that was added and set its id
            added_obj = session.add.call_args[0][0]
            added_obj.id = flush_id
        session.flush = AsyncMock(side_effect=_flush_side_effect)
        session.commit = AsyncMock()
        yield session
    return _override
```

### Minimal valid PDF fixture (inline, no binary committed)

```python
_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type /Pages /Kids[3 0 R] /Count 1>>endobj\n"
    b"3 0 obj<</Type /Page /MediaBox[0 0 612 792] /Parent 2 0 R>>endobj\n"
    b"xref\n0 4\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000115 00000 n \n"
    b"trailer<</Size 4 /Root 1 0 R>>\n"
    b"startxref\n182\n%%EOF\n"
)
```

The bytes are constructed inline in the test file as a module-level constant — no binary fixture file is committed.

### Test table

| Test name | Maps to F-011 criterion | What it asserts |
|---|---|---|
| `test_upload_pdf_returns_201_with_id_and_storage_uri` | V1 | POST with `_MINIMAL_PDF` returns 201; body has `id` (int) and `storage_uri == f"s3://sources/{id}/original.pdf"`. |
| `test_upload_pdf_storage_uri_shape` | V1 | `storage_uri` matches regex `^s3://sources/[0-9]+/original\.pdf$`. |
| `test_upload_pdf_sha256_computed_correctly` | V3 | After flush side-effect sets `source.id`, assert that `session.add` was called with an object where `sha256 == hashlib.sha256(_MINIMAL_PDF).hexdigest()`. |
| `test_upload_pdf_kind_file_mime_pdf_set` | V4 | Assert `session.add` was called with object where `kind == "file"` and `mime_type == "application/pdf"`. |
| `test_upload_pdf_storage_uri_set_from_id` | V1+V2 | After flush sets `id=42`, assert `source.storage_uri == "s3://sources/42/original.pdf"` and `source.dagster_partition_key == "src_42"` (inspect `session.add.call_args` + flush side-effect overwrites). Use a capturing pattern: after flush, check the object that was added. |
| `test_upload_pdf_s3_put_object_called` | V2 | Assert `s3_mock.put_object` was called once with `Bucket=settings.MINIO_SOURCES_BUCKET`, `Key="sources/42/original.pdf"`, `Body=_MINIMAL_PDF`. |
| `test_upload_pdf_no_token_returns_401` | (auth gate) | POST without Authorization header → 401, `WWW-Authenticate: Bearer`. |
| `test_upload_pdf_missing_file_returns_422` | (edge) | POST multipart with no `file` part → 422. |
| `test_upload_non_pdf_content_type_returns_415` | (edge) | POST with `content_type="text/plain"` → 415. |
| `test_upload_with_collection_id` | (edge) | POST with `collection_id=5` → 201; assert `session.add` called with object where `collection_id == 5`. |
| `test_upload_without_collection_id` | (edge) | POST without `collection_id` → 201; assert `collection_id is None` on the persisted object. |
| `test_upload_s3_failure_does_not_commit` | (atomicity) | Override S3 dep to raise `Exception("MinIO down")`; assert 500 returned; assert `session.commit` NOT called. |
| `test_upload_flush_order_before_s3` | (D3 ordering) | Using a recording mock, assert `session.flush()` is called before `s3.put_object()`. Implement by patching both with side-effectful mocks that append to a `call_order` list. |

### Criterion-to-check mapping

| F-011 criterion | Unit test(s) | checks.sh sources) assertion |
|---|---|---|
| V1: 201 + `{"id": int, "storage_uri": "s3://sources/<id>/original.pdf"}` | `test_upload_pdf_returns_201_with_id_and_storage_uri`, `test_upload_pdf_storage_uri_shape` | `sources UPLOAD-V1`: curl POST, assert 201, assert `storage_uri` regex. |
| V2: File exists in MinIO at returned storage_uri path | `test_upload_pdf_s3_put_object_called` | `sources UPLOAD-V2`: `boto3.client.head_object(Bucket='sources', Key=f'sources/{id}/original.pdf')` — no exception means object exists. |
| V3: source row sha256 matches `sha256sum` of uploaded file | `test_upload_pdf_sha256_computed_correctly` | `sources UPLOAD-V3`: Postgres query `SELECT sha256 FROM source WHERE id=<id>` compared to Python `hashlib.sha256(_MINIMAL_PDF).hexdigest()`. |
| V4: source row `kind='file'`, `mime_type='application/pdf'` | `test_upload_pdf_kind_file_mime_pdf_set` | `sources UPLOAD-V4`: Postgres query `SELECT kind, mime_type FROM source WHERE id=<id>`, assert both values. |

---

## §7 checks.sh `sources)` Layer Spec

### Token mint pattern

Copied exactly from `collections)` layer — mint a token for `admin@example.com / testpassword123` using `/api/auth/token`, capture to a temp file, extract with `python3 -c "import json; ..."`, remove temp file.

```bash
sources)
  COMPOSE="docker/docker-compose.dev.yml"
  [[ -f "$COMPOSE" ]] || { echo "no $COMPOSE yet"; exit 0; }

  FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"
  MINIO_USER="${MINIO_ROOT_USER:-minioadmin}"
  MINIO_PASS="${MINIO_ROOT_PASSWORD:-devpassword}"

  echo "--- sources: mint Bearer token ---"
  SRC_TOKEN_BODY=$(mktemp)
  SRC_TOKEN_STATUS=$(curl -sS -X POST \
    "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
    -d "username=admin@example.com&password=testpassword123" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -w '%{http_code}' -o "$SRC_TOKEN_BODY")
  test "$SRC_TOKEN_STATUS" = "200" \
    || { echo "FAIL: sources) could not mint token (status $SRC_TOKEN_STATUS) — run 'bash $0 auth' first"; rm -f "$SRC_TOKEN_BODY"; exit 1; }
  SRC_TOKEN=$(python3 -c "import json; print(json.load(open('$SRC_TOKEN_BODY'))['access_token'])")
  rm -f "$SRC_TOKEN_BODY"
```

### Minimal PDF generation (inline Python, no binary committed)

```bash
  echo "--- sources: generate minimal valid PDF fixture ---"
  PDF_FILE=$(mktemp /tmp/test-XXXXXX.pdf)
  python3 -c "
import sys
pdf = (
    b'%PDF-1.4\n'
    b'1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n'
    b'2 0 obj<</Type /Pages /Kids[3 0 R] /Count 1>>endobj\n'
    b'3 0 obj<</Type /Page /MediaBox[0 0 612 792] /Parent 2 0 R>>endobj\n'
    b'xref\n0 4\n'
    b'0000000000 65535 f \n'
    b'0000000009 00000 n \n'
    b'0000000058 00000 n \n'
    b'0000000115 00000 n \n'
    b'trailer<</Size 4 /Root 1 0 R>>\n'
    b'startxref\n182\n%%EOF\n'
)
with open('$PDF_FILE', 'wb') as f:
    f.write(pdf)
EXPECTED_SHA256 = __import__('hashlib').sha256(pdf).hexdigest()
print(EXPECTED_SHA256)
" > /tmp/src_expected_sha256.txt
  EXPECTED_SHA256=$(cat /tmp/src_expected_sha256.txt)
```

### UPLOAD-V1: POST returns 201 + correct shape

```bash
  echo "--- sources UPLOAD-V1: POST /api/sources/upload returns 201 ---"
  UPLOAD_BODY=$(mktemp)
  UPLOAD_STATUS=$(curl -sS -X POST \
    "http://localhost:${FASTAPI_HOST_PORT}/api/sources/upload" \
    -H "Authorization: Bearer $SRC_TOKEN" \
    -F "file=@${PDF_FILE};type=application/pdf" \
    -w '%{http_code}' -o "$UPLOAD_BODY")
  test "$UPLOAD_STATUS" = "201" \
    || { echo "FAIL: sources UPLOAD-V1 returned $UPLOAD_STATUS: $(cat "$UPLOAD_BODY")"; rm -f "$UPLOAD_BODY" "$PDF_FILE"; exit 1; }
  SRC_ID=$(python3 -c "
import json, re, sys
body = json.load(open('$UPLOAD_BODY'))
assert isinstance(body.get('id'), int), f'id not int: {body}'
uri = body.get('storage_uri', '')
assert re.match(r'^s3://sources/[0-9]+/original\.pdf$', uri), f'storage_uri shape wrong: {uri}'
assert uri == f\"s3://sources/{body['id']}/original.pdf\", f'id/uri mismatch: {body}'
print(body['id'])
" 2>&1) || { echo "FAIL: sources UPLOAD-V1 response shape incorrect: $SRC_ID"; rm -f "$UPLOAD_BODY" "$PDF_FILE"; exit 1; }
  echo "  UPLOAD-V1 OK: id=$SRC_ID storage_uri=s3://sources/${SRC_ID}/original.pdf"
  rm -f "$UPLOAD_BODY"
```

### UPLOAD-V2: File exists in MinIO

```bash
  echo "--- sources UPLOAD-V2: file exists in MinIO at sources/${SRC_ID}/original.pdf ---"
  docker compose -f "$COMPOSE" exec -T \
    -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
    -e SRC_ID="${SRC_ID}" \
    fastapi python -c "
import boto3, os, sys
s3 = boto3.client('s3', endpoint_url='http://minio:9000',
    aws_access_key_id=os.environ['S3_USER'],
    aws_secret_access_key=os.environ['S3_PASS'])
src_id = os.environ['SRC_ID']
key = f'sources/{src_id}/original.pdf'
try:
    s3.head_object(Bucket='sources', Key=key)
    print(f'  UPLOAD-V2 OK: object exists at {key}')
except Exception as e:
    print(f'FAIL: head_object raised {e}', file=sys.stderr)
    sys.exit(1)
" || { echo "FAIL: sources UPLOAD-V2 MinIO head_object failed"; rm -f "$PDF_FILE"; exit 1; }
```

### UPLOAD-V3: Postgres sha256 matches

```bash
  echo "--- sources UPLOAD-V3: Postgres sha256 matches uploaded file ---"
  DB_SHA256=$(docker compose -f "$COMPOSE" exec -T postgres \
    psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
      "SELECT sha256 FROM source WHERE id=${SRC_ID}")
  DB_SHA256=$(echo "$DB_SHA256" | tr -d '[:space:]')
  test "$DB_SHA256" = "$EXPECTED_SHA256" \
    || { echo "FAIL: sha256 mismatch: DB='$DB_SHA256' expected='$EXPECTED_SHA256'"; rm -f "$PDF_FILE"; exit 1; }
  echo "  UPLOAD-V3 OK: sha256=$DB_SHA256"
```

### UPLOAD-V4: kind and mime_type correct

```bash
  echo "--- sources UPLOAD-V4: kind='file', mime_type='application/pdf' in Postgres ---"
  ROW=$(docker compose -f "$COMPOSE" exec -T postgres \
    psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
      "SELECT kind, mime_type FROM source WHERE id=${SRC_ID}")
  echo "$ROW" | grep -q "file" \
    || { echo "FAIL: kind != 'file': $ROW"; rm -f "$PDF_FILE"; exit 1; }
  echo "$ROW" | grep -q "application/pdf" \
    || { echo "FAIL: mime_type != 'application/pdf': $ROW"; rm -f "$PDF_FILE"; exit 1; }
  echo "  UPLOAD-V4 OK: kind=file mime_type=application/pdf"

  rm -f "$PDF_FILE" /tmp/src_expected_sha256.txt
  ;;
```

### Position in `all)` chain and full updated chain

The `sources)` layer is inserted after `collections)` and before `buckets)`. The complete updated `all)` chain in `verify/checks.sh` must read exactly:

```bash
  all)
    bash "$0" smoke
    bash "$0" infra
    bash "$0" backend
    bash "$0" frontend
    bash "$0" contract
    bash "$0" migration
    bash "$0" auth
    bash "$0" collections
    bash "$0" sources
    bash "$0" buckets
    bash "$0" dagster
    bash "$0" runs
    ;;
```

The `sources)` case block is inserted as a new case between `collections)` and `buckets)` in the `case "$LAYER" in` structure.

---

## §8 Hard-Invariant & CAL Compliance Notes

### Invariant #1 — Lineage: NOT APPLICABLE

F-011 creates a raw `source` row. Lineage (parents[], processor identity, config hash, input refs) applies to Commits/DocumentVariants, not to raw source ingestion. The `Source` model has no lineage columns. This feature correctly does not touch lineage.

### Invariant #2 — Storage separation + CAS: SATISFIED (with explanation)

The CAS invariant applies to processed artifacts (DocumentVariant, Silver/Gold commits). Raw source files are explicitly id-keyed per design doc lines 252 and 425. The sha256 IS computed and persisted to `source.sha256` for integrity tracking, but the S3 key is `sources/{id}/original.pdf` (id-keyed), not `sources/{sha256}`. This is correct per the design doc and does not violate invariant #2.

Metadata (source row) lives in Postgres. Content (PDF bytes) lives in MinIO. The byte content is never stored in Postgres. Invariant #2 is fully satisfied.

### Invariant #3 — Schema frozen post-publish: NOT APPLICABLE

No Silver/Gold repo publishes a commit in this sprint.

### Invariant #4 — LLM calls through gateway: NOT APPLICABLE

No LLM calls in this feature.

### Invariant #5 — Async SQLAlchemy: SATISFIED

All DB operations use `await`:
- `await session.flush()` — not `session.flush()` (synchronous variant is forbidden).
- `await session.commit()` — not `session.commit()`.
- `session.add()` IS synchronous in AsyncSession (this is correct SQLAlchemy 2.x behaviour — `add` is not awaitable).
- No `session.query()` anywhere.
- The ruff + mypy checks in `backend)` layer catch any sync session usage.

### Invariant #6 — OpenAPI ↔ TS type sync: REQUIRED

Adding `POST /api/sources/upload` with `SourceUploadResponse` adds a new operation and a new schema to the OpenAPI spec. `packages/api-types/openapi.json` MUST be regenerated and committed in the SAME commit.

**Exact regen command** (confirmed from S009-F-009 `agreed.md` §6 and S010-F-010 `agreed.md` §7, both citing commit `594356d` precedent):

```bash
cd apps/api && uv run python -c \
  'import json; from dataplat_api.main import app; print(json.dumps(app.openapi(), indent=2))' \
  > ../../packages/api-types/openapi.json
```

Run from repo root: `cd apps/api && uv run python -c '...' > ../../packages/api-types/openapi.json`

After running, confirm the diff includes the new `POST /api/sources/upload` operation and the `SourceUploadResponse` schema:

```bash
git diff packages/api-types/openapi.json
```

Commit the updated `openapi.json` in the same commit as `schemas/sources.py`, `routers/sources.py`, and `storage/s3.py`.

### CAL-3 — Schema changes committed with regen

Per reviewer calibration CAL-3: any API schema change must include `make codegen` (or the Python equivalent above while Makefile is absent) and the resulting `packages/api-types/` diff in the SAME commit. This sprint satisfies that requirement by including the regen in the implementation step, not as an afterthought.

---

## §9 Open Questions

| ID | Question | Recommendation |
|---|---|---|
| OQ-2 | Should `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` have no-default (fast-fail at startup) like `SECRET_KEY`? | Current proposal: soft defaults matching compose dev values. Reviewer may prefer no-default for production-hardening. MVP chooses defaults for developer ergonomics; change if asked. |
| OQ-3 | Should a non-existent `collection_id` return 422 (FK check in handler) rather than 500 (unhandled IntegrityError)? | Not in F-011 scope per feature_list criteria. Mark as a future improvement. |
| OQ-4 | Should magic-byte PDF sniffing replace content-type checking? | Not in scope; content-type check is sufficient for MVP. Requires `python-magic` / libmagic which is not in `pyproject.toml`. |
| OQ-5 | aioboto3 session: per-request or module-level singleton? | Per-request for simplicity. Module-level singleton with connection pool is a future optimisation. |
