# Sprint Contract S003-F-003 — MinIO Bucket Initialisation

**Status:** PROPOSED (iteration 2 — addressing Mode A findings 1–10)  
**Date drafted:** 2026-05-22  
**Last updated:** 2026-05-22  
**Author:** Implementer (Claude)

---

## 1. Goal

This sprint ensures that all five MinIO buckets required by the data platform (`sources`, `documents`, `documents_vlm`, `lance`, `datasets`) are created automatically when the dev stack first starts. A one-shot `minio-init` service is added to `docker-compose.dev.yml`; it runs `mc mb --ignore-existing` for each bucket and exits 0. The `minio` service gains a `curl`-based healthcheck so that `minio-init` (which depends on `service_healthy`) never races MinIO startup. This sprint also adds a `buckets` layer to `verify/checks.sh` that covers both verification criteria from `feature_list.json`. It unblocks F-011 (PDF upload route), F-013 (document extractor), and any other sprint that writes to object storage — those sprints can assume all buckets exist and will never need to handle a missing-bucket error.

---

## 2. Scope — Files to Change

| File | Action | Purpose |
|---|---|---|
| `docker/docker-compose.dev.yml` | modify | Add `curl`-based healthcheck to `minio` service; add `minio-init` one-shot service |
| `docker/minio/init-buckets.sh` | create | Shell script run by `minio-init`: sets mc alias from env vars, creates 5 buckets idempotently, exits 0 |
| `verify/checks.sh` | modify | Add `buckets)` layer (V1 exact-match bucket list, V2 SDK upload/head/delete); add `bash "$0" buckets` to `all)` target |
| `apps/api/pyproject.toml` | modify | Add `aioboto3==15.5.0` to `[project.dependencies]` |
| `apps/api/uv.lock` | modify | Regenerate after dep addition (`cd apps/api && uv lock`) |

No new routes. No API schema changes. `make codegen` not required.

**Image rebuild required:** After adding `aioboto3` to `pyproject.toml`, the implementer MUST rebuild the `fastapi` image:
```bash
docker compose -f docker/docker-compose.dev.yml build fastapi
docker compose -f docker/docker-compose.dev.yml up -d fastapi
```

---

## 3. Approach Decision

**Chosen approach: Option 1 — dedicated `minio-init` one-shot service using `minio/mc`.**

### 3.1 Idempotency

`mc mb --ignore-existing` is the canonical idempotent bucket-creation command in the MinIO Client. It exits 0 whether the bucket already exists or is being created for the first time. The `minio-init` service uses `restart: "no"` so compose never relaunches it after a clean exit. On a second `docker compose up` (stack already running), compose reports it as `exited (0)` and skips it. After a full `docker compose down && up`, the `minio_data` volume persists so `--ignore-existing` prevents errors on re-creation.

### 3.2 Wait-for-MinIO ordering

The `minio` service in the current compose file has no healthcheck — the F-001 comment at line 66 explicitly deferred this to F-003: _"A proper healthcheck using `mc ready` is added in F-003."_

This sprint delivers that healthcheck using `curl`, which is **confirmed present** at `/bin/curl` in `minio/minio:RELEASE.2025-04-22T22-12-26Z` (verified by running `ls /bin/curl` inside the image — exit 0). Note: **`mc` is NOT present in the `minio/minio` server image** — it is distributed separately via the `minio/mc` image. The healthcheck therefore uses MinIO's documented liveness endpoint:

```yaml
healthcheck:
  test: ["CMD-SHELL", "curl -fsS http://localhost:9000/minio/health/live || exit 1"]
  interval: 5s
  timeout: 5s
  retries: 12
  start_period: 10s
```

`/minio/health/live` returns HTTP 200 when the server is accepting S3 API requests. The `minio-init` service then declares:

```yaml
depends_on:
  minio:
    condition: service_healthy
```

This guarantees `mc mb` only runs once MinIO's S3 API is accepting connections.

### 3.3 Credentials sourcing

The `minio-init` service receives credentials exclusively via environment variables sourced from the same `env_file: .env.example` that the `minio` service uses. The init script reads `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` — confirmed from `.env.example` lines 21–22 as `minioadmin` and `devpassword` respectively. No credentials are hardcoded in the script or in `docker-compose.dev.yml`.

The `init-buckets.sh` script uses the `MC_HOST_<alias>` environment variable convention (e.g. `MC_HOST_local="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@minio:9000"`) to configure the mc alias without putting credentials in command-line arguments — safe for shell special characters.

### 3.4 Exit behaviour

The script ends with `exit 0` explicitly. The compose service is declared with `restart: "no"` so it shows as `exited (0)` after bucket creation and is never restarted. This is the correct pattern for init containers in compose — the default `restart: unless-stopped` would cause compose to relaunch it in a loop.

### 3.5 Why not Option 2 (FastAPI lifespan)?

Coupling bucket creation to the FastAPI lifespan event would:
- Widen the API container's boot-time surface (MinIO dep where none exists today).
- Require retry logic inside the lifespan event to handle MinIO not yet ready.
- Mix object-storage concerns into the API lifecycle, making the API harder to reason about in isolation.

The design doc reference compose snippet (§1086) shows `minio-init` as a separate service, matching Option 1.

### 3.6 Why not Option 3 (Make/entrypoint script)?

A standalone Make target requires the developer to remember to run it — it is not automatic. An entrypoint override on the `minio` service would mix server startup with init logic.

---

## 4. Bucket Inventory

All five buckets are sourced from `docs/data_platform_design.md §4.3`:

| Bucket | Purpose |
|---|---|
| `sources` | Original uploaded source files — `s3://sources/{source_id}/original.{ext}` plus `metadata.json` |
| `documents` | Extractor output artefacts — `s3://documents/{source_id}/{extractor}/doc.docling.json`, images, `manifest.json` |
| `documents_vlm` | VLM-enriched documents (Phase 2 / F-102) — `s3://documents_vlm/{source_id}/doc.docling.json`; bucket created now so no downstream sprint ever handles a missing-bucket error |
| `lance` | Lance chunk table — `s3://lance/chunks/`; Lance manages its own internal directory structure (`_versions/`, `data/`) under this bucket |
| `datasets` | Materialised HF-style dataset exports — `s3://datasets/{dataset_id}_v{version}/` with `README.md`, `dataset_infos.json`, `recipe.json`, and `data/*.parquet` |

All buckets are created with default-private access policy (MinIO's default). Bucket-level policy changes are explicitly out of scope (§6).

---

## 5. Verification Plan

### V1 — `mc ls` lists all 5 expected buckets

Since `mc` is not present in the `minio/minio` server image, V1 uses `docker compose run --rm` to spawn a short-lived `minio/mc` container (the same image as `minio-init`) with the alias configured via the `MC_HOST_chk` environment variable. This avoids any dependency on the already-exited `minio-init` container state, and avoids putting credentials in command-line arguments.

Each bucket is asserted individually using `grep -qxF` (exact whole-line match) so that a bucket named e.g. `sources_backup` does not false-pass the `sources` check.

The full `checks.sh` block (see below) is the authoritative implementation; the pattern for each bucket is:

```bash
docker compose -f "$COMPOSE" run --rm -T \
  -e MC_HOST_chk="http://${MINIO_USER}:${MINIO_PASS}@minio:9000" \
  minio-init \
  mc ls chk/ | awk '{print $NF}' | grep -qxF "${BUCKET}/" \
  || { echo "FAIL: bucket '${BUCKET}' not found"; exit 1; }
```

### V2 — Upload test file via SDK

`aioboto3==15.5.0` pulls in `boto3` as a dependency, so `import boto3` works after the dep addition and image rebuild. The check runs a synchronous boto3 one-liner inside the `fastapi` container using the container-internal `minio:9000` endpoint.

Credentials are passed as environment variables to the `docker compose exec` call — not interpolated into the Python command string — to avoid breakage if credentials contain shell-special characters (`'`, `$`, `\`, etc.):

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

**CAS invariant note:** The key `test.txt` is a flat path for verification purposes only. Production code (F-011 onwards) MUST store blobs at CAS-derived paths (`sha256(content)` layout) per CLAUDE.md hard invariant #2 and CAL-5. Do not cargo-cult this key pattern into production upload code.

This test uses sync boto3 intentionally — it is a one-shot verification command, not production code. Hard invariant 5 (async SQLAlchemy) applies only to session code in `apps/api/dataplat_api/`; a verification subprocess has no such requirement.

### Full `buckets)` block for `checks.sh`

This block is added to the `case "$LAYER" in` statement:

```bash
  buckets)
    COMPOSE="docker/docker-compose.dev.yml"
    [[ -f "$COMPOSE" ]] || { echo "no $COMPOSE yet"; exit 0; }

    MINIO_USER="${MINIO_ROOT_USER:-minioadmin}"
    MINIO_PASS="${MINIO_ROOT_PASSWORD:-devpassword}"

    echo "--- buckets V1: all 5 buckets present ---"
    for BUCKET in sources documents documents_vlm lance datasets; do
      docker compose -f "$COMPOSE" run --rm -T \
        -e MC_HOST_chk="http://${MINIO_USER}:${MINIO_PASS}@minio:9000" \
        minio-init \
        mc ls chk/ | awk '{print $NF}' | grep -qxF "${BUCKET}/" \
        || { echo "FAIL: bucket '${BUCKET}' not found"; exit 1; }
      echo "  bucket ${BUCKET}: OK"
    done

    echo "--- buckets V2: upload/head/delete test object to sources bucket ---"
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
" || { echo "FAIL: SDK upload/head/delete cycle failed"; exit 1; }
    ;;
```

### Updated `all)` block for `checks.sh`

`bash "$0" buckets` is appended to the `all)` target. The full updated block:

```bash
  all)
    bash "$0" infra
    bash "$0" backend
    bash "$0" frontend
    bash "$0" contract
    bash "$0" migration
    bash "$0" buckets
    ;;
```

---

## 6. Out of Scope

The following are explicitly NOT delivered in this sprint:

- **Bucket policies / ACL** — all buckets are default-private (MinIO's default). Public-read or fine-grained IAM policy work is deferred. MVP uses `visibility = private|internal` per CLAUDE.md scope discipline.
- **Bucket versioning and lifecycle rules** — no `mc version enable` or expiry rules.
- **Lance table initialisation** — only the `lance` bucket is created; the actual `chunks` table inside the bucket (`s3://lance/chunks/`) is initialised by the first extractor run (separate feature).
- **Production-grade credential rotation** — credentials remain static `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` for dev. Secret management is deferred.
- **F-011 PDF upload route** — the bucket existence makes F-011 possible, but the route itself is a separate sprint.
- **`aioboto3` async session usage in routes** — adding the dep is in scope; using it in a route is F-011 scope.
- **`make codegen`** — no API schema changes; not required.

---

## 7. Risks and Open Questions

### OQ-1 — MinIO credential env var names (RESOLVED)

Confirmed from `docker/.env.example` lines 21–22 and `docker-compose.dev.yml` lines 72–73:
- `MINIO_ROOT_USER=minioadmin` (default)
- `MINIO_ROOT_PASSWORD=devpassword` (default)

The init script and `checks.sh` read `${MINIO_ROOT_USER:-minioadmin}` and `${MINIO_ROOT_PASSWORD:-devpassword}`. No hardcoding.

### OQ-2 — mc image tag (RESOLVED)

`minio/mc:RELEASE.2025-04-22T22-12-26Z` does NOT exist on Docker Hub (verified: `docker pull` returns `manifest unknown`). The nearest available tag with a release date prior to the server release date (`RELEASE.2025-04-22T22-12-26Z`) is:

**`minio/mc:RELEASE.2025-04-16T18-13-26Z`**

Confirmed by successful pull:
```
docker pull minio/mc:RELEASE.2025-04-16T18-13-26Z
# Digest: sha256:aead63c77f9db9107f1696fb08ecb0faeda23729cde94b0f663edf4fe09728e3
# Status: Downloaded newer image for minio/mc:RELEASE.2025-04-16T18-13-26Z
```

This tag is used as the `minio-init` service image. The 6-day gap between the mc release (`2025-04-16`) and the server release (`2025-04-22`) is within the supported compatibility window — mc is protocol-stable across minor releases.

### OQ-3 — `curl` in `minio/minio` image (RESOLVED)

`curl` is confirmed present at `/bin/curl` in `minio/minio:RELEASE.2025-04-22T22-12-26Z` (verified by running the image and checking `ls /bin/curl` — exit 0). `wget` and Python are NOT present in this image. The healthcheck uses:

```yaml
test: ["CMD-SHELL", "curl -fsS http://localhost:9000/minio/health/live || exit 1"]
```

`mc` is NOT present in the `minio/minio` server image — it is only available in the `minio/mc` image.

### OQ-4 — `minio-init` restart policy and compose up idempotency

With `restart: "no"`, compose re-runs `minio-init` only if the container is explicitly removed (e.g. `docker compose down` followed by `up`). The implementer must verify:
1. On first `docker compose up`, `minio-init` starts, waits for `minio` healthy, creates buckets, exits 0.
2. On second `docker compose up` (stack already running), `minio-init` is reported as `exited (0)` and is not restarted.
3. After `docker compose down && docker compose up`, `minio-init` runs again and `--ignore-existing` prevents errors.

### OQ-5 — `aioboto3` dep addition and image rebuild (RESOLVED)

`aioboto3==15.5.0` is the latest stable as of 2026-05-22 (verified on PyPI: `pip index versions aioboto3`). It pulls in `boto3` and `aiobotocore` as transitive dependencies, so `import boto3` works in V2.

After adding to `pyproject.toml`:
```bash
cd apps/api && uv lock
docker compose -f docker/docker-compose.dev.yml build fastapi
docker compose -f docker/docker-compose.dev.yml up -d fastapi
```

Without the rebuild, `import boto3` inside the container fails with `ModuleNotFoundError`.

### OQ-6 — `MC_HOST_<alias>` env var alias syntax for mc

The `MC_HOST_<alias>` environment variable is the mc convention for declaring an alias without running `mc alias set`, keeping credentials out of command-line arguments. The format is `http://<user>:<password>@<host>:<port>`. The alias name `chk` is used consistently between V1 and `init-buckets.sh`.

---

## Hard Invariant Alignment

| # | Invariant | Status in this sprint |
|---|---|---|
| 1 | Lineage is mandatory | IRRELEVANT — no Commit objects |
| 2 | Storage separation + CAS | SATISFIED — this sprint creates the object storage buckets that enforce CAS; no bytes stored in Postgres |
| 3 | Schema frozen post-publish | IRRELEVANT — no schema |
| 4 | LLM calls through gateway | IRRELEVANT — no LLM calls |
| 5 | Async SQLAlchemy from day one | NOT TRIGGERED — no session code changes; only `pyproject.toml` dep addition |
| 6 | OpenAPI ↔ TS type sync | NOT TRIGGERED — no API schema changes; `make codegen` not required |

---

## Appendix: Files-to-Create/Modify Summary

```
docker/
  docker-compose.dev.yml          (modify — add curl healthcheck to minio; add minio-init service)
  minio/
    init-buckets.sh               (create — MC_HOST env var alias + mc mb x5 + exit 0)
verify/
  checks.sh                       (modify — add buckets) layer; add bash "$0" buckets to all) target)
apps/api/
  pyproject.toml                  (modify — add aioboto3==15.5.0)
  uv.lock                         (modify — regenerate after dep add)
```

Total: **5 files** modified or created. No new routes. No API schema changes. No `make codegen` required.
