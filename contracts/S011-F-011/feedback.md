# S011-F-011 — Mode A Review Feedback (Iter 2)

**Reviewer:** independent reviewer (Claude)
**Date:** 2026-05-25
**Contract under review:** `contracts/S011-F-011/proposed.md` (iter-2 revision)
**Calibration document:** `verify/reviewer-calibration.md` (CAL-1 through CAL-11 sweep below)

---

## Iter-1 → Iter-2 fix verification

Each of the eight iter-1 findings is confirmed resolved:

### Fix 1 — BLOCKER: temp partition key (§3-D3 ~L112, §4 step 6 ~L204/208)

RESOLVED. Both locations now prescribe `f"src_tmp_{uuid.uuid4().hex}"` exclusively. The `id(source)` and `id(object())` forms are gone. Both occurrences carry an explicit "Do NOT use `id(source)` or `id(object())`" warning. The two locations are consistent with each other. The `import uuid` note appears in §3-D3 (L112: "requires `import uuid` at the top of the handler module") and in §4 step 6 (L198, L208).

### Fix 2 — HIGH: atomicity mechanism (§3-D3 ~L117, §4 step 10 ~L222, §5 ~L254)

RESOLVED. All three locations now accurately describe the mechanism: `AsyncSession.__aexit__` calls `session.close()`, NOT `session.rollback()`; the uncommitted transaction is implicitly rolled back when the connection returns to the pool. All three locations explicitly forbid both `try/except` swallowing the S3 exception and an explicit `await session.rollback()` call. The language is consistent across §3-D3, §4, and §5.

### Fix 3 — HIGH: `%%EOF` vs `%%%%EOF` (§6 ~L321, §7 ~L402)

RESOLVED. Both locations now read `b"startxref\n182\n%%EOF\n"` (exactly two `%` signs). Verified: Python byte literals do not interpret `%`; two `%` signs produce two literal `%` bytes — the correct PDF EOF marker. Both fixtures are byte-identical and will produce the same sha256. The bash double-quote wrapper around the §7 `python3 -c "..."` call does not process `%`, confirmed by direct testing.

### Fix 4 — MEDIUM: OQ-1 converted to decision (§3-D5 ~L128, §9 OQ table)

RESOLVED. §3-D5 now opens with "DECIDED: `dagster_partition_key = f"src_{source.id}"`" and fully explains the design-doc vs. feature_list.json rationale. OQ-1 is absent from §9 — grep across the entire document finds zero occurrences of "OQ-1". The §9 table now starts at OQ-2, which is the unchanged original OQ-2 entry (MinIO creds). No dangling references.

### Fix 5 — MEDIUM: MINIO_SOURCES_BUCKET compose documentation (§3-D2 ~L88)

RESOLVED. §3-D2 now explicitly states which of the four settings are injected by compose (MINIO_ENDPOINT, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD — lines 223-225 of docker-compose.dev.yml) and which is not (MINIO_SOURCES_BUCKET), together with the reasoning that the Python default `"sources"` matches the bucket created by minio-init. No compose change needed and that is now documented.

### Fix 6 — LOW: storage_uri placeholder safety (§3-D3 ~L111)

RESOLVED. §3-D3 now has an explicit bullet: "`storage_uri` has NO UNIQUE constraint, so the constant placeholder `'__pending__'` is safe under concurrent requests — multiple in-flight transactions can each hold `'__pending__'` in their own uncommitted transaction without colliding."

### Fix 7 — LOW: commit() auto-flushes dirty attrs (§3-D3 ~L114)

RESOLVED. §3-D3 now explicitly states: "`session.commit()` auto-flushes all dirty attributes before committing, so the overwritten `storage_uri` and `dagster_partition_key` values set in steps 7–8 persist in the same commit with no second explicit `await session.flush()` needed. The implementer MUST set both fields after flush and before commit, or the row persists with the placeholder values." The warning is appropriately strong.

### Fix 8 — NIT: full `all)` chain (§7 ~L494-L509)

RESOLVED. §7 now shows all 12 entries in the complete updated `all)` chain, with `sources` correctly positioned between `collections` and `buckets`.

---

## New-inconsistency sweep

Checked for anything the iter-2 edits could have introduced:

**uuid import not listed in §2 imports summary:** §2 (L44) lists the new imports for `routers/sources.py` as `File, Form, UploadFile, Source ORM model, SourceUploadResponse, get_s3_client`. It does not list `uuid` or `hashlib`. Both are standard library modules; their omission from a high-level summary table is not a correctness problem — §4 step 6 says "requires `import uuid`" and step 3 uses `hashlib.sha256`. An implementer following §4 will import both. This is a very minor summary incompleteness, not an actionable defect.

**OQ numbering gap:** §9 now starts at OQ-2 (OQ-1 removed but numbering not renumbered to OQ-1 through OQ-4). No code or prose references OQ-N identifiers, so the gap is cosmetic only. No correctness impact.

**Two `id(source)`/`id(object())` "do NOT use" warnings:** Both §3-D3 and §4 step 6 warn against the old approach. The redundancy is a feature, not a bug — it reduces the chance an implementer reads only one section and misses the constraint.

**§3-D3 and §4 step 10 atomicity text:** Both sections now independently describe the same `close()` mechanism. The descriptions are consistent and reinforce each other. No contradiction.

**checks.sh §7 PDF bytes:** Verified by running the Python fixture locally — `b'startxref\n182\n%%EOF\n'` in a Python byte literal produces exactly two `%` chars, the correct PDF EOF sequence. The sha256 of the §6 `_MINIMAL_PDF` constant and the §7 bash-generated PDF file will be identical. UPLOAD-V3 will pass.

**`all)` chain count:** The §7 chain shows 12 entries (smoke, infra, backend, frontend, contract, migration, auth, collections, sources, buckets, dagster, runs) — matching the existing 11-entry chain from `verify/checks.sh` plus the new `sources` entry. Correct.

---

## Calibration sweep (Mode A — iter 2)

- **CAL-1 (Async session):** PASS — `await session.flush()`, `await session.commit()`, synchronous `session.add()`. No sync API prescribed anywhere.
- **CAL-2 (LLM gateway):** N/A — no LLM calls in F-011.
- **CAL-3 (OpenAPI sync):** PASS — §2 includes `packages/api-types/openapi.json`; §8 gives the verified regen command (identical to S009/S010 precedent); same-commit requirement stated.
- **CAL-4 (Lineage completeness):** N/A — `source` row, no Commit/DocumentVariant created.
- **CAL-5 (CAS path discipline):** PASS — id-keyed storage for raw files is correct per design doc lines 252 and 425; sha256 is computed and stored in Postgres. Invariant #2 does not apply to raw source uploads.
- **CAL-6 (Schema freeze post-publish):** N/A — no Silver/Gold commit.
- **CAL-7 (Bronze faithfulness):** N/A — no Bronze adapter.
- **CAL-8 (MVP scope discipline):** PASS — F-012, F-013, F-014 explicitly deferred in §1; no Dagster calls, no list route, no detail route prescribed.
- **CAL-9 (Plugin isolation):** N/A — no plugin.
- **CAL-10 (Test coverage):** PASS — 13 tests covering all four F-011 verification criteria plus auth gate, edge cases (415, 422), and failure mode (S3 down). Well above minimum bar.
- **CAL-11 (Bias check):** Applied — no vague approval; all eight prior findings confirmed resolved with line-level evidence; new-inconsistency sweep completed with specific checks.

---

## DECISION: APPROVED

All eight iter-1 findings are resolved. No new inconsistencies were introduced. The contract is internally consistent, prescribes a correct implementation, respects all six hard invariants, stays within F-011 scope, and provides concrete verifications for all four feature criteria.

**Risks the implementer should watch for during build (not blockers — informational):**

1. The `import uuid` and `import hashlib` must both appear in `routers/sources.py` — the §2 summary does not list them explicitly, but §4 steps 3 and 6 make them mandatory. Do not forget either.
2. The overwrite of `source.storage_uri` and `source.dagster_partition_key` (steps 8-9) must happen after `await session.flush()` and before `await session.commit()` — `session.commit()` will auto-flush them, but only if they have been set on the object. Setting them is mandatory; forgetting produces a row with `"__pending__"` values in Postgres.
3. The S3 `put_object()` call must not be wrapped in any `try/except` that swallows the exception. Let it propagate; the session context manager handles cleanup.
