# S003-F-003 Mode B Review (Final)

**Reviewer:** Claude (Mode B, code review)
**Date:** 2026-05-22
**Commit range reviewed:** `2dd3d6a..8e1e22b`
**Contract:** `contracts/S003-F-003/agreed.md`

---

## DECISION: APPROVED

---

## Calibration cases

| CAL-N | Status | Evidence |
|---|---|---|
| CAL-1 (async SQLAlchemy) | N/A | Zero `.py` files changed in `apps/api/dataplat_api/`. `git diff 2dd3d6a..8e1e22b --name-only` lists no Python sources. Only `pyproject.toml` and `uv.lock` changed in `apps/api/`. |
| CAL-2 (LLM gateway) | N/A | No LLM SDK imports anywhere in the diff. `aioboto3` is an S3 client, not an LLM SDK. |
| CAL-3 (OpenAPI sync) | N/A | No routes added, no Pydantic schemas changed. `make codegen` correctly not run. Agreed.md §6 explicitly excludes this. |
| CAL-4 (lineage completeness) | N/A | No Commit objects created. This sprint is infra-only. |
| CAL-5 (CAS path discipline) | PASS | V2 uses `Key='test.txt'` — flat path, verification only. The `checks.sh` comment at the V2 block explicitly states: "IMPORTANT: 'test.txt' is a flat key for verification ONLY. Production code (F-011+) MUST use CAS paths (sha256(content) layout) per CLAUDE.md hard invariant #2 and CAL-5." No production code in this diff uses any storage paths at all. |
| CAL-6 (schema freeze) | N/A | No Silver/Gold schema changes. |
| CAL-7 (Bronze faithfulness) | N/A | No Bronze adapter code in scope. |
| CAL-8 (MVP scope) | PASS | No bucket policies (`mc policy`), no versioning (`mc version`), no Lance schema setup, no public buckets. All explicitly deferred in `agreed.md §6`. No Celery, DinD, ACL, or other CLAUDE.md-banned items. |
| CAL-9 (plugin isolation) | N/A | No plugin code in this sprint. |
| CAL-10 (test coverage) | N/A — infra sprint | Infra sprint. Integration verification via `checks.sh buckets` (V1 + V2) is the correct substitute. No unit tests needed for a compose init service. |
| CAL-11 (bias check) | Applied | Two deviations from agreed.md are documented below. Each was evaluated on its merits. Approval is based on concrete file:line evidence, not on the implementer's self-assessment. |

---

## Contract criteria — agreed.md §2 (scope table)

| Criterion | Status | Evidence |
|---|---|---|
| `docker/docker-compose.dev.yml` — curl healthcheck + minio-init service | PASS | `docker-compose.dev.yml:81–86` adds `healthcheck: test: ["CMD-SHELL", "curl -fsS http://localhost:9000/minio/health/live || exit 1"]` with `interval:5s, timeout:5s, retries:12, start_period:10s`. `minio-init` service added at lines 94–110 with `image: minio/mc:RELEASE.2025-04-16T18-13-26Z`, correct `depends_on: minio: condition: service_healthy`, `restart: "no"` (quoted string — YAML 1.1 safe, per inline comment at line 91). |
| `docker/minio/init-buckets.sh` — mc alias + 5 buckets + exit 0 | PASS | File created at `docker/minio/init-buckets.sh`. `set -eu` at line 15. `mc ready "${ALIAS}"` at line 20. Five `mc mb --ignore-existing` calls at lines 23–30. `exit 0` at line 33. |
| `verify/checks.sh` — buckets layer + all) updated | PASS | `buckets)` case added at line 123. V1 exact-match check via `grep -qxF` at line 147. V2 PUT+HEAD+DELETE round-trip at lines 157–170. `bash "$0" buckets` added to `all)` target at line 175. |
| `apps/api/pyproject.toml` — add `aioboto3==15.5.0` | PASS | `pyproject.toml` lines 20–23 add `"aioboto3==15.5.0"` with explanatory comment. |
| `apps/api/uv.lock` — regenerated | PASS | `uv.lock` diff adds 664 lines. `aioboto3 15.5.0` entry present with PyPI URL and hash. `boto3 1.40.61` present as transitive dep. |
| No new routes / no `make codegen` | PASS | Zero Python files changed in `apps/api/dataplat_api/`. |

---

## Deviation 1 — `documents_vlm` → `documents-vlm` (forced, unavoidable)

**Assessment: APPROVED as forced deviation. Requires addendum note.**

The agreed.md §4 bucket table names the bucket `documents_vlm` (underscore). S3 / MinIO bucket naming rules (RFC 1123 host label syntax) prohibit underscores — only lowercase letters, digits, and hyphens are permitted. `mc mb documents_vlm` would fail with "Bucket name contains invalid characters". The implementer renamed the bucket to `documents-vlm`.

Evidence in the diff:
- `docker/minio/init-buckets.sh:28`: `mc mb --ignore-existing "${ALIAS}/documents-vlm"` — with inline comment explaining the constraint.
- `verify/checks.sh:139–140`: inline comment "NOTE: 'documents-vlm' uses a hyphen (not underscore) — S3/MinIO bucket names prohibit underscores."
- `verify/checks.sh:145`: `for BUCKET in sources documents documents-vlm lance datasets; do` — correctly asserts the hyphen form.
- `claude-progress.txt` (implementer entry at 12:30): documents the deviation explicitly.

This deviation is **technically mandatory** — there is no other way to create the bucket. The rename is contained and documented in the two most critical files (the init script and the checker). The design doc's `s3://documents_vlm/` URI (§4.3) is aspirational notation, not an S3 validation target; the actual running bucket name must comply with S3 naming rules.

**Required action for the leader:** The closing `claude-progress.txt` entry for S003-F-003 and any sprint contract addendum must note: "Bucket `documents_vlm` renamed to `documents-vlm` (hyphen) — S3/MinIO bucket names prohibit underscores. All application code from F-011 onwards must use `documents-vlm` as the bucket name in storage URIs." The design doc `docs/data_platform_design.md` must NOT be edited (CLAUDE.md hard rule). The discrepancy between the spec (underscore) and the running bucket (hyphen) is a known tolerated delta, documented in the progress log.

**Does it affect V1 passing?** No — V1 correctly asserts `documents-vlm` (the hyphen form that actually exists). If V1 had been left as `documents_vlm`, it would correctly FAIL (the bucket does not exist under that name), making the discrepancy self-detecting.

---

## Deviation 2 — `fastapi` `depends_on: minio-init: condition: service_completed_successfully` + new env vars

**Assessment: APPROVED. Scope expansion is sane and leader-sanctioned.**

The agreed.md §2 scope table does not mention a `fastapi` `depends_on` change or new env vars. The implementer added:

```yaml
# docker/docker-compose.dev.yml:233-234
minio-init:
  condition: service_completed_successfully
```

And three new env vars on the `fastapi` service (lines 221–223):
- `MINIO_ENDPOINT: ${MINIO_ENDPOINT:-minio:9000}`
- `MINIO_ROOT_USER: ${MINIO_ROOT_USER:-minioadmin}`
- `MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:-devpassword}`

Evaluation:
- `service_completed_successfully` is the correct compose condition for a one-shot init container (not `service_healthy`, which would fail because `minio-init` has no healthcheck of its own). This prevents any FastAPI route from running before buckets exist — exactly the race condition the leader identified.
- `MINIO_ENDPOINT` defaults to `minio:9000` (container-internal, no scheme prefix). Note: F-011 code will need to prepend `http://` when constructing `endpoint_url` for boto3/aioboto3. This is a design choice, not a bug. A comment in F-011 will handle it.
- `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` match the values on the `minio` service (both default to `minioadmin` and `devpassword` respectively). Consistent. No credential mismatch.
- `MINIO_ENDPOINT` is not in `docker/.env.example`. It is used with a compose default (`:-minio:9000`), so the stack works without it. This is a low-severity nit — consistent with other host-port env vars that ARE in `.env.example`, adding `MINIO_ENDPOINT=minio:9000` there would be cleaner. Non-blocking.

---

## Deviation 3 — `docker/api/Dockerfile` modified

**Assessment: PASS. Same pattern as S002-F-002.**

`Dockerfile:23`: `"aioboto3==15.5.0"` added to the explicit `pip install` list. This is the same explicit-pin-in-Dockerfile pattern used for alembic and pydantic-settings in S002-F-002. The pin matches `pyproject.toml` exactly (`aioboto3==15.5.0`). No version drift. The Dockerfile's `pip install -e .` at the end still installs the package from source, so pyproject.toml remains the canonical dependency list. The Dockerfile explicit install is belt-and-suspenders for the built image. PASS.

---

## Deviation 4 — V1 uses `--entrypoint sh` override + cached `mc ls`

**Assessment: PASS. Correct and necessary workaround.**

The agreed.md §5 V1 template used `docker compose run --rm -T minio-init mc ls chk/` as the command. This assumes `minio-init`'s entrypoint is `mc` (or similar pass-through), so that `mc ls chk/` would work. In the actual implementation, `minio-init` has `entrypoint: ["/bin/sh", "/init-buckets.sh"]` — so running `docker compose run minio-init mc ls chk/` would re-execute the init script with `mc ls chk/` as an argument to the script (not as a separate mc invocation), which would re-create the buckets instead of listing them.

The implementer's fix (`--entrypoint sh` with `-c "mc ls chk/"`) correctly overrides the init script entrypoint and runs a plain mc list command. Tracing the command at `checks.sh:143–145`:

```bash
BUCKET_LIST=$(docker compose -f "$COMPOSE" run --rm -T \
  --entrypoint sh \
  -e MC_HOST_chk="http://${MINIO_USER}:${MINIO_PASS}@minio:9000" \
  minio-init \
  -c "mc ls chk/" 2>/dev/null | awk '{print $NF}')
```

Execution path: `docker compose run` overrides the entrypoint to `sh`; passes `-c "mc ls chk/"` as the command argument. `sh -c "mc ls chk/"` executes `mc ls chk/` inside the container. The `MC_HOST_chk` alias is configured via env var. `2>/dev/null` suppresses mc warnings from the host-level stderr. `awk '{print $NF}'` extracts bucket names (last field of each line). Result stored in `$BUCKET_LIST`. Then `grep -qxF "${BUCKET}/"` does exact whole-line match. CORRECT.

Caching into `$BUCKET_LIST` (single `docker compose run` per V1 pass instead of one per bucket) is more efficient than the agreed.md template's per-bucket approach and produces identical results. The correctness is the same; the performance is better. PASS.

---

## Additional findings

**Finding A (LOW) — `MINIO_ENDPOINT` not in `docker/.env.example`**

`docker/docker-compose.dev.yml:221` adds `MINIO_ENDPOINT: ${MINIO_ENDPOINT:-minio:9000}` to the fastapi service, but this variable is absent from `docker/.env.example`. All other host-port env vars are documented in `.env.example` (lines 32–40). Adding `MINIO_ENDPOINT=minio:9000` to `.env.example` would be consistent. This does not affect functionality (the default kicks in) but reduces developer discoverability. Non-blocking.

**Finding B (LOW) — `init-buckets.sh` has a double health gate (compose healthcheck + `mc ready`)**

`docker/minio/init-buckets.sh:20`: `mc ready "${ALIAS}"` runs after compose has already confirmed `minio` is `service_healthy` via the curl check. This redundant readiness probe is harmless (if anything, it provides additional insurance). It could cause a confusing hang if mc's readiness endpoint behaves differently from curl's, but in practice they both hit MinIO's readiness infrastructure. Non-blocking, noted for transparency.

**Finding C (INFO) — agreed.md V1 template still shows `documents_vlm` (underscore) in for-loop**

`agreed.md §5 line 167` shows `for BUCKET in sources documents documents_vlm lance datasets; do`. The actual `checks.sh` correctly uses `documents-vlm` (hyphen). The agreed.md is now permanently diverged from the implementation on this point, but that is expected and documented in `claude-progress.txt`. Anyone re-reading the contract should note the Deviation 1 explanation above. No action needed; the implementation is correct.

---

## Hard invariant alignment (per agreed.md §8)

| # | Invariant | Status |
|---|---|---|
| 1 | Lineage mandatory | NOT TRIGGERED — no Commit objects |
| 2 | Storage separation + CAS | SATISFIED — five buckets created in MinIO (object storage). Zero blob bytes in Postgres. No SQL changes at all. |
| 3 | Schema frozen post-publish | NOT TRIGGERED — no schema |
| 4 | LLM calls through gateway | NOT TRIGGERED — no LLM calls |
| 5 | Async SQLAlchemy | NOT TRIGGERED — no Python code in `apps/api/dataplat_api/` changed; only `pyproject.toml` and `uv.lock` |
| 6 | OpenAPI ↔ TS sync | NOT TRIGGERED — no API routes or Pydantic schemas changed |

---

## Approval rationale (per criterion)

- **Bucket inventory (5 buckets):** `init-buckets.sh` lines 23–30 create `sources`, `documents`, `documents-vlm` (see Deviation 1), `lance`, `datasets` via `mc mb --ignore-existing`. Idempotent. Correct count.
- **Healthcheck command:** `docker-compose.dev.yml:82` exactly matches agreed.md §3.2: `curl -fsS http://localhost:9000/minio/health/live || exit 1`. PASS.
- **mc image tag:** `docker-compose.dev.yml:95` — `minio/mc:RELEASE.2025-04-16T18-13-26Z`. Matches agreed.md OQ-2 confirmed tag with digest `sha256:aead63c7...`. Not `latest`. PASS.
- **`restart: "no"` quoted:** `docker-compose.dev.yml:110` — `restart: "no"` (double-quoted string, not YAML boolean false). Comment at line 91 documents why. PASS.
- **Idempotency:** `--ignore-existing` on all five `mc mb` calls. `restart: "no"` prevents compose from re-running `minio-init`. PASS.
- **V1 exact-match:** `checks.sh:147` — `grep -qxF "${BUCKET}/"`. PASS.
- **V2 round-trip:** `checks.sh:158–170` — `put_object` + `head_object` + `delete_object`. Full PUT+HEAD+DELETE cycle, not PUT-only. V2 cleans up `test.txt` via `delete_object` — idempotent. PASS.
- **Credentials not hardcoded:** All credential values come from `${MINIO_ROOT_USER:-minioadmin}` / `${MINIO_ROOT_PASSWORD:-devpassword}` env var references. `env_file: .env.example` on both `minio` and `minio-init` services. PASS.
- **aioboto3 dep pinned:** `pyproject.toml:22` — `"aioboto3==15.5.0"`. Dockerfile line 23 matches. `uv.lock` regenerated (664 lines added). PASS.
- **all) target updated:** `checks.sh:175` — `bash "$0" buckets` is the sixth entry in `all)`. PASS.
- **No scope creep into apps/api Python code:** Zero `.py` files in `apps/api/dataplat_api/` changed. PASS.
- **MVP scope discipline:** No bucket policies, versioning, Lance schema setup, public buckets, Celery, DinD, or granular ACL. PASS.

**The implementation satisfies the contract. Verifier may proceed.**
