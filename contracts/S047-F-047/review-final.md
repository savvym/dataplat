# Sprint S047-F-047 — Review Final (Mode B)

**Reviewer**: leader (Mode B)
**Date**: 2026-06-04
**Commit reviewed**: 9ad1f9e
**Contract reviewed**: `contracts/S047-F-047/agreed.md` (rev-2)
**Prior feedback reviewed**: `contracts/S047-F-047/feedback.md` (round-1 + round-2 / NIT-2)

---

## Files in commit 9ad1f9e

| File | Lines Δ | Status |
|---|---|---|
| `apps/api/dataplat_api/config.py` | +5 | `MINIO_DATASETS_BUCKET: str = "datasets"` added |
| `apps/api/dataplat_api/schemas/datasets.py` | +35 | `DatasetDownloadFile` + `DatasetDownloadResponse` added |
| `apps/api/dataplat_api/routers/datasets.py` | +79 | `GET /{id}/download` handler + `_PRESIGN_TTL_SECONDS` constant added |
| `apps/api/dataplat_api/storage/s3.py` | +/−15 | Docstring updated (L1) |
| `apps/api/tests/test_datasets_download.py` | +495 | New — 11 tests |
| `packages/api-types/openapi.json` | +96 | Regenerated — new path + 2 new schemas |

6 files, exactly matching §3 of `agreed.md`. No extraneous files.

---

## Blocker Criterion Verdicts

### B1 — Owner-scope filter on SELECT; 404-collapse for not-found OR wrong-owner

**PASS.**

`routers/datasets.py` lines 165–175:
```python
result = await session.execute(
    select(Dataset)
    .where(Dataset.id == id)
    .where(Dataset.materialized_by == current_user.id)
)
row = result.scalar_one_or_none()
if row is None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
```
Both `id == ?` and `materialized_by == current_user.id` appear in the single SELECT. A non-existent id and an id owned by another user both return the same 404/`"Dataset not found"` — no enumeration leak. Confirmed structurally by test #9 (`literal_binds` assertion asserts `"materialized_by"` and mock user id `9` appear in compiled SQL) and behaviourally by tests #5 + #6 (both return `{"detail": "Dataset not found"}`).

---

### B2 — 11 tests; #11 uses `call_args_list` exact-set assertion on the 5 EXACT prefixed keys

**PASS.**

`grep -c "^def test_" test_datasets_download.py` → **11**.

Test #11 (`test_download_presigned_url_keys_match_prefix`, lines 453–495):
```python
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
Uses `call_args_list`, set equality (`==` not `in`), extracts via `c.kwargs["Params"]["Key"]`.
Cross-checked against `hf_dataset_io_manager.py` lines 228, 243–290: the IOManager writes to
`{prefix}/data/train-00000.parquet`, `{prefix}/data/validation-00000.parquet`, `{prefix}/recipe.json`,
`{prefix}/README.md`, `{prefix}/dataset_infos.json` (prefix = `f"{obj.dataset_id}_{obj.version_tag}"`).
The 5 keys in test #11 with `dataset_id=42, version_tag="v1"` → `42_v1/…` match exactly. No `data/`-subfolder omission, no bare-relative-name bug.

---

### B3 — `openapi.json` regenerated in same commit (invariant #6)

**PASS.**

`packages/api-types/openapi.json` is in commit 9ad1f9e alongside all Python source changes (confirmed by `git show --stat`). Verified contents:
- `paths["/api/datasets/{id}/download"]` present with `get` operation, `DatasetDownloadResponse` response schema, `OAuth2PasswordBearer` security, `422` validation-error response.
- `components.schemas.DatasetDownloadFile`: `{name: str, presigned_url: str}` — 2 required fields.
- `components.schemas.DatasetDownloadResponse`: `{dataset_id: int, files: [DatasetDownloadFile], expires_in_seconds: int}` — 3 required fields.
- No existing paths or schemas were removed or altered.

---

### B4 — 422 invalid-id test asserts 422

**PASS.**

Test #8 (`test_download_invalid_id_returns_422`, lines 354–373):
```python
response = client.get("/api/datasets/not-a-number/download")
assert response.status_code == 422
```
Auth dep overridden so 401 does not fire first; path-param validation fires before handler body, returning 422. Correct.

---

### B5 — Round-1 findings M1/L1/L2/N1 + round-2 NIT-2 all reflected in code

**PASS — all five addressed.**

| Finding | Severity | Code location | Verdict |
|---|---|---|---|
| **M1** — No `Key=` assertion on `generate_presigned_url` | MEDIUM | Test #11, lines 453–495 | ✅ Exact-set `call_args_list` assertion implemented per agreed.md §5 code shape |
| **L1** — `storage/s3.py` docstring "for the sources bucket" | LOW | `s3.py` lines 30–36 | ✅ "for the sources bucket" dropped; new text "Used for both the sources bucket (F-011) and the datasets bucket (F-047)" matches agreed.md §3 verbatim |
| **L2** — §8 invariant #2 silent on presigned URL hostname | LOW | Contract document | ✅ N/A to code — was a contract-document finding; reflected in agreed.md §8 row #2; handler docstring mentions `minio:9000` in router module-level docstring context |
| **N1** — `3600` bare magic number | NIT | `routers/datasets.py` line 62 | ✅ `_PRESIGN_TTL_SECONDS: int = 3600` present; used in all 5 `ExpiresIn=_PRESIGN_TTL_SECONDS` calls (line 191) and `expires_in_seconds=_PRESIGN_TTL_SECONDS` (line 200) |
| **NIT-2** — test #1 description/assertion still used literal `3600` | NIT | `test_datasets_download.py` line 208 | ✅ Test #1 assertion reads `body["expires_in_seconds"] == _PRESIGN_TTL_SECONDS` (imported constant, not literal) |

---

### B6 — Hard invariants #1–#6 each PASS or N/A

| # | Invariant | Verdict | Evidence |
|---|---|---|---|
| 1 | Lineage mandatory | **N/A** | Read-only endpoint; no `Commit` row created |
| 2 | Storage separation + CAS | **PASS** | Metadata (prefix, version_tag) read from Postgres; presigned URLs (URI references, not blob bytes) returned to client; `minio:9000` internal hostname is the MVP trade-off explicitly accepted per design doc §11.1 and documented in agreed.md §8 |
| 3 | Schema frozen post-publish | **N/A** | No dataset schema mutations; read-only |
| 4 | LLM calls through gateway | **N/A** | No LLM calls in a download endpoint |
| 5 | Async SQLAlchemy | **PASS** | `AsyncSession = Depends(get_session)`; `await session.execute(select(…).where(…).where(…))`; `scalar_one_or_none()` (sync on result proxy, correct); `await s3.generate_presigned_url(…)` (aiobotocore 2.25.1 `async def`, OQ-1 confirmed) |
| 6 | OpenAPI ↔ TS type sync | **PASS** | `openapi.json` regenerated and committed in same commit 9ad1f9e (see B3) |

---

### B7 — No scope deviations beyond agreed.md + feedback.md NIT-2

**PASS.**

All 6 files changed are exactly the 6 listed in agreed.md §3. No extra schemas, routes, models, or migrations introduced. `_PRESIGN_TTL_SECONDS` is exported (imported in test file via `from dataplat_api.routers.datasets import _PRESIGN_TTL_SECONDS`) — this is the NIT-2 resolution, consistent with agreed.md. No out-of-scope features (no `MINIO_PUBLIC_ENDPOINT`, no status gate, no zip archive, no audit log). Route declaration order is `GET ""` → `GET /{id}` → `GET /{id}/download` → `POST /{recipe_id}/materialize` — exactly as agreed (confirmed by line-number inspection of final file: 65, 103, 137, 204).

---

## Additional Spot-Checks

- **Bucket reference in handler**: `Params={"Bucket": settings.MINIO_DATASETS_BUCKET, "Key": key}` — uses the settings constant, not a hardcoded `"datasets"` string. ✅
- **Prefix construction**: `prefix = f"{row.id}_{row.version_tag}"` (line 177) — matches `hf_dataset_io_manager.py` line 228 `f"{obj.dataset_id}_{obj.version_tag}"` exactly. ✅
- **Name extraction**: `name = key[len(prefix) + 1:]` (line 194) strips the `{prefix}/` to produce relative names matching agreed.md §2 JSON example (`"data/train-00000.parquet"`, etc.). ✅
- **5-call loop correctness**: `for key in object_keys` iterates over the 5-element list; each iteration calls `generate_presigned_url` once; `files` list is built with one `DatasetDownloadFile` per key. ✅
- **Test #9 SQL structural assertion**: compiles the Select with `literal_binds=True`, asserts `"materialized_by"` and `"9"` (mock user id) in compiled SQL string — mirrors the S045 M1 pattern. ✅
- **test_download_no_token_returns_401 (test #7)**: no dependency override — real `oauth2_scheme` fires, returning 401 with `WWW-Authenticate: Bearer`. ✅
- **`_make_dataset()` factory**: populates all 13 ORM-mapped attributes on the `MagicMock(spec=Dataset)` — matches agreed.md §5 specification. ✅
- **No regression in openapi.json**: all 27 paths from prior sprints are present in the regenerated file; only `/api/datasets/{id}/download` is new. ✅

---

## Summary

| Criterion | Verdict | Blockers |
|---|---|---|
| B1 — Owner-scope filter + 404-collapse | ✅ PASS | 0 |
| B2 — 11 tests + #11 exact-set `call_args_list` | ✅ PASS | 0 |
| B3 — openapi.json in same commit | ✅ PASS | 0 |
| B4 — 422 invalid-id test | ✅ PASS | 0 |
| B5 — M1/L1/L2/N1/NIT-2 reflected | ✅ PASS | 0 |
| B6 — Hard invariants #1–#6 | ✅ PASS / N/A | 0 |
| B7 — No scope deviations | ✅ PASS | 0 |

**Total blockers: 0**

---

## APPROVED
