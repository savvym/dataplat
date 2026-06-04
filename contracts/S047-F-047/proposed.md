# Sprint S047-F-047 — Proposed Contract

**Feature**: F-047 — Dataset download: `GET /api/datasets/{id}/download` streams Parquet files from MinIO as a zip archive or returns a presigned URL for direct download  
**Depends on**: F-044 (`passes: true`)  
**Sprint directory**: `contracts/S047-F-047/`  
**Author**: leader (inline)  
**Date**: 2026-06-04  
**Revision**: 2

---

## §1 Goal

Add a `GET /api/datasets/{id}/download` endpoint to the existing datasets router that returns, for the authenticated owner of dataset `{id}`, a JSON object listing presigned MinIO URLs for each of the five dataset artifacts written by F-044's `HFDatasetIOManager` — enabling direct, server-bandwidth-free download of the Parquet train and validation splits plus the metadata files.

---

## §2 Design Overview

### Option chosen: **Option C — Hybrid presigned URL list**

The endpoint returns:

```json
{
  "dataset_id": 42,
  "files": [
    {"name": "data/train-00000.parquet",  "presigned_url": "http://minio:9000/..."},
    {"name": "data/validation-00000.parquet", "presigned_url": "http://minio:9000/..."},
    {"name": "recipe.json",              "presigned_url": "http://minio:9000/..."},
    {"name": "README.md",                "presigned_url": "http://minio:9000/..."},
    {"name": "dataset_infos.json",       "presigned_url": "http://minio:9000/..."}
  ],
  "expires_in_seconds": 3600
}
```

`Content-Type: application/json`. HTTP 200 for success, 404 for not-found / wrong-owner, 401 for missing/invalid token, 422 for non-integer `{id}`.

### Why Option C

1. **Zero F-044 re-work.** The five object keys already exist in MinIO under the `{dataset_id}_{version_tag}` prefix. The endpoint just enumerates them and generates presigned GET URLs. No new writes, no pre-zipping.
2. **No server bandwidth cost.** Presigned URLs direct the client (browser, CLI, Python SDK) straight to MinIO. The API server is not in the data path.
3. **Spec literal compliance.** The feature's verification criteria say "or returns a JSON with `presigned_url` field". Returning an array (`files[*].presigned_url`) is a strict superset of a single `presigned_url` field — it satisfies the spec while being more useful (caller loads each file independently, or passes the Parquet URLs directly to `pandas.read_parquet()`).
4. **V2 verification is satisfied naturally.** A client that fetches the two `*.parquet` presigned URLs and calls `pd.read_parquet()` on each gets valid Parquet data. The test simulates this by asserting the returned URLs are well-formed signed S3 URLs and that a mocked MinIO `get_object()` on those keys returns bytes parseable by `pyarrow`.
5. **TTL is one hour (3600 s).** Long enough for interactive use; short enough to limit exposure. Returned as `expires_in_seconds` in the body so clients can cache-invalidate correctly.

### Rejected options

**Option A — StreamingResponse zip archive.** Rejected for two reasons. First, `pyproject.toml` lists no zip-streaming library (no `zipstream-ng`, no `zipfile` streaming shim beyond the stdlib's `zipfile` module). The stdlib `zipfile` module requires a seekable file-like object for write mode, which makes true streaming (chunked read from MinIO → chunked write to response without buffering the whole archive) non-trivial without a third-party library. Buffering the full archive in memory before sending is acceptable only for tiny datasets; real datasets can be hundreds of MB. Second, once `StreamingResponse` has begun sending the body, we cannot return a 5xx if a mid-stream MinIO read fails — the client receives a truncated archive with a 200 status, which is worse than an error. Adding `zipstream-ng` to `pyproject.toml` would be non-trivial and outside MVP scope.

**Option B — Single presigned URL for a pre-zipped object.** Rejected because it requires F-044's `HFDatasetIOManager` to also write a `.zip` object containing the five files, which is out-of-scope re-work for a landed sprint. Alternatively, a one-time zip creation in the download handler has the same memory / streaming problem as Option A.

---

## §3 File Changes

| File | Status | Reason |
|---|---|---|
| `apps/api/dataplat_api/config.py` | **edit** | Add `MINIO_DATASETS_BUCKET: str = "datasets"` to `Settings`. This was explicitly deferred to F-047 by F-043's agreed.md (§Out of Scope item 4) and noted in `hf_dataset_io_manager.py` line 23. |
| `apps/api/dataplat_api/schemas/datasets.py` | **edit** | Add `DatasetDownloadFile` (2 fields: `name: str`, `presigned_url: str`) and `DatasetDownloadResponse` (3 fields: `dataset_id: int`, `files: list[DatasetDownloadFile]`, `expires_in_seconds: int`). |
| `apps/api/dataplat_api/routers/datasets.py` | **edit** | Add `GET /{id}/download` route `download_dataset()` after `GET /{id}` and before `POST /{recipe_id}/materialize`. Import `DatasetDownloadResponse`, `DatasetDownloadFile` from `dataplat_api.schemas.datasets`. Import `get_s3_client` from `dataplat_api.storage.s3`. Import `settings` from `dataplat_api.config`. Add module-level constant `_PRESIGN_TTL_SECONDS: int = 3600`; all 5 `generate_presigned_url` calls reference this constant instead of the bare `3600` literal. **Route declaration order** (safe, per OQ-4 resolution — FastAPI `{id}` matches one path segment only): `GET ""` → `GET /{id}` → `GET /{id}/download` → `POST /{recipe_id}/materialize`. |
| `apps/api/dataplat_api/storage/s3.py` | **edit** | Update `get_s3_client()` docstring to remove "for the sources bucket" specificity — function is now used for both sources and datasets buckets. 1-line docstring change; no behavior change; no new test required. |
| `apps/api/tests/test_datasets_download.py` | **create** | New test module — 11 unit tests (see §5). |
| `packages/api-types/openapi.json` | **generated** | Regenerated after schema and router additions; committed in the same commit per hard invariant #6. |

No Alembic migration is needed — the endpoint is read-only against Postgres (single owner-scoped SELECT) and reads from MinIO only to generate presigned URLs.

**Codegen hard requirement (invariant #6):** Implementer MUST regenerate `packages/api-types/openapi.json` manually via:

```bash
cd apps/api && uv run python -c "
import json
from dataplat_api.main import app
from fastapi.openapi.utils import get_openapi
spec = get_openapi(title=app.title, version=app.version, routes=app.routes)
with open('../../packages/api-types/openapi.json', 'w') as f:
    json.dump(spec, f, indent=2)
"
```

(No `Makefile` exists at the repo root — confirmed in S045/S046 contracts.) The resulting diff MUST be staged and committed in the **same** commit as all Python source changes. This is a hard requirement, not advisory.

---

## §4 Verification Mapping

### V1 — `GET /api/datasets/{id}/download` returns 200 with appropriate Content-Type and non-empty body (or a JSON with `presigned_url` field)

Covered by:
- **`test_download_200_returns_json_with_files`** (test §5, item 1): mock session returns a valid Dataset row; mock S3 client generates well-formed presigned URLs; asserts `status_code == 200`, `Content-Type` contains `application/json`, `body["files"]` has 5 entries, each entry has non-empty `presigned_url` string, `body["dataset_id"] == <mocked id>`, `body["expires_in_seconds"] == 3600`.
- **`test_download_response_shape_exact`** (item 2): asserts `set(body.keys()) == {"dataset_id", "files", "expires_in_seconds"}` and `set(body["files"][0].keys()) == {"name", "presigned_url"}` — exact key match, no extra fields.

### V2 — Downloading and extracting the result yields valid Parquet files loadable by pandas

Covered by:
- **`test_download_parquet_urls_are_named_correctly`** (item 3): asserts the `files` list contains entries with `name == "data/train-00000.parquet"` and `name == "data/validation-00000.parquet"` — confirming the exact MinIO keys that hold valid Parquet data (written by F-044) are included.
- **`test_download_presigned_urls_are_well_formed`** (item 4): calls the endpoint with a mock S3 client whose `generate_presigned_url()` returns a URL string of the form `http://minio:9000/datasets/{prefix}/{object_key}?X-Amz-Signature=...`; asserts each URL in `files[*].presigned_url` matches `re.match(r'^https?://.+\?X-Amz', url)` (well-formed signed URL pattern). This is the structural proxy for V2: a URL that is correctly signed and names a valid MinIO key fetches actual Parquet bytes in a live environment.

> **Note on full E2E coverage:** True pandas roundtrip validation (fetch URL → `pd.read_parquet()`) is an integration test beyond the unit-test scope of this sprint. The `verify/checks.sh` smoke layer covers liveness. The `checks.sh backend` layer covers the unit tests above. If a live integration test is added later, it belongs in the `integration` pytest marker group (see `pyproject.toml` `[tool.pytest.ini_options]`).

### V3 — `GET /api/datasets/99999/download` returns 404

Covered by:
- **`test_download_not_found_returns_404`** (item 5): mock session returns `scalar_one_or_none() == None` (simulates id=99999); asserts `status_code == 404`, `body == {"detail": "Dataset not found"}`.
- **`test_download_wrong_owner_returns_404`** (item 6): session mock returns `None` simulating a row owned by a different user; same 404 assertion — no enumeration leak.

---

## §5 Test Plan

File: `apps/api/tests/test_datasets_download.py`

All tests use `TestClient(app)`, `MagicMock(spec=Dataset)` row factory, `AsyncMock` session overrides (single `execute()` call: `scalar_one_or_none()` on a synchronous `MagicMock` result proxy — matching the `test_datasets_get.py` pattern), and `AsyncMock` S3 client override (via `app.dependency_overrides[get_s3_client]`). The `_make_dataset()` factory populates all 13 ORM attributes.

**S3 client mock pattern** for presigned URL tests (OQ-1 resolved: `generate_presigned_url` is `async def` in aiobotocore 2.25.1; `AsyncMock` is the correct and only mock type):
```python
mock_s3 = AsyncMock()
mock_s3.generate_presigned_url = AsyncMock(
    return_value="http://minio:9000/datasets/42_v1/data/train-00000.parquet"
              "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc123"
)
async def _mock_s3_dep():
    yield mock_s3
app.dependency_overrides[get_s3_client] = _mock_s3_dep
```

| # | Test name | Invariant covered | Maps to |
|---|---|---|---|
| 1 | `test_download_200_returns_json_with_files` | 200 happy path, 5 files returned, Content-Type, `expires_in_seconds == 3600` | V1 |
| 2 | `test_download_response_shape_exact` | Exact key set in response body — no extra fields leaked | V1, schema guard |
| 3 | `test_download_parquet_urls_are_named_correctly` | `files` list includes entries with `name == "data/train-00000.parquet"` and `name == "data/validation-00000.parquet"` | V2 |
| 4 | `test_download_presigned_urls_are_well_formed` | Each `presigned_url` matches `r'^https?://.+\?X-Amz'` | V2 |
| 5 | `test_download_not_found_returns_404` | Session returns `None` for id=99999 → `{"detail": "Dataset not found"}` | V3 |
| 6 | `test_download_wrong_owner_returns_404` | Session returns `None` (different user) → same 404, identical detail string | V3, no enumeration leak |
| 7 | `test_download_no_token_returns_401` | No `Authorization` header; no dep override → real `oauth2_scheme` → 401 with `WWW-Authenticate: Bearer` | auth gate |
| 8 | `test_download_invalid_id_returns_422` | Non-integer path `/api/datasets/not-a-number/download` → 422 before handler; auth dep overridden so 401 does not fire first | FastAPI path-param validation |
| 9 | `test_download_owner_scope_sql_literal_binds` | SQL-structural: capture `session.execute.call_args_list[0].args[0]`, compile with `literal_binds=True`; assert `"materialized_by"` and mock user id appear in the compiled SQL string. Mirrors M1 pattern from S045. | owner-scope never silently omitted |
| 10 | `test_download_all_five_keys_present` | `files` list has exactly 5 entries; `set(f["name"] for f in body["files"]) == {"data/train-00000.parquet", "data/validation-00000.parquet", "recipe.json", "README.md", "dataset_infos.json"}` | complete key enumeration, no accidental omission or duplication |
| 11 | `test_download_presigned_url_keys_match_prefix` | `mock_s3.generate_presigned_url.call_args_list` extracted; asserts exact set of `Key=` kwargs across all 5 calls equals `{"42_v1/data/train-00000.parquet", "42_v1/data/validation-00000.parquet", "42_v1/recipe.json", "42_v1/README.md", "42_v1/dataset_infos.json"}`. Structural analog of S045 M1: ensures the handler passes fully-prefixed keys to MinIO, not bare relative names. | correct MinIO key routing — a handler using wrong keys passes all other tests because the mock returns a constant URL |

**Test #11 code shape:**
```python
def test_download_presigned_url_keys_match_prefix():
    # arrange: _make_dataset(id=42, version_tag="v1"); AsyncMock S3; dep overrides
    # act: GET /api/datasets/42/download
    calls = mock_s3.generate_presigned_url.call_args_list
    assert len(calls) == 5
    keys = {c.kwargs["Params"]["Key"] for c in calls}
    assert keys == {
        "42_v1/data/train-00000.parquet",
        "42_v1/data/validation-00000.parquet",
        "42_v1/recipe.json",
        "42_v1/README.md",
        "42_v1/dataset_infos.json",
    }
```

---

## §6 MinIO Key Layout

Taken verbatim from `dagster/dagster_platform/hf_dataset_io_manager.py` (lines 165–169, 243–281). The prefix is `{dataset_id}_{version_tag}`. The bucket is `MINIO_DATASETS_BUCKET` (default `"datasets"`).

The five objects written by F-044 for a dataset with `dataset_id=42` and `version_tag="v1"` are:

| Object key | Content-Type (informational) | Notes |
|---|---|---|
| `42_v1/data/train-00000.parquet` | `application/octet-stream` | PyArrow Parquet, train split |
| `42_v1/data/validation-00000.parquet` | `application/octet-stream` | PyArrow Parquet, validation split |
| `42_v1/recipe.json` | `application/json` | `json.dumps(recipe_snapshot)` |
| `42_v1/README.md` | `text/markdown` | dataset card or generated stub |
| `42_v1/dataset_infos.json` | `application/json` | HuggingFace DatasetInfo registry |

The `prefix` is constructed as `f"{obj.dataset_id}_{obj.version_tag}"` (line 228 of `hf_dataset_io_manager.py`). For the download endpoint, the prefix is derived from the Postgres row: `f"{row.id}_{row.version_tag}"`.

**Handler key-generation code (to be implemented):**
```python
# Module-level constant (NIT-1 resolution): avoids magic number in 5 call sites.
_PRESIGN_TTL_SECONDS: int = 3600  # 1 hour; configurable TTL deferred to post-MVP.

...

prefix = f"{row.id}_{row.version_tag}"
OBJECT_KEYS = [
    f"{prefix}/data/train-00000.parquet",
    f"{prefix}/data/validation-00000.parquet",
    f"{prefix}/recipe.json",
    f"{prefix}/README.md",
    f"{prefix}/dataset_infos.json",
]
```

Presigned URL generation for each key (OQ-1 resolved: `await` is correct — aiobotocore 2.25.1 ships `async def generate_presigned_url`):
```python
url = await s3.generate_presigned_url(
    "get_object",
    Params={"Bucket": settings.MINIO_DATASETS_BUCKET, "Key": key},
    ExpiresIn=_PRESIGN_TTL_SECONDS,
)
```

---

## §7 Settings

### `MINIO_DATASETS_BUCKET` — adding to FastAPI `Settings` now

This field was **explicitly deferred to F-047** by:
- `contracts/S043-F-043/agreed.md` §Out of Scope item 4: *"MINIO_DATASETS_BUCKET in FastAPI Settings: Deferred to F-047."*
- `dagster/dagster_platform/hf_dataset_io_manager.py` line 23 docstring: *"Deferred (F-047): MINIO_DATASETS_BUCKET in FastAPI Settings."*
- The Dagster layer already reads `os.environ.get("MINIO_DATASETS_BUCKET", "datasets")` directly (line 236 of `hf_dataset_io_manager.py`).

**Action in this sprint:** Add to `apps/api/dataplat_api/config.py` `Settings` class:
```python
# Added S047-F-047: datasets bucket for Parquet + metadata artifacts written by F-044.
# Default "datasets" matches MINIO_DATASETS_BUCKET env default in hf_dataset_io_manager.py.
# Deferred from F-043 (contracts/S043-F-043/agreed.md §Out of Scope item 4).
MINIO_DATASETS_BUCKET: str = "datasets"
```

**Migration story:** This is a new `pydantic-settings` field with a default value (`"datasets"`). No existing deployment breaks — the env var is optional (the default matches what Dagster already uses). No Alembic migration is involved. Docker Compose does not need to be updated for development; if a non-default bucket name is used in production, the operator adds `MINIO_DATASETS_BUCKET=<name>` to the `fastapi` service environment block in `docker-compose.dev.yml` (same pattern as `MINIO_SOURCES_BUCKET`).

### `MINIO_PUBLIC_ENDPOINT` — deferred post-MVP (OQ-2 resolved)

Presigned URLs generated with `endpoint_url=f"http://{settings.MINIO_ENDPOINT}"` embed `minio:9000` as the host — the internal Docker DNS name. This is the intended MVP behaviour: the design doc §11.1 explicitly scopes MVP to a single-machine `docker-compose` deployment where both the API and its clients run on the same network. A `MINIO_PUBLIC_ENDPOINT` setting (for overriding the presigned URL host to a browser-reachable address) is **not added in this sprint** for the following reasons:

1. It would require a new settings field, a `get_datasets_s3_client()` variant or URL-rewriting logic, and a new test — out of scope for a feature whose spec does not require browser reachability.
2. F-069 (Datasets page) is the immediate frontend consumer; it can open a new tab with the presigned URL. In the `docker-compose` dev stack, MinIO's port 9000 is mapped to `localhost:9000` — so browser-based download works when `minio:9000` is substituted with `localhost:9000` by the F-069 implementer.
3. `MINIO_PUBLIC_ENDPOINT` remains a post-MVP operator concern. If a production deployment requires a public host, the operator sets the new env var in a future sprint (F-070 or ops sprint).

---

## §8 Hard Invariants

| # | Invariant (CLAUDE.md) | Status | One-line reason |
|---|---|---|---|
| 1 | **Lineage mandatory** — any Commit MUST record `parents[]` + processor identity + config hash + input refs | **N/A** | `GET /api/datasets/{id}/download` is read-only; no `Commit` record is created. No lineage event fires. |
| 2 | **Storage separation + CAS** — metadata in Postgres; content in MinIO/S3 by `sha256(content)`; no blob bytes in Postgres | **✓ Respected** | Metadata (prefix, version_tag) read from Postgres; presigned URLs (URI references, not bytes) returned to client. URL hostname is `minio:9000` (internal Docker DNS) — acceptable for MVP internal-network deployment per design doc §11.1; `MINIO_PUBLIC_ENDPOINT` deferred to post-MVP ops (OQ-2 ruling). |
| 3 | **Schema frozen post-publish** — Silver/Gold schema changes require new commit | **N/A** | No dataset schema is mutated. Read-only endpoint. |
| 4 | **LLM calls go through the gateway** | **N/A** | No LLM calls in a download endpoint. |
| 5 | **Async SQLAlchemy from day one** — every DB session is async; no `session.query()`; no sync sessions | **✓ Respected** | Handler uses `AsyncSession = Depends(get_session)`, `await session.execute(select(...).where(...).where(...))`, `scalar_one_or_none()`. No `session.query()`. The `get_s3_client` dependency uses `aioboto3` (async) — already present in `pyproject.toml` as `aioboto3==15.5.0`. `generate_presigned_url` is `async def` on aiobotocore 2.25.1 (OQ-1 confirmed by reviewer). |
| 6 | **OpenAPI ↔ TS type sync** — API schema change MUST be followed by `make codegen`; `packages/api-types/` diff in same commit | **Required — hard requirement** | Two new schemas (`DatasetDownloadFile`, `DatasetDownloadResponse`) and one new path (`/api/datasets/{id}/download`) extend the OpenAPI surface. No `Makefile` at repo root (confirmed S045/S046 precedent). Implementer MUST regenerate `packages/api-types/openapi.json` manually (see §3) and commit the diff in the **same** commit. |

---

## §9 Out of Scope

- **Streaming zip archive (Option A)** — not implemented; see §2 rejection rationale.
- **Pre-zipped object in MinIO (Option B)** — not implemented; see §2 rejection rationale.
- **`MINIO_PUBLIC_ENDPOINT` / host override for presigned URLs** — deferred to post-MVP. Presigned URLs embed `minio:9000` (internal Docker DNS hostname), which is acceptable for MVP's single-machine `docker-compose` deployment per design doc §11.1. F-069 frontend integration uses `localhost:9000` via the MinIO port-mapping in `docker-compose.dev.yml`. A `MINIO_PUBLIC_ENDPOINT` settings field and URL-rewriting logic are out of scope for this sprint (OQ-2 resolved).
- **Gating on `status == "done"`** — not required by spec (OQ-3 resolved). F-069 frontend gates the Download button; the backend returns presigned URLs regardless of status. If the dataset is not yet materialized, the client receives a 404 from MinIO when it fetches the presigned URL — acceptable for MVP.
- **Audit log of download events** — no `download_event` table, no analytics tracking per download invocation. Future sprint.
- **Watermarking / DRM** — not in MVP scope (design doc §1.3).
- **Rate limiting or quota on downloads** — not in MVP scope.
- **Frontend wiring (F-070 / dataset detail page)** — UI integration is a separate sprint; this endpoint is backend-only.
- **Download of a subset of files** (e.g., train split only via query param) — MVP returns all five files unconditionally.
- **Signed URL TTL as a query parameter** — TTL is the `_PRESIGN_TTL_SECONDS` constant (3600 s); a configurable TTL via env var is a future enhancement.
- **`dataset_card_md` column population** — F-044 writes `README.md` to MinIO but does not write to the Postgres `dataset_card_md` column. This sprint does not change that. The download endpoint returns a presigned URL for `README.md` (the MinIO object), not the Postgres column value.

---

## §10 Open Questions (All Resolved)

All four open questions from rev-1 are resolved definitively below. Zero open questions remain. Resolutions are baked into the implementation contract; implementer is not required to re-verify independently.

### OQ-1 — `generate_presigned_url()`: sync or async?

**RESOLVED: ASYNC. Use `await`. Use `AsyncMock` in tests.**

Verified by reviewer (Mode A) directly against the installed `aiobotocore==2.25.1` (ships with `aioboto3==15.5.0`). The method is `async def` in `aiobotocore/signers.py`; `inspect.iscoroutinefunction` returns `True`. Handler calls `url = await s3.generate_presigned_url(...)`. Tests use `AsyncMock(return_value=...)` unconditionally. No conditional "if sync" path exists or is needed.

### OQ-2 — `MINIO_PUBLIC_ENDPOINT` for browser-reachable presigned URLs

**RESOLVED: DEFER. Acceptable for MVP per design doc §11.1.**

Presigned URLs embed `http://minio:9000` (internal Docker DNS). This is the correct MVP behaviour for a single-machine `docker-compose` deployment. `MINIO_PUBLIC_ENDPOINT` is not added in this sprint. F-069 uses `localhost:9000` via the port-mapped MinIO service. Full discussion in §7 and §8 invariant #2.

### OQ-3 — Gate on `status == "done"` before generating URLs?

**RESOLVED: NO GATE. Not required by spec.**

Spec verification criteria do not require a 409 for non-`done` status. F-069 frontend gates the Download button so clients never send a download request for a non-ready dataset in normal operation. If the MinIO objects do not exist, the client receives a 404 from MinIO. No backend status guard in this sprint. Noted in §9 Out of Scope.

### OQ-4 — Route path collision: `GET /{id}/download` vs. `GET /{id}`?

**RESOLVED: NO COLLISION. Declaration order is safe.**

FastAPI path parameters (`{id}`) match exactly one path segment and do not match across `/`. `GET /api/datasets/42/download` has two segments after the router prefix (`42` and `download`), so it cannot match `GET /{id}` (one segment). Declaration order `GET ""` → `GET /{id}` → `GET /{id}/download` → `POST /{recipe_id}/materialize` is correct and safe; documented explicitly in §3.

---

## §11 Codegen

New OpenAPI surface introduced by this sprint:

- **New path**: `GET /api/datasets/{id}/download` with response schema `DatasetDownloadResponse`.
- **New schemas**: `DatasetDownloadFile` (fields: `name: str`, `presigned_url: str`) and `DatasetDownloadResponse` (fields: `dataset_id: int`, `files: list[DatasetDownloadFile]`, `expires_in_seconds: int`).

Since no `Makefile` exists at the repo root (confirmed during S045 and S046 contract drafting), the implementer MUST regenerate `packages/api-types/openapi.json` manually using the `uv run python -c "..."` snippet in §3. The resulting diff MUST be committed in the **same** commit as `config.py`, `schemas/datasets.py`, `routers/datasets.py`, `storage/s3.py`, and `tests/test_datasets_download.py`. A commit that modifies Python API source without the matching `openapi.json` update will be rejected by the reviewer and (per CLAUDE.md invariant #6) by CI.

The diff should show:
1. A new `"/api/datasets/{id}/download"` entry under `paths` with a `get` operation.
2. New `DatasetDownloadFile` and `DatasetDownloadResponse` entries under `components/schemas`.
3. No removals or modifications to existing paths or schemas.

---

## §12 Round-1 Addenda

Summarises each finding from Reviewer Round 1 (Mode A, rev-1) and its resolution in this rev-2 update. Follows the S045/S046 precedent. Enables Round-2 reviewer to verify all resolutions at a glance without re-reading the full document.

| Finding | Severity | Resolution in rev-2 |
|---|---|---|
| **MEDIUM-1** — No assertion on `Key=` arg passed to `generate_presigned_url`; mock returns constant URL regardless of what key the handler actually builds | MEDIUM (blocking) | Added test #11 `test_download_presigned_url_keys_match_prefix` (§5): extracts `mock_s3.generate_presigned_url.call_args_list`, asserts the exact set of 5 `Key=` values equals `{"42_v1/data/train-00000.parquet", "42_v1/data/validation-00000.parquet", "42_v1/recipe.json", "42_v1/README.md", "42_v1/dataset_infos.json"}`. Structural analog of S045 M1. Code shape added to §5. |
| **LOW-1** — `storage/s3.py` docstring update not listed in §3 file changes | LOW | Added `storage/s3.py` row to §3 table: `edit` / "Update `get_s3_client()` docstring to remove 'for the sources bucket' specificity". |
| **LOW-2** — §8 invariant #2 row did not acknowledge presigned URL hostname (`minio:9000`) exposure | LOW | Expanded §8 invariant #2 "One-line reason" to explicitly state: URL hostname is `minio:9000` (internal Docker DNS), acceptable for MVP per design doc §11.1, `MINIO_PUBLIC_ENDPOINT` deferred to post-MVP ops (OQ-2 ruling). |
| **NIT-1** — `3600` TTL appears as a bare magic number in handler and tests | NIT | Added `_PRESIGN_TTL_SECONDS: int = 3600` module-level constant to `routers/datasets.py` (§3 routers row updated; §6 code sketch updated to reference the constant in `ExpiresIn=_PRESIGN_TTL_SECONDS`). |
| **OQ-1** — `generate_presigned_url()` sync or async? | OQ | RESOLVED ASYNC: aiobotocore 2.25.1 ships `async def generate_presigned_url`; always `await`; always `AsyncMock` in tests. "If sync, replace with MagicMock" advisory dropped from §10 and §5. |
| **OQ-2** — `MINIO_PUBLIC_ENDPOINT` for browser-reachable URLs | OQ | RESOLVED DEFERRED: `minio:9000` hostname acceptable for MVP per design doc §11.1; F-069 uses `localhost:9000` via port mapping; `MINIO_PUBLIC_ENDPOINT` is a post-MVP operator concern. §7 fully updated; §8 invariant #2 explicitly documents the trade-off; §9 Out of Scope bullet updated. |
| **OQ-3** — Gate on `status == "done"`? | OQ | RESOLVED NO GATE: spec has no such criterion; F-069 frontend gates the Download button; 404 from MinIO is the acceptable MVP fallback. §9 Out of Scope bullet added; §10 resolved. |
| **OQ-4** — Route collision `GET /{id}` vs. `GET /{id}/download`? | OQ | RESOLVED NO COLLISION: FastAPI `{id}` matches one segment only; declaration order `GET ""` → `GET /{id}` → `GET /{id}/download` → `POST /{recipe_id}/materialize` documented explicitly in §3 routers row and §10. |
