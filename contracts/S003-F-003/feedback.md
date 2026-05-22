# S003-F-003 Mode A Review (Iteration 1)

**Reviewer:** Claude (Mode A, contract review)
**Date:** 2026-05-22
**Contract under review:** `contracts/S003-F-003/proposed.md`

---

## Verdict: CHANGES_REQUESTED

---

## Findings (numbered, ordered by severity)

### BLOCKER

**Finding 1 — [proposed.md §3.2, line 43–49] `mc ready local` healthcheck will fail: `mc` is not in the `minio/minio` server image.**

The contract states (line 50): _"`mc` is included in the `minio/minio` image, so no extra tooling is needed."_
This is factually incorrect. The `minio/minio` server image does NOT ship the `mc` client binary. `mc` is distributed separately via the `minio/mc` image. Running `mc ready local` inside the `minio` container will produce `exec: "mc": executable file not found in $PATH` and the healthcheck will immediately fail on every cycle. This is the same failure mode the F-001 comment at `docker-compose.dev.yml:66` was protecting against ("minio image does not ship wget or curl").

**Required fix:** Replace the `mc ready local` healthcheck on the `minio` service with the correct liveness probe that uses only tooling present in the server image:

```yaml
healthcheck:
  test: ["CMD-SHELL", "curl -fsS http://localhost:9000/minio/health/live || exit 1"]
  interval: 5s
  timeout: 5s
  retries: 12
  start_period: 10s
```

MinIO's `/minio/health/live` endpoint is the documented liveness probe; it returns 200 when the server is accepting requests. The MinIO server image (`RELEASE.2025-04-22T22-12-26Z`) does include `curl` — verify this before committing; if not present, substitute `wget -qO- http://localhost:9000/minio/health/live` since the image is known to include `wget` (the existing frontend service at `docker-compose.dev.yml:219` already relies on `wget` being present in the nginx image, as a reference point — but verify `minio` image specifically).

This is a blocker because the misconfigured healthcheck will cause the `minio` service to remain `unhealthy` indefinitely, which in turn prevents `minio-init` (which depends on `service_healthy`) from ever running, leaving all 5 buckets uncreated and causing V1 and V2 to fail.

The contract's OQ-3 (line 239–244) acknowledges this uncertainty but then resolves it incorrectly ("confirmed to include mc" without evidence). The actual fallback in OQ-3 is the right answer — use it as the primary.

---

**Finding 2 — [proposed.md §5 V1 shell snippet, lines 108–113] `grep -qF "${BUCKET}/"` can false-pass on prefix matches.**

The bucket listing output from `mc ls chk/` is in the format `[date] [time] [DIR] bucketname/`. The `grep -qF "${BUCKET}/"` check at line 110 will match any bucket whose name _contains_ the search string. For example, if a bucket named `sources_backup/` existed, the check for `sources` would match it. This is a false-positive risk.

The `checks.sh` block in §5 (line 178–183) repeats this pattern inside the implementation. The asserted bucket list is small and the dev environment is controlled, so the practical risk is low — but a false-pass in a verification layer is categorically unacceptable.

**Required fix:** Use an exact-match pattern. Replace `grep -qF "${BUCKET}/"` with `grep -qxF "${BUCKET}/"` (anchored) or, more robustly:

```bash
mc ls chk/ | awk '{print $NF}' | grep -qx "${BUCKET}/" \
  || { echo "FAIL: bucket '${BUCKET}' not found"; exit 1; }
```

`grep -qx` requires the entire line to match the pattern. Alternatively use `grep -qE "^${BUCKET}/$"`.

---

### HIGH

**Finding 3 — [proposed.md §5 V2, line 130] boto3 is asserted as a "transitive dependency" — it is not.**

Line 130–131 states: _"`boto3` is already available as a transitive dependency (via `sqlalchemy` pull-through or as a standalone dep — see §7 on the dep question). Not transitive."_

The contract immediately contradicts itself in the same sentence ("Not transitive"), then in §7 (line 155–161) correctly identifies that `boto3` must be added explicitly. The V2 check block at line 186–199 calls `boto3` inside the fastapi container — this will fail with `ModuleNotFoundError` until the dep is added and the image rebuilt.

The contract recommends adding `aioboto3` (§5, line 161, §7 line 159–161) but the V2 snippet on line 186 imports plain `boto3`, not `aioboto3`. If `aioboto3` is added, `boto3` is pulled in as a transitive dependency of `aioboto3`, so the `import boto3` in V2 will work. However, the contract is internally inconsistent on this point.

**Required fix:** The scope table at §2 (line 22) says "No Python dependency additions in this sprint (see §7 on boto3)" — but §7 recommends adding `aioboto3` and the Appendix (line 289–290) lists `pyproject.toml` and `uv.lock` as modified files. This is a direct contradiction.

The scope table (§2) must be corrected: remove the parenthetical "no Python dependency additions" and instead state: "add `aioboto3==<pin>` to `pyproject.toml`; regenerate `uv.lock`." The dep addition IS in scope as agreed in §7; the §2 table must reflect it.

Also: the `aioboto3` dep is reasonable and approved. The implementer must use a concrete pinned version (not `aioboto3==13.x` — specify the actual latest stable release as of implementation date, e.g. `aioboto3==13.4.0`). Check PyPI for the current latest stable before committing.

---

**Finding 4 — [proposed.md §5 V2, lines 136–138] Shell variable expansion inside a Python heredoc will break if credentials contain special characters.**

The V2 verification block uses `${MINIO_USER}` and `${MINIO_PASS}` shell variables interpolated directly into a Python string that is passed via `-c "..."`. If either credential contains a single quote, dollar sign, or backslash, the interpolation will produce a syntax error in the Python code or an incorrect credential string.

The default credentials (`minioadmin` / `devpassword`) happen to be safe, but the V2 code will be run from `checks.sh` and could be run against a non-default stack where these values differ.

**Required fix:** Pass credentials as environment variables to the Python one-liner, not via string interpolation:

```bash
docker compose -f "$COMPOSE" exec -T \
  -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
  fastapi python -c "
import boto3, os, sys
s3 = boto3.client('s3', endpoint_url='http://minio:9000',
    aws_access_key_id=os.environ['S3_USER'],
    aws_secret_access_key=os.environ['S3_PASS'])
s3.put_object(Bucket='sources', Key='test.txt', Body=b'hello-dataplat')
s3.head_object(Bucket='sources', Key='test.txt')
s3.delete_object(Bucket='sources', Key='test.txt')
print('OK')
sys.exit(0)
"
```

The same fix applies to the V1 `sh -c` block at line 178–180 where `${MINIO_USER}` and `${MINIO_PASS}` are interpolated into the mc alias command.

---

**Finding 5 — [proposed.md §3.2, line 50 + §7 OQ-3] Unresolved ambiguity about `mc` in minio/minio image must be resolved, not deferred to implementer.**

OQ-3 ends with "The implementer must verify which works before committing." This leaves an unresolved architectural decision in the contract. For an init container that depends on `mc` for bucket creation, the implementer must know at contract time whether the `minio-init` container's `mc` image is sufficient or whether the `minio` service's healthcheck must use an alternative.

Finding 1 above resolves the healthcheck question (use `curl`). For the `minio-init` service itself, `mc` comes from the `minio/mc` image (which DOES contain the mc binary) — this is correct. The confusion in the contract is only about the minio server image's healthcheck, which Finding 1 resolves.

**No separate action needed** — resolving Finding 1 resolves this. Marking for tracking only.

---

### MEDIUM

**Finding 6 — [proposed.md §7 OQ-2, line 232–235] `minio/mc:RELEASE.2025-04-22T22-12-26Z` tag existence not confirmed; "implementer must confirm" is insufficient for a contract.**

The contract recommends a specific mc image tag but states (line 234): _"The implementer must confirm this specific `minio/mc` tag exists on Docker Hub before committing."_ A contract should specify a pinned tag that is KNOWN to exist.

**Required fix:** Before the contract is agreed, verify that `minio/mc:RELEASE.2025-04-22T22-12-26Z` exists on Docker Hub using `docker pull minio/mc:RELEASE.2025-04-22T22-12-26Z --dry-run` or `docker manifest inspect minio/mc:RELEASE.2025-04-22T22-12-26Z`. If it does not exist, find the nearest matching release and pin that. Substitute the actual confirmed tag in the proposed.md before moving to agreed.md. The "if it does not exist" fallback is not appropriate in an agreed contract.

---

**Finding 7 — [proposed.md §5 V1, line 117–125] V1 primary vs. fallback approach inconsistency.**

The contract describes two different V1 approaches: (a) run `mc ls` via `docker compose run --rm minio-init` (lines 107–113), then (b) "the `checks.sh` implementation uses the `minio` exec variant" (line 126). The `checks.sh` block (line 167–183) implements approach (b). Approach (a) in lines 107–113 is described then abandoned without clearly marking it as discarded.

This is a documentation nit but creates confusion for the implementer. **Required fix:** Remove the `docker compose run --rm minio-init` snippet from §5 V1 (lines 107–113) since the agreed approach is `exec` on the running `minio` container. Keep only the approach that `checks.sh` will actually implement.

---

**Finding 8 — [proposed.md §5, checks.sh block, line 167–200] The `buckets)` case is missing from the `all)` target invocation.**

The `checks.sh` all) block at `verify/checks.sh:123–128` currently calls: `infra`, `backend`, `frontend`, `contract`, `migration`. The contract says (line 203): "`buckets` is appended to the `all)` composite target." This is stated as intent but is not shown explicitly in the proposed diff.

**Required fix:** The contract must explicitly show the full updated `all)` block in `checks.sh`, confirming that `bash "$0" buckets` is added. Without this, it is easy for the implementer to forget this step and a verifier running `checks.sh all` would silently skip the bucket checks.

---

**Finding 9 — [proposed.md §5 V2, line 143] Test object key `test.txt` is a flat path, not a CAS path.**

The contract uses `Key='test.txt'` (line 143) as the test key in the sources bucket. This is a verification test only (not production code), so it does not violate CAL-5 (CAS path discipline). However the contract should explicitly note that production code (F-011 and later) MUST use CAS paths (`blobs/{sha[:2]}/{sha[2:4]}/{sha}` as per CAL-5 / design doc §1.2 #5). The contract partially does this in §6 (out of scope) but does not call out the CAS invariant for production code specifically.

**Required fix:** Add one sentence to the V2 description: "This test key (`test.txt`) is for verification only; production code (F-011 onwards) MUST store blobs at CAS paths derived from `sha256(content)` per CLAUDE.md hard invariant #2 and CAL-5."

This is a documentation guard, not a code change. It prevents a future implementer from cargo-culting the `test.txt` flat path into production upload code.

---

### LOW / NON-BLOCKING

**Finding 10 — [proposed.md §5, checks.sh block, line 170] `MINIO_API_HOST_PORT` is read but not used.**

Line 170 reads `MINIO_API_HOST_PORT="${MINIO_API_HOST_PORT:-19000}"` but this variable is never used in the `buckets)` block — all MinIO calls go through `docker compose exec`, which uses the container-internal port 9000 directly. The variable is dead code.

**Fix:** Remove line 170 or comment it with a note explaining it is unused in this block. No functional impact.

---

## Calibration Cases Checked

| CAL-N | Status | Evidence |
|---|---|---|
| CAL-1 (async SQLAlchemy) | N/A | No `apps/api/` Python code in this sprint (only `pyproject.toml` dep addition) |
| CAL-2 (LLM gateway) | N/A | No LLM calls anywhere in proposed scope |
| CAL-3 (OpenAPI sync) | N/A | No API schema changes; contract correctly identifies `make codegen` not required (§6) |
| CAL-4 (lineage completeness) | N/A | No Commit objects created in this sprint; correctly marked IRRELEVANT in invariant table |
| CAL-5 (CAS path discipline) | CONCERN — see Finding 9 | V2 test key `test.txt` is not CAS but is a test; production CAS guard is not stated clearly enough |
| CAL-6 (schema freeze) | N/A | No schema changes |
| CAL-7 (Bronze faithfulness) | N/A | No plugin adapters in scope |
| CAL-8 (MVP scope) | PASS | §4 bucket policy work explicitly deferred; §6 out-of-scope list is correct; no Celery, no DinD, no ACL |
| CAL-9 (plugin isolation) | N/A | No plugin code in scope |
| CAL-10 (test coverage) | N/A for unit tests | This sprint is infra-only; verification is integration checks in `checks.sh`, which is the correct pattern for infra features |
| CAL-11 (bias check) | Applied | Five concrete blockers/highs identified; not approving on summary |

---

## Bucket Inventory Cross-check

Feature list F-003 specifies: `sources, documents, documents_vlm, lance, datasets` — 5 buckets.

Design doc §4.3 specifies exactly the same 5 top-level buckets: `s3://sources/`, `s3://documents/`, `s3://documents_vlm/`, `s3://lance/`, `s3://datasets/`. No drift between feature_list and design doc.

The contract's §4 table (lines 88–93) matches both sources exactly. **No discrepancy.**

---

## Hard Invariant Alignment Review

The contract's own invariant table (§8) is correct. Specifically:
- Invariant 2 (Storage separation + CAS): SATISFIED at the infra level — this sprint creates the buckets that downstream features will use for CAS storage.
- Invariant 5 (Async SQLAlchemy): Only `pyproject.toml` + `uv.lock` are changed in `apps/api/`, not any session code. Adding `aioboto3` as a library dep does not trigger this invariant.
- The `aioboto3` dep addition was correctly identified as not triggering Invariant 6 (OpenAPI sync) since there are no route changes.

---

## Next Action

**The proposed contract is CHANGES_REQUESTED.** Findings 1–4 are blockers or high-severity issues that MUST be resolved before implementation begins:

1. **Finding 1** (BLOCKER): Replace `mc ready local` healthcheck on `minio` service with `curl -fsS http://localhost:9000/minio/health/live || exit 1`. Verify `curl` is present in the `minio/minio:RELEASE.2025-04-22T22-12-26Z` image; if not, use the MinIO `/minio/health/live` endpoint via `wget`.

2. **Finding 2** (BLOCKER): Change `grep -qF "${BUCKET}/"` to `grep -qxF "${BUCKET}/"` (or equivalent exact-match) in both the §5 V1 snippet and the `checks.sh` block.

3. **Finding 3** (HIGH): Remove the contradictory "No Python dependency additions" clause from §2; confirm that `aioboto3==<exact-pinned-version>` is in scope, with `uv.lock` regeneration required.

4. **Finding 4** (HIGH): Rewrite the V1 and V2 credential injection to use environment variables rather than shell string interpolation into the command string.

5. **Finding 6** (MEDIUM): Confirm the `minio/mc:RELEASE.2025-04-22T22-12-26Z` tag exists before agreeing the contract; substitute a confirmed-existing tag.

6. **Findings 7 and 8** (MEDIUM): Clean up the V1 approach description (remove the abandoned `run --rm` snippet); add the explicit `all)` block diff showing `bash "$0" buckets` added.

7. **Finding 9** (LOW): Add the CAS invariant note to the V2 description.

8. **Finding 10** (LOW): Remove the unused `MINIO_API_HOST_PORT` variable from the `buckets)` block.

After resolving Findings 1–6 (blockers, highs, mediums), re-submit as iteration 2 for a final Mode A check before handing to the implementer.

---

## Iteration 2

**Reviewer:** Claude (Mode A, contract review)
**Date:** 2026-05-22
**Based on:** `contracts/S003-F-003/proposed.md` (iteration 2 — "addressing Mode A findings 1–10")

---

## Verdict: APPROVED

---

## Per-finding resolution status

**F-1 (BLOCKER — healthcheck):** RESOLVED.
`proposed.md §3.2 line 52`: `test: ["CMD-SHELL", "curl -fsS http://localhost:9000/minio/health/live || exit 1"]`. The `minio/minio` server image's lack of `mc` is now explicitly documented (line 48: "`mc` is NOT present in the `minio/minio` server image"). OQ-3 (line 249–257) confirms `curl` is at `/bin/curl` with exit-0 evidence. Healthcheck parameters (`interval: 5s`, `retries: 12`, `start_period: 10s`) are correct. PASS.

**F-2 (BLOCKER — grep exact match):** RESOLVED.
`proposed.md §5 V1 prose line 116`: "Each bucket is asserted individually using `grep -qxF` (exact whole-line match)". The `checks.sh` block at line 171: `grep -qxF "${BUCKET}/"`. No `grep -qF` without `-x` remains anywhere in the document. PASS.

**F-3 (HIGH — scope contradiction):** RESOLVED.
`proposed.md §2 lines 23–24`: scope table now explicitly lists `apps/api/pyproject.toml` (add `aioboto3==15.5.0`) and `apps/api/uv.lock` (regenerate). The old "No Python dependency additions" clause is absent. OQ-5 (line 266–277) specifies the exact version, confirms it was verified on PyPI, and documents the required image rebuild step. PASS.

**F-4 (HIGH — credential interpolation):** RESOLVED for V2. V2 at lines 135–147 uses `-e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}"` with Python reading `os.environ['S3_USER']` and `os.environ['S3_PASS']`. Credentials are never interpolated into the Python code string. PASS for V2.

V1 at lines 168–169 uses `-e MC_HOST_chk="http://${MINIO_USER}:${MINIO_PASS}@minio:9000"`. The shell correctly passes this as an env var to the container (no shell-quoting injection). This approach does carry a residual risk for URL-special characters in credentials (see new Finding N-1 below) but the contract's stated rationale — avoiding argv injection — is satisfied, and the dev stack defaults are safe. Noted as LOW; not a blocker.

**F-6 (MEDIUM — mc image tag):** RESOLVED.
`proposed.md §7 OQ-2 lines 236–247`: `minio/mc:RELEASE.2025-04-16T18-13-26Z` is the pinned tag. Verified by successful pull with explicit digest `sha256:aead63c77f9db9107f1696fb08ecb0faeda23729cde94b0f663edf4fe09728e3`. The original unverified tag and its "implementer must confirm" caveat are gone. PASS.

**F-7 (MEDIUM — two-alternative V1):** RESOLVED.
`proposed.md §5 V1 lines 114–126` describe a single approach (docker compose run --rm against `minio-init`). The old "exec on minio container" snippet with inline `mc alias set chk` is gone (confirmed by `grep` finding no `mc alias set chk` in the document). PASS.

**F-8 (MEDIUM — all) target):** RESOLVED.
`proposed.md §5 lines 193–206` show the explicit updated `all)` block with `bash "$0" buckets` as the sixth line. PASS.

**F-9 (LOW — CAS guard note):** RESOLVED.
`proposed.md §5 V2 line 150`: "The key `test.txt` is a flat path for verification purposes only. Production code (F-011 onwards) MUST store blobs at CAS-derived paths (`sha256(content)` layout) per CLAUDE.md hard invariant #2 and CAL-5." PASS.

**F-10 (LOW — dead `MINIO_API_HOST_PORT`):** RESOLVED.
The `buckets)` block in §5 (lines 158–191) no longer contains any `MINIO_API_HOST_PORT` reference. Confirmed absent. PASS.

---

## New finding from walk-through

**N-1 (LOW) — [proposed.md §3.3 line 73 vs §7 OQ-6 line 281] Minor alias name inconsistency between init-buckets.sh description and OQ-6.**

`§3.3 line 73` shows the example alias as `MC_HOST_local` (alias name: `local`). `§7 OQ-6 line 281` states "The alias name `chk` is used consistently between V1 and `init-buckets.sh`." The alias name in `init-buckets.sh` is independent of the alias name in V1 — they run in separate container invocations and never share state — so the mismatch has no functional consequence. But OQ-6's claim of consistency between the two is factually inaccurate as written. The implementer should pick one alias name for `init-buckets.sh` (either `local` or `chk`) and document it without asserting it "matches" V1.

This is documentation only. It does NOT block approval.

**N-2 (LOW) — [proposed.md §3.3 line 73] MC_HOST URL-encoding caveat.**

The contract describes the `MC_HOST_<alias>` pattern as "safe for shell special characters" (line 73). This is true for shell quoting (the value is passed as an env var, not in argv). However, it is NOT safe for URL-special characters in credentials: a password containing `@`, `:`, or other RFC 3986 reserved chars would cause mc to misparse the authority component. The dev-stack defaults (`minioadmin` / `devpassword`) contain no such characters, so there is no practical risk. The contract should not claim general safety; it should say "safe for shell special characters; URL-reserved characters in credentials would require %-encoding." This is documentation hygiene only and does not block approval.

---

## V1 command walk-through (hand trace)

Taking the `checks.sh` block at lines 158–191 and tracing BUCKET=`sources`:

```
MINIO_USER="minioadmin"       # from MINIO_ROOT_USER env or default
MINIO_PASS="devpassword"      # from MINIO_ROOT_PASSWORD env or default

docker compose -f "docker/docker-compose.dev.yml" run --rm -T \
  -e MC_HOST_chk="http://minioadmin:devpassword@minio:9000" \
  minio-init \
  mc ls chk/ | awk '{print $NF}' | grep -qxF "sources/"
```

Execution path:
1. `docker compose run --rm -T minio-init mc ls chk/` — spawns a new one-off container from the `minio-init` service definition (image: `minio/mc:RELEASE.2025-04-16T18-13-26Z`). The `MC_HOST_chk` env var is injected, so mc recognises the alias `chk` without needing `mc alias set`. The command `mc ls chk/` lists all buckets at the MinIO endpoint.
2. The container's stdout is piped (shell-level) to `awk '{print $NF}'`, which extracts the last field of each line — the bucket name including trailing slash (e.g. `sources/`).
3. Piped to `grep -qxF "sources/"` — exact whole-line fixed-string match. Exits 0 if found, 1 if not.
4. `|| { echo "FAIL: ..."; exit 1; }` fires only if grep returns non-zero.

Shell operator precedence: the backslash continuation at the end of each `docker compose` option line means lines 168–172 form one logical shell command. The `|` on line 171 is a shell pipe applied to the output of the complete `docker compose run` command, not passed as an argument to it. This is syntactically correct.

Network reachability: `docker compose run` uses the same compose network as the other services, so `minio:9000` is resolvable from the one-off container. Correct.

The one-off container created by `docker compose run --rm` is independent of the already-exited `minio-init` persistent container. `restart: "no"` on the persistent container does not affect one-off runs. Correct.

Result: V1 is functionally correct as written, subject to N-1/N-2 documentation nits.

---

## V2 command walk-through (hand trace)

```
docker compose -f "docker/docker-compose.dev.yml" exec -T \
  -e S3_USER="minioadmin" -e S3_PASS="devpassword" \
  fastapi python -c "
import boto3, os, sys
s3 = boto3.client('s3', endpoint_url='http://minio:9000',
    aws_access_key_id=os.environ['S3_USER'],
    aws_secret_access_key=os.environ['S3_PASS'])
s3.put_object(Bucket='sources', Key='test.txt', Body=b'hello-dataplat')
s3.head_object(Bucket='sources', Key='test.txt')
s3.delete_object(Bucket='sources', Key='test.txt')
print('OK')
sys.exit(0)
"
```

Execution path:
1. `docker compose exec -T -e S3_USER=... -e S3_PASS=... fastapi python -c "..."` — `-e` flags are correctly placed before the service name `fastapi`, passing `S3_USER` and `S3_PASS` as env vars into the running container. `-T` suppresses TTY allocation (required for non-interactive use in `checks.sh`).
2. Python reads credentials from `os.environ['S3_USER']` and `os.environ['S3_PASS']` — never from the command string. Correct.
3. `boto3.client` is available because `aioboto3==15.5.0` (once added) pulls in `boto3` as a transitive dep.
4. `put_object` → `head_object` → `delete_object` performs a full write-read-delete round-trip. This satisfies the F-003 verification criterion "Uploading a test file to `s3://sources/test.txt` via the MinIO SDK succeeds."
5. The `|| { echo "FAIL: ..."; exit 1; }` error handler at line 189 catches any non-zero exit from the Python subprocess.

The Python `-c "..."` string contains no double-quotes (it uses single-quoted Python string literals), so the outer double-quoting is safe. The string spans multiple lines — this is valid in bash inside a double-quoted string. Correct.

Result: V2 is functionally correct as written.

---

## Calibration cases (Iteration 2)

| CAL-N | Status | Evidence |
|---|---|---|
| CAL-1 (async SQLAlchemy) | N/A | No session code changes. Only `pyproject.toml` dep addition and compose/shell changes. |
| CAL-2 (LLM gateway) | N/A | No LLM calls in scope. |
| CAL-3 (OpenAPI sync) | N/A | No API schema changes. `make codegen` correctly excluded at §2 line 26. |
| CAL-4 (lineage completeness) | N/A | No Commit objects. Invariant table §8 correctly marks IRRELEVANT. |
| CAL-5 (CAS path discipline) | PASS | V2 test key `test.txt` is verification-only. Production CAS guard note added at line 150. Resolved from iter-1 concern. |
| CAL-6 (schema freeze) | N/A | No schema modifications. |
| CAL-7 (Bronze faithfulness) | N/A | No plugin adapters in scope. |
| CAL-8 (MVP scope) | PASS | Bucket policies, versioning, ACL all deferred in §6. No Celery, DinD, granular ACL, or any other CLAUDE.md-banned item present. |
| CAL-9 (plugin isolation) | N/A | No plugin code in scope. |
| CAL-10 (test coverage) | N/A | Infra-only sprint; integration verification in `checks.sh` is the appropriate pattern for an infra feature. |
| CAL-11 (bias check) | Applied | Two new LOW findings identified (N-1, N-2). Approval is based on concrete per-finding evidence above, not on summary. |

---

## Approval rationale (one line per criterion)

- **Bucket inventory fidelity:** §4 table names exactly `sources`, `documents`, `documents_vlm`, `lance`, `datasets` — matches feature_list F-003 and design doc §4.3. No drift.
- **Healthcheck correctness:** `curl -fsS http://localhost:9000/minio/health/live` uses a tool confirmed present in the server image; MinIO's documented liveness endpoint. Correct.
- **Init service one-shot:** `restart: "no"` documented in §3.4; `exit 0` explicit in appendix script description. Correct.
- **Idempotency:** `mc mb --ignore-existing` documented in §3.1. Correct.
- **Credentials never hardcoded:** V1 via `MC_HOST_chk` env var; V2 via `-e S3_USER`/`-e S3_PASS`; init script via `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` from `env_file`. Correct.
- **mc image pinned:** `minio/mc:RELEASE.2025-04-16T18-13-26Z` with verified digest. Not `latest`. Correct.
- **V1 exact-match:** `grep -qxF "${BUCKET}/"` at lines 116 and 171. Correct.
- **V2 round-trip:** PUT + HEAD + DELETE at lines 184–186. Not PUT-only. Correct.
- **aioboto3 dep:** explicitly in scope (§2 lines 23–24), exact pin `aioboto3==15.5.0`, uv.lock regen required, image rebuild documented. Correct.
- **all) target updated:** `bash "$0" buckets` appended in §5 lines 198–205. Correct.
- **CAS guard note:** present at line 150. Correct.
- **MVP scope:** no bucket policies, no Lance schema setup, no public buckets, no banned items. Correct.
- **Hard invariants:** all six reviewed and correctly classified in §8 (invariants 1, 3, 4 irrelevant; 2 satisfied; 5 and 6 not triggered). Correct.

New findings N-1 and N-2 are LOW documentation nits that do not affect correctness. They should be fixed in the actual implementation commit (update OQ-6 alias name and soften the "safe" claim), but they do not require another contract iteration.

**This contract is ready to be copied to `agreed.md` and dispatched to the implementer.**
