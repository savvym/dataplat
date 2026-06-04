# Sprint S047-F-047 — Reviewer Feedback (Mode A)

**Reviewer**: leader (Mode A)  
**Date**: 2026-06-04  
**Proposed.md revision reviewed**: Rev 1  
**Sources consulted**: `spec/feature_list.json` (F-047), `spec/product-spec.md`, `spec/tech-direction.md`, `docs/data_platform_design.md` §4.3 + §1.2, `CLAUDE.md` hard invariants, `dagster/dagster_platform/hf_dataset_io_manager.py`, `apps/api/dataplat_api/config.py`, `apps/api/dataplat_api/routers/datasets.py`, `apps/api/dataplat_api/schemas/datasets.py`, `apps/api/dataplat_api/storage/s3.py`, `apps/api/pyproject.toml`, `contracts/S044-F-044/agreed.md`, `contracts/S045-F-045/agreed.md`, `contracts/S046-F-046/agreed.md`, aiobotocore 2.25.1 source (via `uv run python` in `.venv`)

---

## Open Question Resolutions

All four open questions are resolved definitively below. These resolutions MUST be folded verbatim into `agreed.md`; implementer is not required to re-verify independently.

### OQ-1 — `generate_presigned_url()`: sync or async?

**RESOLVED: ASYNC. Use `await`. Use `AsyncMock` in tests.**

Verified directly against the installed `aiobotocore==2.25.1` (the version shipped with `aioboto3==15.5.0`):

```python
# aiobotocore/signers.py
async def generate_presigned_url(
    self, ClientMethod, Params=None, ExpiresIn=3600, HttpMethod=None
):
    ...
    params = await self._emit_api_params(...)
    (endpoint_url, ...) = await self._resolve_endpoint_ruleset(...)
    request_dict = await self._convert_to_request_dict(...)
    return await request_signer.generate_presigned_url(...)
```

`inspect.iscoroutinefunction` confirms `True`. The `add_generate_presigned_url` hook injects this `async def` onto the client class at instantiation time.

**Consequences for implementation:**
- Handler: `url = await s3.generate_presigned_url("get_object", Params={...}, ExpiresIn=3600)` — correct as written in proposed.md §6.
- Tests: `mock_s3.generate_presigned_url = AsyncMock(return_value=...)` — correct as written in proposed.md §5.
- The advisory note "if sync, replace with `MagicMock`" in OQ-1 is now moot and must NOT be carried into agreed.md.

### OQ-2 — `MINIO_PUBLIC_ENDPOINT` for browser-reachable presigned URLs

**RESOLVED: DEFER. Acceptable for MVP.**

Presigned URLs generated with `endpoint_url=f"http://{settings.MINIO_ENDPOINT}"` will embed `http://minio:9000` as the host — the internal Docker DNS name. This is not browser-reachable from the host machine. For the following reasons, this is acceptable for MVP:

1. The design doc (§11.1) explicitly scopes MVP to a single-machine `docker-compose` deployment. The download endpoint is also usable from CLI (`curl`, `httpx`) and Python SDK contexts — both run inside or can reach the Docker network.
2. F-069 (Datasets page, the immediate frontend consumer) shows a "Download" button — the F-069 implementer can open a new tab or use `window.open(presigned_url)`. If `localhost:9000` is exposed via the MinIO compose port mapping, browser-based download works in dev. The proposed.md §7 confirms the MinIO port is mapped.
3. `MINIO_PUBLIC_ENDPOINT` requires a new settings field, a new `get_datasets_s3_client()` variant or URL rewriting logic, and a new test. This is out of scope for this sprint.
4. If a production deployment requires a public host, the operator sets a new env var — the operator concern, not MVP scope (CLAUDE.md §"Scope discipline").

**Ruling: `MINIO_PUBLIC_ENDPOINT` is out of scope for this sprint as proposed. The F-070 implementer should note that presigned URLs will contain `minio:9000` and the Docker Compose dev file must expose port 9000 on localhost for browser integration.**

### OQ-3 — Gate on `status == "done"` before generating URLs?

**RESOLVED: DEFER. Not required by spec.**

Spec literalism (F-047 `verification[]`):
- V1: "returns 200 with appropriate Content-Type and non-empty body (or a JSON with `presigned_url` field)" — met regardless of status.
- V2: "Downloading and extracting the result yields valid Parquet files loadable by pandas" — this is a happy-path criterion testing status='done' datasets; it does NOT state that a 409 must be returned for non-done status.
- V3: "returns 404" for non-existent id — no mention of 409 for status != 'done'.

The spec has no verification criterion that requires 409/422 for `status='pending'` or `status='failed'` requests. Additionally, F-069 (Datasets page) explicitly specifies: "Pending/running datasets show a status badge **without** a download button" — the UX gate already lives in the frontend. A backend 409 guard is therefore doubly redundant for MVP.

**Ruling: No `status='done'` gate in this sprint. The endpoint returns presigned URLs regardless of status; the client gets a 404 from MinIO if objects don't exist. Document in agreed.md §Out of Scope.**

### OQ-4 — Route path collision: `GET /{id}/download` vs. `GET /{id}`

**RESOLVED: No collision. Declaration order is safe.**

FastAPI path parameters (`{id}`) match exactly one path segment and do NOT match across `/`. A request to `GET /api/datasets/42/download` has two segments after the router prefix (`42` and `download`), so it cannot match `GET /{id}` (which expects exactly one segment matching `int`). FastAPI will skip `GET /{id}` and correctly route to `GET /{id}/download`.

The declared order `GET "" → GET /{id} → GET /{id}/download → POST /{recipe_id}/materialize` is safe. No future ambiguity arises unless a route `GET /{id}/{something}` is added later with a conflicting pattern — but that is a future-sprint concern, not an MVP risk.

---

## Hard Invariant Compliance Review

| # | Invariant | Status |
|---|---|---|
| 1 | Lineage mandatory | ✅ N/A — read-only endpoint; no Commit record created |
| 2 | Storage separation + CAS | ✅ See note below |
| 3 | Schema frozen post-publish | ✅ N/A — no schema mutations |
| 4 | LLM calls through gateway | ✅ N/A — no LLM calls |
| 5 | Async SQLAlchemy | ✅ Handler uses `AsyncSession`, `await session.execute()`, `scalar_one_or_none()`; `get_s3_client()` yields an `aioboto3` async context-managed client; `generate_presigned_url` is `async def` (OQ-1 confirmed) |
| 6 | OpenAPI ↔ TS type sync | ✅ Listed in §3 and §11; manual codegen snippet provided; committed in same commit required |

**Invariant #2 explicit note (called out per review instructions):**
The endpoint generates presigned URLs whose `host:port` component is `minio:9000` — the internal Docker DNS name. This is technically exposed to the API client. This does NOT violate invariant #2: that invariant prohibits blob bytes in Postgres and requires content to live in MinIO/S3. Presigned URLs are metadata (URI references), not blob bytes. The URL hostname leakage is the intended MVP trade-off for server-bandwidth-free downloads on an internal-network deployment (design doc §11.1). An external-facing `MINIO_PUBLIC_ENDPOINT` setting is deferred post-MVP (OQ-2 ruling above). This decision MUST be explicitly documented in the §8 invariant #2 row of `agreed.md`.

---

## MinIO Key Layout Verification

Confirmed against `dagster/dagster_platform/hf_dataset_io_manager.py` lines 228, 243–290:
- `prefix = f"{obj.dataset_id}_{obj.version_tag}"` (line 228)
- `version_tag` is always `f"v{n}"` (e.g., `"v1"`) — confirmed from `routers/datasets.py` line `version_tag: str = f"v{n}"`
- Five objects: `{prefix}/data/train-00000.parquet`, `{prefix}/data/validation-00000.parquet`, `{prefix}/recipe.json`, `{prefix}/README.md`, `{prefix}/dataset_infos.json`
- Design doc §4.3 layout `s3://datasets/{dataset_id}_v{version}/` matches `42_v1/` exactly

The proposed `OBJECT_KEYS` construction in §6 is correct and matches the actual F-044 output. ✅

---

## Test Plan Coverage Check (F-045/F-046 pattern)

| Check | Present? | Test # |
|---|---|---|
| 200 happy path with JSON body | ✅ | #1 |
| Exact key set in response (no extra fields) | ✅ | #2 (`test_download_response_shape_exact`) |
| 404 not-found (bad id) | ✅ | #5 |
| 404 wrong-owner (no enumeration leak) | ✅ | #6 |
| 401 no token | ✅ | #7 |
| 422 non-integer id | ✅ | #8 |
| SQL `literal_binds` owner-scope structural assertion | ✅ | #9 |
| All 5 expected file names present | ✅ | #10 |
| Parquet file names specifically verified | ✅ | #3 |
| Well-formed presigned URL regex | ✅ | #4 |

Pattern alignment with S045/S046: ✅ All required patterns present.

---

## Findings

### MEDIUM-1 — Missing assertion on `Key` argument to `generate_presigned_url`

**Severity**: MEDIUM  
**Location**: §5, tests #3, #4, and #10

Tests #3, #4, and #10 verify the `name` field in the response and the URL shape, but **none of the 10 tests assert what `Key` value was actually passed to `generate_presigned_url`**. The `name` field in the response comes from the handler's `OBJECT_KEYS` construction; the `Key` argument to `generate_presigned_url` is set independently in the handler body. A handler with a key-construction bug — e.g., generating URLs for keys without the `{prefix}/` component, or using relative names instead of full keys — would pass all 10 tests because the mock always returns the same URL regardless of arguments.

This is the structural analog of S045's M1 (ensuring the SQL owner filter was actually applied to the COUNT query, not just the list query). Here, the structural assertion is: **the MinIO key passed to `generate_presigned_url` must match the full prefixed key `f"{row.id}_{row.version_tag}/{relative_name}"`**.

**Required fix**: Add key-argument verification to the test suite. Options:

**Option A** — Add assertions inside test #4 (`test_download_presigned_urls_are_well_formed`):
```python
# After asserting URL shape, also assert call arguments:
call_args = mock_s3.generate_presigned_url.call_args_list
assert len(call_args) == 5
called_keys = [c.kwargs["Params"]["Key"] for c in call_args]
# or positional: [c[1]["Params"]["Key"] or c[0][1]["Params"]["Key"] depending on how called]
assert f"{mock_dataset_id}_v1/data/train-00000.parquet" in called_keys
assert f"{mock_dataset_id}_v1/data/validation-00000.parquet" in called_keys
```

**Option B** — Add a new test #11 (`test_download_presigned_url_keys_match_prefix`):
```python
def test_download_presigned_url_keys_match_prefix():
    # ... setup with _make_dataset(id=42, version_tag="v1") ...
    # ... call endpoint ...
    calls = mock_s3.generate_presigned_url.call_args_list
    assert len(calls) == 5
    keys = [c.kwargs["Params"]["Key"] for c in calls]
    assert all(k.startswith("42_v1/") for k in keys)
    assert "42_v1/data/train-00000.parquet" in keys
    assert "42_v1/data/validation-00000.parquet" in keys
    assert "42_v1/recipe.json" in keys
    assert "42_v1/README.md" in keys
    assert "42_v1/dataset_infos.json" in keys
```

Either option is acceptable. Option B is preferred as it keeps test #4 focused on URL shape.

This finding requires a contractual change: §5 test plan must include this assertion, and §11 must note it as part of the codegen/test surface.

---

### LOW-1 — `storage/s3.py` docstring update not in §3 file changes

**Severity**: LOW  
**Location**: §3 file changes table

`apps/api/dataplat_api/storage/s3.py`'s `get_s3_client()` docstring currently reads: *"FastAPI dependency — yields an aioboto3 S3 client **for the sources bucket**."* F-047 reuses this dependency for a second bucket (datasets). The function is correctly general-purpose (bucket is passed as a parameter to each call); only the docstring is misleading.

The §3 file changes table should add a `storage/s3.py` row with status `edit` and reason: *"Update `get_s3_client()` docstring to remove 'for the sources bucket' specificity — function is now used for both sources and datasets buckets."*

This is a 1-line docstring change. It does not require a new test, does not affect behavior, and does not require codegen.

---

### LOW-2 — §8 invariant #2 row does not explicitly acknowledge presigned URL hostname exposure

**Severity**: LOW  
**Location**: §8 hard invariants table, row 2

The current §8 invariant #2 analysis states only: *"No blob bytes are fetched into the API server and no content is written to Postgres."* This is correct but incomplete. It does not address the specific concern raised by the review mandate: presigned URLs embed `http://minio:9000` (internal Docker hostname) in the body returned to the client.

**Required fix**: Expand the §8 invariant #2 "One-line reason" to: *"Metadata (prefix, version_tag) read from Postgres; presigned URLs (URI references, not bytes) returned to client. URL hostname is `minio:9000` (internal Docker DNS) — acceptable for MVP internal-network deployment per design doc §11.1; `MINIO_PUBLIC_ENDPOINT` deferred to post-MVP ops (OQ-2 ruling)."*

This makes the MVP trade-off part of the explicit compliance record for this sprint, so a future reviewer does not need to re-derive it.

---

### NIT-1 — `3600` TTL should be a named constant

**Severity**: NIT  
**Location**: §6 handler code, §5 test assertions

The presigned URL TTL appears as the magic number `3600` in three places: the `generate_presigned_url(ExpiresIn=3600)` call, the `DatasetDownloadResponse(expires_in_seconds=3600)` value, and test assertions (`body["expires_in_seconds"] == 3600`). A module-level constant makes the coupling explicit:

```python
# In routers/datasets.py
_PRESIGN_TTL_SECONDS: int = 3600  # 1 hour; deferred to configurable via MINIO_PRESIGN_TTL_SECONDS (F-???).
```

Tests then reference `_PRESIGN_TTL_SECONDS` rather than the literal `3600`, or the constant is exported and imported in the test module. This is advisory — not blocking.

---

## Summary

| Finding | Severity | Location | Blocking? |
|---|---|---|---|
| Missing `Key` argument assertion on `generate_presigned_url` calls | **MEDIUM** | §5 test plan | Yes — add test #11 (or extend test #4) |
| `storage/s3.py` docstring update not listed in §3 | LOW | §3 file changes | No — add 1-line docstring edit |
| §8 invariant #2 row does not address presigned URL hostname leakage | LOW | §8 invariants table | No — expand row 2 narrative |
| `3600` TTL should be named constant | NIT | §6 / §5 | No |

---

## CHANGES_REQUESTED

The proposal is structurally sound: the MinIO key layout is correct (verified against `hf_dataset_io_manager.py`), all four OQs are resolvable (and resolved above), all required test patterns from S045/S046 are present (422, SQL literal_binds, 404 collapse, no-extra-fields, 401), invariant #5 (async) is correctly implemented (`await generate_presigned_url` is confirmed correct for aiobotocore 2.25.1), and invariant #6 (codegen) is explicitly planned.

The single blocking item is **MEDIUM-1**: the test suite has a structural coverage gap — no assertion verifies that the correct MinIO object keys are passed to `generate_presigned_url`. This is the key correctness invariant for the endpoint (equivalent to the SQL owner-filter assertion in S045). It must be addressed before implementation.

Address MEDIUM-1 and fold the two LOW items and four OQ resolutions into `agreed.md`. The NIT is advisory.

---

## Round 2

**Reviewer**: leader (Mode A)
**Date**: 2026-06-04
**Proposed.md revision reviewed**: Rev 2
**Sources consulted**: rev-2 proposed.md (all 12 sections), round-1 feedback.md

---

### Round-1 Finding Resolutions

All four round-1 findings are resolved **in the body** of rev-2, not merely in the §12 summary. Verification:

| Finding | Severity | Resolved in body? | Where |
|---|---|---|---|
| **M1** — No `Key=` assertion on `generate_presigned_url` | MEDIUM | ✅ RESOLVED | §5 test table row #11 + full code shape block (lines 140–157). Uses exact set equality `keys == {…}` — stronger than the `in`-membership Option B suggested in round 1. |
| **L1** — `storage/s3.py` docstring not in §3 file changes | LOW | ✅ RESOLVED | §3 table: `storage/s3.py` row added with status `edit` and verbatim reason: "Update `get_s3_client()` docstring to remove 'for the sources bucket' specificity — function is now used for both sources and datasets buckets." |
| **L2** — §8 invariant #2 row silent on presigned URL hostname | LOW | ✅ RESOLVED | §8 row #2 "One-line reason" expanded to: "Metadata (prefix, version_tag) read from Postgres; presigned URLs (URI references, not bytes) returned to client. URL hostname is `minio:9000` (internal Docker DNS) — acceptable for MVP internal-network deployment per design doc §11.1; `MINIO_PUBLIC_ENDPOINT` deferred to post-MVP ops (OQ-2 ruling)." Verbatim match to the required fix text. |
| **N1** — `3600` TTL is a bare magic number | NIT | ✅ RESOLVED | §3 routers row states `_PRESIGN_TTL_SECONDS: int = 3600` module-level constant added and all 5 `generate_presigned_url` calls reference it. §6 code sketch shows `ExpiresIn=_PRESIGN_TTL_SECONDS`. |

### Open Question Resolutions

All four OQs baked into the body (not only §12):

| OQ | Resolved in body? | Where |
|---|---|---|
| OQ-1 (`generate_presigned_url` async?) | ✅ RESOLVED ASYNC | §5 mock pattern uses `AsyncMock` unconditionally; §6 uses `await`; §8 invariant #5; §10 unambiguous |
| OQ-2 (`MINIO_PUBLIC_ENDPOINT`) | ✅ RESOLVED DEFER | §7 full rationale; §8 invariant #2; §9 Out of Scope bullet; §10 |
| OQ-3 (gate on `status == "done"`) | ✅ RESOLVED NO GATE | §9 Out of Scope bullet with spec-citation reasoning; §10 |
| OQ-4 (route collision `/{id}` vs `/{id}/download`) | ✅ RESOLVED NO COLLISION | §3 routers row with declaration order; §10 |

### New Findings

**NIT-2 (cosmetic, non-blocking)** — §5 test table row #1 description still writes `expires_in_seconds == 3600` as a literal integer. Now that `_PRESIGN_TTL_SECONDS` is a named constant in `routers/datasets.py`, the test description (and the corresponding assertion in the implementation) ideally reads `expires_in_seconds == _PRESIGN_TTL_SECONDS`. This is a purely documentary inconsistency in the contract text; it does not affect correctness and is not blocking.

No new significant findings.

### Summary

| Finding | Severity | Status |
|---|---|---|
| M1 — Missing `Key=` arg assertion | MEDIUM | ✅ RESOLVED |
| L1 — `storage/s3.py` not in §3 | LOW | ✅ RESOLVED |
| L2 — §8 invariant #2 silent on hostname | LOW | ✅ RESOLVED |
| N1 — `3600` magic number | NIT | ✅ RESOLVED |
| OQ-1 async/sync | OQ | ✅ RESOLVED |
| OQ-2 MINIO_PUBLIC_ENDPOINT | OQ | ✅ RESOLVED |
| OQ-3 status gate | OQ | ✅ RESOLVED |
| OQ-4 route collision | OQ | ✅ RESOLVED |
| NIT-2 literal 3600 in §5 description | NIT | New (non-blocking) |

---

## APPROVED

The rev-2 contract is complete and implementation-ready. All round-1 blocking and non-blocking items are addressed in the body of the document, the hard invariants are fully satisfied, the test suite achieves structural correctness coverage equivalent to the S045/S046 bar (owner-scope SQL assertion, MinIO key-argument assertion, exact response shape, all error paths, codegen hard requirement stated in three sections), and zero open questions remain. Proceed to `agreed.md` and implementation.
