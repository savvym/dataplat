# S019-F-019 — Reviewer Feedback (Mode A)

**Status:** CHANGES_REQUESTED
**Date:** 2026-05-25
**Reviewer:** Claude (independent)

---

## Calibration Checks

- **CAL-1:** N/A — no changes to `apps/api/`. The Dagster asset uses sync psycopg2 in `dagster/`, which is outside the scoped invariant.
- **CAL-2:** N/A — no LLM calls in this sprint.
- **CAL-3:** N/A — no API surface changes; no router or schema files touched.
- **CAL-4:** N/A — no Commit object is created in this sprint. The `document_variant` row records lineage-adjacent metadata (source_id, extractor identity, config_hash, dagster_run_id) per the invariant-#1 PARTIAL assessment in §9 — acceptable for MVP.
- **CAL-5:** N/A — the S3 key `{source_id}/extract_mineru/doc.docling.json` is identity-based, not CAS. This is consistent with the existing `sources/{source_id}/original.pdf` pattern, and DoclingDocument JSON is mutable-per-run output (not a content-addressed blob). The contract does not claim CAS for this path. Acceptable.
- **CAL-6:** N/A — no schema publish.
- **CAL-7:** N/A — no Bronze adapter work.
- **CAL-8:** PASS — no out-of-scope MVP features introduced.
- **CAL-9:** PASS — `dagster/dagster_platform/` does not import from `plugins/` or `apps/api/`.
- **CAL-10:** FAIL — see finding #5 below.
- **CAL-11:** Applied — concrete evidence cited for each finding.

---

## Findings

### FINDING 1 — HIGH: `binary_hash` SILENTLY TRUNCATES to 64-bit (proposed.md §4 / §10-OQ2)

**Evidence (live):** The `DocumentOrigin.parse_hex_string` validator (confirmed via live introspection of the running dagster-webserver container) converts the hex sha256 string to an integer, then MASKS it to 64 bits:

```python
return hash_int & 0xFFFFFFFFFFFFFFFF   # TODO be sure it doesn't clip uint64 max
```

A sha256 is 256 bits. The mask discards the upper 192 bits. The model construction and `model_dump_json()` will NOT raise — they will silently succeed. The stored `binary_hash` in the JSON will be a truncated 64-bit integer, not the full sha256.

**This is not a crash risk — it is a data-integrity risk.** The contract's §10-OQ2 note says "serializes as an integer (the int representation of the hex)" and presents this as correct behavior. It is not: the integer is a truncated hash. Downstream consumers (F-020, F-022, F-054) that rely on `binary_hash` for integrity checks will see a useless value.

**Remedy:** The contract must acknowledge the truncation explicitly. Two acceptable options:
  - (a) Drop `binary_hash` entirely from the `DocumentOrigin` constructor call. It is not required per the schema (only `name` is required), and omitting it avoids communicating false precision. Recommended.
  - (b) Document clearly in comments and OQ2 that `binary_hash` is a truncated 64-bit representation, with an explicit note that it CANNOT be used for integrity verification. This is acceptable if the implementer decides keeping the field is useful despite truncation.

The contract currently presents the truncated value as "the int representation of the sha256" which is misleading. Fix the prose even if option (b) is chosen.

---

### FINDING 2 — HIGH: `MINIO_USER` / `MINIO_PASS` are NOT DEFINED in the `extract)` layer scope (checks.sh §7 V2)

**Evidence:** `MINIO_USER` and `MINIO_PASS` are set inside the `buckets)` case (lines 157–158) and `sources)` case (lines 792–793) of `checks.sh`. They are **not** set in the `runs)` case, the `operators)` case, or any global scope. The proposed `extract)` layer's V2 check uses:

```bash
-e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
```

If `extract)` is run standalone (`bash checks.sh extract`), `MINIO_USER` and `MINIO_PASS` are unset shell variables. With `set -euo pipefail` (line 16 of checks.sh), referencing an unset variable under `-u` causes the script to abort immediately with `unbound variable`. V2 will never reach the boto3 python check — it will crash the entire `extract)` layer at the variable expansion.

**Remedy:** Add the following two lines at the top of the `extract)` case body, before V2:

```bash
MINIO_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_PASS="${MINIO_ROOT_PASSWORD:-devpassword}"
```

This mirrors the pattern in `buckets)` and `sources)` exactly.

---

### FINDING 3 — MEDIUM: `all)` chain placement not specified — must be after `operators` (checks.sh §7 / proposed.md §2)

**Evidence:** The contract (§2, §7) states "Add `bash "$0" extract` to `all)` chain after `operators`." The current `all)` chain ends with `bash "$0" operators`. The contract correctly identifies the insertion point. However, the contract does not specify that `extract)` must also follow `runs)` (F-018 backfill launching is a prerequisite — the `extract)` layer triggers its own backfill, but the `runs)` layer tests F-018 wiring which the `extract)` layer depends on indirectly). 

This is LOW-risk since `all)` runs sequentially, but the implementer should confirm the final line in `all)` is:

```bash
bash "$0" operators   # F-015 / F-016 / F-017
bash "$0" extract     # F-019  ← ADD HERE
```

**Remedy:** Explicitly state in the contract that `extract` is appended after `operators` (the current last line). No other change.

---

### FINDING 4 — MEDIUM: Failure ordering — S3 write before DB insert creates orphan objects on DB failure (proposed.md §3, step 6 before step 7)

**Evidence:** Step 6 (`write_document_json`) runs before step 7 (`insert_document_variant`). If the DB insert fails (e.g. psycopg2 connection error, FK violation if `source_id` was deleted between upload and extraction), the S3 object is written but no DB row exists. This is an orphan: the document JSON exists in MinIO with no pointer from Postgres. For MVP this is tolerable (no F-054 DoclingDocIOManager yet), but the contract currently does not acknowledge this ordering decision or its consequence.

**Remedy:** Add one sentence to §3 or §9 acknowledging: "S3 write precedes DB insert; a DB failure after a successful S3 write leaves an orphan doc.docling.json in MinIO. This is acceptable for MVP (no atomicity guarantee); F-054 will handle partial-failure cleanup." This is documentation only — no code change required.

---

### FINDING 5 — LOW (CAL-10): No unit tests for pure helper functions in `extractor.py`

**Evidence:** The contract creates `dagster/dagster_platform/extractor.py` with pure functions (`build_docling_document`, `estimate_page_count`, `config_hash` computation). The only verification is integration-level (`checks.sh extract`). CAL-10 requires at least one success case and one failure case per new feature. For integration-only verification this is partially satisfied by V1-proxy through V4, but the pure functions (especially `estimate_page_count`'s regex logic and `build_docling_document`'s field construction) are independently testable without Docker.

Prior plugin sprints (F-011/F-012) had `apps/api/tests/` unit tests. This sprint is in `dagster/` not `apps/api/`, so the test runner location differs.

**Remedy:** Add a `dagster/tests/test_extractor.py` with:
  - One test: `estimate_page_count` returns 1 for the synthetic PDF fixture bytes used in `checks.sh`.
  - One test: `estimate_page_count` returns 0 for `b""` (empty bytes / malformed input).
  - One test: `build_docling_document` returns an object whose `model_dump_json()` succeeds and `schema_name == "DoclingDocument"`.

These tests run without the Docker stack and provide confidence before the E2E check. If the project has no `dagster/tests/` directory yet, the contract should specify creating it with a `pyproject.toml` or note that tests are added under the existing test runner.

---

### FINDING 6 — NIT: `POSTGRES_DB` env var injected into dagster services is redundant (proposed.md §5a)

**Evidence:** The `PLATFORM_DB_URL` env var already encodes the database name (`/platform`). The contract also adds `POSTGRES_DB: ${POSTGRES_DB:-platform}` to the dagster services. The asset only reads `PLATFORM_DB_URL` (confirmed in §5: `psycopg2.connect(os.environ["PLATFORM_DB_URL"])`). `POSTGRES_DB` is unused by the asset logic. Adding it causes no harm but is dead config.

**Remedy:** Drop `POSTGRES_DB` from the dagster service env additions. Only `MINIO_ENDPOINT`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, and `PLATFORM_DB_URL` are needed.

---

## Contract Correctness Confirmations

The following items were explicitly checked against ground truth and are CORRECT:

- **NOT NULL columns:** `extractor_name`, `extractor_version`, `config_hash`, `storage_prefix` are all `nullable=False` in `models.py:134–137`. The INSERT sets all four — no NULL constraint violation.
- **Unique constraint:** `uq_document_variant_source_extractor_config` on `(source_id, extractor_name, config_hash)` at `models.py:115–118`. The `ON CONFLICT (source_id, extractor_name, config_hash) DO NOTHING` matches exactly.
- **Partial unique index:** `idx_doc_canonical` is `unique=True, postgresql_where=text("is_canonical")` at `models.py:121–125`. The SELECT-then-INSERT-in-one-transaction logic is sound for the single-producer MVP case (no concurrent extractions for the same source in this stack).
- **Dagster Dockerfile current deps:** Confirmed `dagster==1.11.16`, `dagster-webserver==1.11.16`, `dagster-postgres==0.27.16`, `psycopg2-binary==2.9.10`. The contract adds only `boto3==1.37.38` and `docling-core==2.77.0` — minimal and correct.
- **Compose services lack MINIO_*/platform-DB env:** Confirmed — `dagster-webserver` and `dagster-daemon` currently have only `POSTGRES_USER/PASSWORD/HOST/PORT/DB_DAGSTER`. The contract's additions are correct (add to webserver and daemon; NOT to workers which run `sleep infinity`).
- **PLATFORM_DB_URL driver:** `postgresql://` (sync psycopg2 driver) — correct for psycopg2.connect(). Invariant #5 scoped to `apps/api/` — confirmed in CLAUDE.md. No violation.
- **MINIO_* values match fastapi service:** fastapi uses `MINIO_ENDPOINT: ${MINIO_ENDPOINT:-minio:9000}`, `MINIO_ROOT_USER: ${MINIO_ROOT_USER:-minioadmin}`, `MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:-devpassword}`. The contract's dagster additions use identical defaults.
- **Poll-to-SUCCESS logic:** Checks `COMPLETED_SUCCESS` (not just terminal), then separately asserts per-partition run `status=SUCCESS`. Timeout 120s / 3s intervals. This is non-vacuous and correctly distinguishes COMPLETED_FAILED.
- **V2 MINIO_USER/MINIO_PASS source:** FAIL — see Finding #2 above. The pattern from `buckets)` and `sources)` must be replicated inside `extract)`.
- **`binary_hash` crash risk:** NOT a crash — model construction and `model_dump_json()` succeed. The risk is silent truncation to 64 bits — see Finding #1.
- **`is_canonical` logic:** SELECT COUNT(*) + INSERT in one transaction with ON CONFLICT DO NOTHING. For MVP (single-producer, no concurrent extractions), this is correct. The first run sets `TRUE`; a second run with the same config_hash hits ON CONFLICT DO NOTHING (no update to `is_canonical`). A second run with a DIFFERENT config_hash would set `FALSE` correctly. Logic is sound.
- **`fastapi` container has `boto3`:** Confirmed — `apps/api/dataplat_api/storage/s3.py` uses `aioboto3` which depends on `boto3`. The fastapi container has boto3. V2's python boto3 invocation inside `fastapi` container is valid.
- **Plugin boundary note §9:** The `dagster/` asset pattern for MVP (instead of `plugins/` Processor protocol) is noted and not re-litigated per scope decision.

---

## Summary

**CHANGES_REQUESTED.** Two changes are required before APPROVED:

1. **(HIGH — Finding #1):** Fix the `binary_hash` prose in §4/§10-OQ2. Either drop `binary_hash` from the constructor call (recommended) or explicitly document that it is a TRUNCATED 64-bit value, not a full sha256, and cannot be used for integrity checks.

2. **(HIGH — Finding #2):** Add `MINIO_USER="${MINIO_ROOT_USER:-minioadmin}"` and `MINIO_PASS="${MINIO_ROOT_PASSWORD:-devpassword}"` inside the `extract)` case body in `checks.sh`. Without this, V2 will abort with `unbound variable` under `set -euo pipefail` when `extract` is run standalone.

Findings #3 (NIT on all) placement), #4 (S3-before-DB documentation), and #6 (redundant POSTGRES_DB) are documentation/prose fixes only. Finding #5 (unit tests) is LOW — the implementer should add `dagster/tests/test_extractor.py` with the three tests listed, but this may be resolved as part of implementation if the sprint contract is updated to include it in §2 Files Changed.

---

## Iteration 2 Convergence (2026-05-25)

### Fix #1 — binary_hash: RESOLVED

`§4` code now constructs `DoclingDocument(name=f"source_{source_id}")` with no `DocumentOrigin` at all. The example JSON shows `"origin": null`. `§10-OQ2` is gone, replaced by a one-paragraph rationale in §4 citing `source.sha256` (F-011) as the authoritative hash. No `binary_hash` reference remains anywhere in the contract. Fix confirmed — no further action.

### Fix #2 — MINIO_USER/MINIO_PASS: RESOLVED

`§7 Setup` block now opens with:
```bash
MINIO_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_PASS="${MINIO_ROOT_PASSWORD:-devpassword}"
```
These appear before the V2 reference to `${MINIO_USER}` / `${MINIO_PASS}`. Pattern mirrors `buckets)` exactly. Fix confirmed — no further action.

### Fix #3 — Unit tests: BLOCKER FOUND — pytest NOT in dagster image

The contract's §2a specifies running tests via:
```bash
docker compose exec -T dagster-webserver python -m pytest /app/dagster/tests/test_extractor.py -q
```

**Verified live against the running `dataplat-dagster-webserver-1` container:**
```
$ docker exec dataplat-dagster-webserver-1 python -m pytest --version
/usr/local/bin/python: No module named pytest
```
```
$ docker exec dataplat-dagster-webserver-1 pip show pytest
WARNING: Package(s) not found: pytest
```

`pytest` is not installed in the current Dagster image. The Dockerfile pip install list (`dagster`, `dagster-webserver`, `dagster-postgres`, `psycopg2-binary`, `boto3`, `docling-core`) does not include `pytest`. The `extract)` layer will abort at the unit-test step with "No module named pytest" before any E2E check runs.

**Required fix — one of two options:**

**(a) Preferred:** Add `pytest` to the Dockerfile pip install:
```dockerfile
RUN pip install --no-cache-dir \
    dagster==1.11.16 \
    dagster-webserver==1.11.16 \
    dagster-postgres==0.27.16 \
    psycopg2-binary==2.9.10 \
    boto3==1.37.38 \
    docling-core==2.77.0 \
    pytest
```
Note: `dagster` itself has a test-time dependency on `pytest` which is often present in dev images, but it is NOT a runtime dependency and is not installed by the current Dockerfile. A pinned `pytest>=7.0,<9` is sufficient.

**(b) Alternative:** Replace the `python -m pytest` invocation with a stdlib-only self-test script (`python /app/dagster/tests/run_tests.py`) that uses bare `assert` statements and exits non-zero on failure, requiring no external dependencies. This avoids the image rebuild for tests but loses pytest's reporting quality.

Option (a) is recommended — the image rebuild is already required for `boto3` and `docling-core`, so adding `pytest` is zero additional operational cost.

**The contract §2 (Files Changed) and §5a (Dockerfile) MUST be updated to include `pytest` in the pip install, and §2a must reflect this.** Until then, Fix #3 is not resolved.

---

## Final Verdict (Iteration 2)

**CHANGES_REQUESTED** — one remaining blocker:

- **BLOCKER (Finding #3 revised):** `pytest` is not installed in the Dagster image. `docker compose exec dagster-webserver python -m pytest ...` will immediately fail with "No module named pytest", breaking the entire `extract)` layer before any assertion runs. Add `pytest` to `docker/dagster/Dockerfile`'s pip install list (and update §2 / §5a accordingly). This is a one-line fix with zero operational overhead since the image rebuild is already required.

Fixes #1 and #2 are fully resolved. Once the Dockerfile is updated with `pytest`, all three Mode A findings will be closed and the contract may be APPROVED.

---

## Iter 3 — convergence (leader-folded)

Reviewer iter-2 confirmed HIGH#1 (binary_hash dropped) and HIGH#2 (MINIO_USER/MINIO_PASS defined in extract) block) RESOLVED, and surfaced one remaining blocker: pytest is NOT in the Dagster image (confirmed live: `python -m pytest` → No module named pytest), so fix #3's test invocation would be dead-on-arrival. Reviewer gave a one-line remedy (add pytest to the Dockerfile pip list; rebuild already required).

Leader folded the remedy directly into proposed.md (contract-content convention): §5a pip list now includes `pytest==8.3.4` (with `pytest>=8,<9` fallback note), and the §2 Dockerfile row mentions pytest. This is the exact pre-blessed remedy; no further implementer/reviewer round needed.

**FINAL VERDICT: APPROVED** (all 3 iter-1 findings + the iter-2 pytest gap resolved).
