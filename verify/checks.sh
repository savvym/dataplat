#!/usr/bin/env bash
# Layered verification script. Exit code is ground truth.
#
# Usage:
#   bash verify/checks.sh smoke         # fast baseline (~30s)
#   bash verify/checks.sh backend       # apps/api lint + type + unit
#   bash verify/checks.sh frontend      # apps/web lint + type + unit
#   bash verify/checks.sh contract      # OpenAPI ↔ TS sync
#   bash verify/checks.sh migration     # alembic up/down round-trip
#   bash verify/checks.sh plugin <name> # one plugin's tests
#   bash verify/checks.sh all           # everything except per-plugin
#
# Layers gracefully skip if their target directory doesn't exist yet
# (useful in the early phase before apps/api or apps/web are built).

set -euo pipefail
LAYER="${1:-all}"
shift || true

run() { echo "▶ $*"; ( eval "$*" ); }
exists() { [[ -d "$1" ]]; }

case "$LAYER" in
  infra)
    # F-001: docker-compose dev stack syntax + service health checks
    COMPOSE_FILE="docker/docker-compose.dev.yml"
    [[ -f "$COMPOSE_FILE" ]] || { echo "no $COMPOSE_FILE yet"; exit 0; }

    # Host ports (overridable via env; defaults match docker/.env.example).
    # Container-internal ports unchanged from agreed.md §5.
    FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"
    DAGSTER_HOST_PORT="${DAGSTER_HOST_PORT:-13000}"
    MINIO_CONSOLE_HOST_PORT="${MINIO_CONSOLE_HOST_PORT:-19001}"
    POSTGRES_HOST_PORT="${POSTGRES_HOST_PORT:-15432}"

    echo "--- infra: validate compose syntax ---"
    run "docker compose -f $COMPOSE_FILE config -q"

    echo "--- infra: check running services (stack must already be up) ---"
    # V2: FastAPI /healthz
    run "curl -fsS http://localhost:${FASTAPI_HOST_PORT}/healthz | grep -q '\"ok\"'"
    # V3: Dagster /dagster_version returns 200 (spec literal).
    # In Dagster 1.11+, /dagster_version returns the SPA shell with HTTP 200;
    # the authoritative JSON version lives at /server_info. We assert 200 on
    # /dagster_version (matches feature_list.json) AND the JSON shape on /server_info.
    DAGV_STATUS=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:${DAGSTER_HOST_PORT}/dagster_version")
    [[ "$DAGV_STATUS" == "200" ]] || { echo "FAIL: /dagster_version returned $DAGV_STATUS"; exit 1; }
    run "curl -fsS http://localhost:${DAGSTER_HOST_PORT}/server_info | grep -q '\"dagster_version\":\"1\\.'"
    # V4: MinIO console reachable
    STATUS=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:${MINIO_CONSOLE_HOST_PORT})
    [[ "$STATUS" == "200" || "$STATUS" == "302" || "$STATUS" == "307" ]] || { echo "FAIL: MinIO console returned $STATUS"; exit 1; }
    echo "MinIO console: $STATUS — OK"
    # V5: psql against both 'platform' and 'platform_dagster'.
    # Uses docker exec because the host may not have a psql client installed
    # (the postgres container ships one). Container-internal connection skips
    # the host port and uses the in-network port 5432 directly.
    POSTGRES_USER_DEFAULT="${POSTGRES_USER:-app}"
    run "docker compose -f $COMPOSE_FILE exec -T postgres psql -U $POSTGRES_USER_DEFAULT -d ${POSTGRES_DB:-platform} -c 'SELECT 1' -t | grep -q 1"
    run "docker compose -f $COMPOSE_FILE exec -T postgres psql -U $POSTGRES_USER_DEFAULT -d ${POSTGRES_DB_DAGSTER:-platform_dagster} -c 'SELECT 1' -t | grep -q 1"
    ;;
  smoke)
    FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"
    DAGSTER_HOST_PORT="${DAGSTER_HOST_PORT:-13000}"
    MINIO_API_HOST_PORT="${MINIO_API_HOST_PORT:-19000}"

    echo "--- smoke: C1 API health ---"
    curl -fsS "http://localhost:${FASTAPI_HOST_PORT}/healthz" \
      | grep -q '"ok"' \
      || { echo "FAIL: smoke C1 API health: /healthz did not return ok"; exit 1; }
    echo "smoke C1 API health: OK"

    echo "--- smoke: C2 DB connection ---"
    # C2 DB connection: proven by C1 — FastAPI lifespan runs a SELECT 1 probe on
    # startup (added this sprint); /healthz is unreachable if Postgres is down.
    echo "smoke C2 DB connection: OK (via FastAPI lifespan)"

    echo "--- smoke: C3 MinIO connectivity ---"
    STATUS=$(curl -s -o /dev/null -w '%{http_code}' \
      "http://localhost:${MINIO_API_HOST_PORT}/minio/health/live") \
      || { echo "FAIL: smoke C3 MinIO connectivity: connection refused or curl error"; exit 1; }
    [[ "$STATUS" == "200" ]] \
      || { echo "FAIL: smoke C3 MinIO connectivity: /minio/health/live returned $STATUS"; exit 1; }
    echo "smoke C3 MinIO connectivity: OK"

    echo "--- smoke: C4 Dagster connectivity ---"
    curl -fsS "http://localhost:${DAGSTER_HOST_PORT}/server_info" \
      | grep -q '"dagster_version"' \
      || { echo "FAIL: smoke C4 Dagster connectivity: /server_info did not return dagster_version"; exit 1; }
    echo "smoke C4 Dagster connectivity: OK"
    ;;
  backend)
    exists apps/api || { echo "no apps/api yet"; exit 0; }
    run "cd apps/api && uv run ruff check ."
    run "cd apps/api && uv run mypy dataplat_api"
    run "cd apps/api && uv run pytest -q"
    ;;
  frontend)
    exists apps/web || { echo "no apps/web yet"; exit 0; }
    run "pnpm --filter web lint"
    run "pnpm --filter web typecheck"
    run "pnpm --filter web test --run"
    ;;
  contract)
    exists apps/api || { echo "no apps/api yet"; exit 0; }
    exists packages/api-types || { echo "no packages/api-types yet"; exit 0; }
    # S007-F-007: packages/api-types/ now exists (openapi.json committed) but the
    # Makefile + pnpm TS generator are not yet wired (web sprint deferred).
    # Without this guard, `make codegen` would fire and exit 1 on a missing Makefile,
    # breaking the all) baseline.  Once the web sprint scaffolds the Makefile, this
    # guard becomes inert and full TS codegen runs automatically.
    [[ -f Makefile ]] || { echo "no Makefile yet (codegen deferred to web sprint)"; exit 0; }
    run "make codegen"
    run "git diff --exit-code packages/api-types/"
    ;;
  migration)
    exists apps/api/alembic || { echo "no alembic yet"; exit 0; }
    COMPOSE="docker/docker-compose.dev.yml"
    [[ -f "$COMPOSE" ]] || { echo "no $COMPOSE — stack not available"; exit 1; }
    API="docker compose -f $COMPOSE exec -T fastapi"
    PG="docker compose -f $COMPOSE exec -T postgres"

    echo "--- migration pre-flight: alembic installed in fastapi container ---"
    # The fastapi container installs via pip (not uv), so alembic is on PATH directly.
    $API alembic --version \
      || { echo "FAIL: alembic not in fastapi container — run: docker compose -f $COMPOSE build fastapi && docker compose -f $COMPOSE up -d fastapi"; exit 1; }

    echo "--- migration V1: upgrade head exits 0 ---"
    run "$API alembic upgrade head"

    echo "--- migration V2: all 8 tables present ---"
    for TABLE in users source_collection source document_variant operator recipe dataset run; do
      $PG psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='$TABLE'" \
        | grep -q '^1$' \
        || { echo "FAIL: table '$TABLE' not found"; exit 1; }
      echo "  table $TABLE: OK"
    done

    echo "--- migration V3: idempotent re-run ---"
    run "$API alembic upgrade head"

    echo "--- migration V4-extra: downgrade base + upgrade head round-trip ---"
    run "$API alembic downgrade base"
    run "$API alembic upgrade head"
    ;;
  plugin)
    PLUGIN_NAME="${1:?usage: checks.sh plugin <name>}"
    [[ -d "plugins/$PLUGIN_NAME" ]] || { echo "no plugins/$PLUGIN_NAME"; exit 1; }
    run "cd plugins/$PLUGIN_NAME && uv run pytest -q"
    run "cd plugins/$PLUGIN_NAME && uv run ruff check ."
    ;;
  buckets)
    # F-003: MinIO bucket initialisation verification
    COMPOSE="docker/docker-compose.dev.yml"
    [[ -f "$COMPOSE" ]] || { echo "no $COMPOSE yet"; exit 0; }

    MINIO_USER="${MINIO_ROOT_USER:-minioadmin}"
    MINIO_PASS="${MINIO_ROOT_PASSWORD:-devpassword}"

    echo "--- buckets V1: all 5 buckets present ---"
    # grep -qxF: exact whole-line match prevents 'sources_backup' satisfying
    # the 'sources' check. MC_HOST_chk keeps credentials out of CLI args.
    # NOTE: if MINIO_ROOT_PASSWORD ever contains URL-special chars (@, :, /, ?, #),
    # switch to `mc alias set` with --api S3v4 and pass creds via STDIN instead.
    # NOTE: 'documents-vlm' uses a hyphen (not underscore) — S3/MinIO bucket
    # names prohibit underscores. This maps to design doc's 'documents_vlm'.
    # --entrypoint sh overrides the minio-init service's init-buckets.sh so
    # we get a plain mc ls instead of re-running bucket creation.
    BUCKET_LIST=$(docker compose -f "$COMPOSE" run --rm -T \
      --entrypoint sh \
      -e MC_HOST_chk="http://${MINIO_USER}:${MINIO_PASS}@minio:9000" \
      minio-init \
      -c "mc ls chk/" 2>/dev/null | awk '{print $NF}')
    for BUCKET in sources documents documents-vlm lance datasets; do
      echo "${BUCKET_LIST}" | grep -qxF "${BUCKET}/" \
        || { echo "FAIL: bucket '${BUCKET}' not found"; exit 1; }
      echo "  bucket ${BUCKET}: OK"
    done

    echo "--- buckets V2: upload/head/delete test object to sources bucket ---"
    # Credentials via -e flags (not string interpolation) to avoid breakage
    # if credentials contain shell-special characters.
    # IMPORTANT: 'test.txt' is a flat key for verification ONLY.
    # Production code (F-011+) MUST use CAS paths (sha256(content) layout)
    # per CLAUDE.md hard invariant #2 and CAL-5.
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
  dagster)
    # F-004: DagsterGateway + GET /api/admin/dagster-status
    # Runs from repo root (same assumption as all other layers — no cd).
    COMPOSE="docker/docker-compose.dev.yml"
    [[ -f "$COMPOSE" ]] || { echo "no $COMPOSE yet"; exit 0; }

    FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"

    echo "--- dagster: mint Bearer token for protected routes ---"
    DAGSTER_TOKEN_BODY=$(mktemp)
    DAGSTER_TOKEN_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
      -d "username=admin@example.com&password=testpassword123" \
      -H "Content-Type: application/x-www-form-urlencoded" \
      -w '%{http_code}' -o "$DAGSTER_TOKEN_BODY")
    test "$DAGSTER_TOKEN_STATUS" = "200" \
      || { echo "FAIL: dagster) could not mint auth token (status $DAGSTER_TOKEN_STATUS) — run 'bash $0 auth' first"; rm -f "$DAGSTER_TOKEN_BODY"; exit 1; }
    DAGSTER_TOKEN=$(python3 -c "import json; print(json.load(open('$DAGSTER_TOKEN_BODY'))['access_token'])")
    rm -f "$DAGSTER_TOKEN_BODY"

    echo "--- dagster V1: GET /api/admin/dagster-status returns 200 with dagster_version ---"
    # Curl output piped directly into python3 stdin — never captured into a
    # shell variable.  This avoids shell injection / Python syntax breakage
    # from any single-quote, backslash, or $ in the response body.
    curl -fsS -H "Authorization: Bearer $DAGSTER_TOKEN" \
      "http://localhost:${FASTAPI_HOST_PORT}/api/admin/dagster-status" \
      | python3 -c "
import json, sys
body = json.load(sys.stdin)
assert 'dagster_version' in body, f'missing dagster_version key: {body}'
assert len(body['dagster_version']) > 0, f'dagster_version is empty: {body}'
print('  V1 OK: dagster_version =', body['dagster_version'])
" || { echo "FAIL: V1 check failed (non-200, connection refused, or assertion error)"; exit 1; }

    echo "--- dagster boundary: no raw httpx->dagster calls outside gateway module ---"
    # Grep for httpx.(get|post|AsyncClient) on the same line as "dagster"
    # in any .py file NOT under dataplat_api/dagster/.
    # Runs from repo root; paths are relative to CWD.
    BAD_CALLS=$(grep -rn --include='*.py' -E 'httpx\.(get|post|AsyncClient)' \
      apps/api/dataplat_api/ \
      | grep -i 'dagster' \
      | grep -v 'apps/api/dataplat_api/dagster/' \
      || true)
    if [[ -n "$BAD_CALLS" ]]; then
      echo "FAIL: raw httpx call to Dagster outside gateway module:"
      echo "$BAD_CALLS"
      exit 1
    fi
    echo "  gateway boundary check: OK"

    echo "--- dagster V2: restart fastapi container; route still returns 200 ---"
    docker compose -f "$COMPOSE" restart fastapi

    # Wait for fastapi healthy (max 30s; python urllib avoids curl-in-container dep)
    READY=0
    for i in $(seq 1 30); do
      docker compose -f "$COMPOSE" exec -T fastapi \
        python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=2).getcode()==200 else 1)" \
        2>/dev/null && { READY=1; break; }
      sleep 1
    done
    [[ "$READY" == "1" ]] || { echo "FAIL: fastapi did not become healthy after restart"; exit 1; }

    curl -fsS -H "Authorization: Bearer $DAGSTER_TOKEN" \
      "http://localhost:${FASTAPI_HOST_PORT}/api/admin/dagster-status" \
      | python3 -c "
import json, sys
body = json.load(sys.stdin)
assert 'dagster_version' in body, f'missing dagster_version key: {body}'
assert len(body['dagster_version']) > 0, f'dagster_version is empty: {body}'
print('  V2 OK (post-restart): dagster_version =', body['dagster_version'])
" || { echo "FAIL: V2 check failed after restart"; exit 1; }

    # F-012: Reload dagster-webserver with updated definitions.py (bind-mounted dagster/).
    # The bind mount (added this sprint) means no image rebuild is needed —
    # docker compose up -d has already mounted the live dagster/ tree.
    # A restart is required because the Dagster webserver does NOT hot-reload Python.
    echo "--- dagster F012-prerestart: reload dagster-webserver with updated definitions ---"
    docker compose -f "$COMPOSE" restart dagster-webserver

    # Wait for dagster-webserver healthy (max 60s — longer than fastapi because Dagster
    # re-imports code location on restart which takes a few extra seconds).
    DAGSTER_READY=0
    for i in $(seq 1 60); do
      STATUS=$(curl -s -o /dev/null -w '%{http_code}' \
        "http://localhost:${DAGSTER_HOST_PORT:-13000}/dagster_version" 2>/dev/null || echo "000")
      [[ "$STATUS" == "200" ]] && { DAGSTER_READY=1; break; }
      sleep 1
    done
    [[ "$DAGSTER_READY" == "1" ]] || { echo "FAIL: dagster-webserver did not become healthy after restart"; exit 1; }
    echo "  dagster-webserver restarted and healthy"

    echo "--- dagster F012-V1: dynamic partition key appears in Dagster after upload ---"
    # Mint a fresh token for the F012 upload.
    F012_TOKEN_BODY=$(mktemp)
    F012_TOKEN_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
      -d "username=admin@example.com&password=testpassword123" \
      -H "Content-Type: application/x-www-form-urlencoded" \
      -w '%{http_code}' -o "$F012_TOKEN_BODY")
    test "$F012_TOKEN_STATUS" = "200" \
      || { echo "FAIL: dagster F012-V1 could not mint token (status $F012_TOKEN_STATUS)"; rm -f "$F012_TOKEN_BODY"; exit 1; }
    F012_TOKEN=$(python3 -c "import json; print(json.load(open('$F012_TOKEN_BODY'))['access_token'])")
    rm -f "$F012_TOKEN_BODY"

    # Generate a minimal valid PDF fixture.
    F012_PDF=$(mktemp /tmp/f012-XXXXXX.pdf)
    python3 -c "
pdf = (b'%PDF-1.4\n1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n'
       b'2 0 obj<</Type /Pages /Kids[3 0 R] /Count 1>>endobj\n'
       b'3 0 obj<</Type /Page /MediaBox[0 0 612 792] /Parent 2 0 R>>endobj\n'
       b'xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n'
       b'0000000058 00000 n \n0000000115 00000 n \n'
       b'trailer<</Size 4 /Root 1 0 R>>\nstartxref\n182\n%%EOF\n')
open('$F012_PDF', 'wb').write(pdf)
"

    # Upload the PDF to capture the source id and expected partition key.
    F012_UPLOAD_BODY=$(mktemp)
    F012_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/upload" \
      -H "Authorization: Bearer $F012_TOKEN" \
      -F "file=@${F012_PDF};type=application/pdf" \
      -w '%{http_code}' -o "$F012_UPLOAD_BODY")
    rm -f "$F012_PDF"
    test "$F012_STATUS" = "201" \
      || { echo "FAIL: dagster F012-V1 upload returned $F012_STATUS: $(cat "$F012_UPLOAD_BODY")"; rm -f "$F012_UPLOAD_BODY"; exit 1; }
    F012_SRC_ID=$(python3 -c "import json; print(json.load(open('$F012_UPLOAD_BODY'))['id'])")
    rm -f "$F012_UPLOAD_BODY"
    F012_PARTITION_KEY="src_${F012_SRC_ID}"
    echo "  uploaded source id=$F012_SRC_ID, expected partition key=$F012_PARTITION_KEY"

    # Query Dagster GraphQL for the source asset's partition keys from inside the container.
    PARTITION_CHECK=$(docker compose -f "$COMPOSE" exec -T dagster-webserver \
      python3 -c "
import urllib.request, json, sys
url = 'http://localhost:3000/graphql'
query = json.dumps({
    'query': '''query {
        assetNodes(assetKeys: [{path: [\"source\"]}]) {
            assetKey { path }
            isPartitioned
            partitionKeys
        }
    }'''
})
req = urllib.request.Request(url, data=query.encode(), headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req)
data = json.load(resp)
nodes = data['data']['assetNodes']
if not nodes:
    print('FAIL: no assetNodes returned for source', file=sys.stderr)
    sys.exit(1)
node = nodes[0]
keys = node.get('partitionKeys', [])
target = '$F012_PARTITION_KEY'
if target in keys:
    print(f'  F012-V1 OK: partition key {target} found in {len(keys)} keys')
else:
    print(f'FAIL: partition key {target} not found. Keys: {keys}', file=sys.stderr)
    sys.exit(1)
" 2>&1)
    echo "$PARTITION_CHECK" | grep -q "FAIL" \
      && { echo "$PARTITION_CHECK"; exit 1; } || echo "$PARTITION_CHECK"

    echo "--- dagster F012-V2: materialization event recorded in Dagster ---"
    # Confirmed query shape (Dagster 1.11.16):
    #   assetMaterializations arg is 'partitions' (plural, list), NOT 'partitionInLast'.
    #   MaterializationEvent has a 'partition' (singular) field.
    # Verified end-to-end against live Dagster 1.11.16 (S012-F-012 agreed.md §7-F012-V2).
    MAT_CHECK=$(docker compose -f "$COMPOSE" exec -T dagster-webserver \
      python3 -c "
import urllib.request, json, sys
url = 'http://localhost:3000/graphql'
query = json.dumps({
    'query': '''query {
        assetOrError(assetKey: {path: [\"source\"]}) {
            __typename
            ... on Asset {
                assetMaterializations(partitions: [\"$F012_PARTITION_KEY\"], limit: 10) {
                    partition
                    runId
                }
            }
            ... on AssetNotFoundError { message }
        }
    }'''
})
req = urllib.request.Request(url, data=query.encode(), headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req)
data = json.load(resp)
result = data['data']['assetOrError']
typename = result.get('__typename')
if typename != 'Asset':
    print(f'FAIL: assetOrError returned {typename}', file=sys.stderr)
    sys.exit(1)
mats = result.get('assetMaterializations', [])
target = '$F012_PARTITION_KEY'
found = any(m.get('partition') == target for m in mats)
if found:
    print(f'  F012-V2 OK: materialization event for partition {target} found')
else:
    print(f'FAIL: no materialization for {target}. Got: {mats}', file=sys.stderr)
    sys.exit(1)
" 2>&1)
    echo "$MAT_CHECK" | grep -q "FAIL" \
      && { echo "$MAT_CHECK"; exit 1; } || echo "$MAT_CHECK"
    ;;
  runs)
    # F-005: hello-world Dagster job launch + status poll
    COMPOSE="docker/docker-compose.dev.yml"
    [[ -f "$COMPOSE" ]] || { echo "no $COMPOSE yet"; exit 0; }

    FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"

    echo "--- runs: mint Bearer token for protected routes ---"
    RUNS_TOKEN_BODY=$(mktemp)
    RUNS_TOKEN_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
      -d "username=admin@example.com&password=testpassword123" \
      -H "Content-Type: application/x-www-form-urlencoded" \
      -w '%{http_code}' -o "$RUNS_TOKEN_BODY")
    test "$RUNS_TOKEN_STATUS" = "200" \
      || { echo "FAIL: runs) could not mint auth token (status $RUNS_TOKEN_STATUS) — run 'bash $0 auth' first"; rm -f "$RUNS_TOKEN_BODY"; exit 1; }
    RUNS_TOKEN=$(python3 -c "import json; print(json.load(open('$RUNS_TOKEN_BODY'))['access_token'])")
    rm -f "$RUNS_TOKEN_BODY"

    echo "--- runs V1: trigger hello-world job via FastAPI ---"
    # 2-step pattern: capture body to a temp file, write status code to RESP.
    # This ensures non-201 responses print the body for debugging rather than
    # silently failing. curl -sS shows connection errors on stderr without -f
    # suppressing the body.
    # mktemp avoids clobber risk on shared CI hosts (vs fixed /tmp/launch_body).
    LAUNCH_BODY=$(mktemp)
    RESP=$(curl -sS -X POST "http://localhost:${FASTAPI_HOST_PORT}/api/admin/runs/hello-world" \
      -H "Authorization: Bearer $RUNS_TOKEN" \
      -w '\n%{http_code}' -o "$LAUNCH_BODY")
    STATUS_CODE=$(echo "$RESP" | tail -n1)
    BODY=$(cat "$LAUNCH_BODY")
    rm -f "$LAUNCH_BODY"
    test "$STATUS_CODE" = "201" || { echo "FAIL: expected 201 got $STATUS_CODE: $BODY"; exit 1; }
    RUN_ID=$(echo "$BODY" | python3 -c "
import json, sys
body = json.load(sys.stdin)
assert 'dagster_run_id' in body, f'missing dagster_run_id key: {body}'
assert body['dagster_run_id'], f'dagster_run_id is empty: {body}'
print(body['dagster_run_id'], end='')
")
    test -n "$RUN_ID" || { echo "FAIL: no dagster_run_id returned from trigger"; exit 1; }
    echo "  triggered run: $RUN_ID"

    echo "--- runs V1: poll GET /api/runs/{run_id} until success or timeout ---"
    STATUS="unknown"
    STATUS_BODY=$(mktemp)
    for i in $(seq 1 60); do
      RESP=$(curl -sS "http://localhost:${FASTAPI_HOST_PORT}/api/runs/${RUN_ID}" \
        -H "Authorization: Bearer $RUNS_TOKEN" \
        -w '\n%{http_code}' -o "$STATUS_BODY")
      STATUS_CODE=$(echo "$RESP" | tail -n1)
      BODY=$(cat "$STATUS_BODY")
      test "$STATUS_CODE" = "200" || { echo "GET /api/runs/$RUN_ID -> $STATUS_CODE: $BODY"; rm -f "$STATUS_BODY"; exit 1; }
      STATUS=$(echo "$BODY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','unknown'), end='')")
      [ "$STATUS" = "success" ] && break
      [ "$STATUS" = "failure" ] && { echo "FAIL: hello-world run reached failure status: $BODY"; rm -f "$STATUS_BODY"; exit 1; }
      sleep 1
    done
    rm -f "$STATUS_BODY"
    test "$STATUS" = "success" || { echo "FAIL: timeout waiting for success (last status=$STATUS)"; exit 1; }
    echo "  V1 OK: hello-world run reached success in ~${i}s"

    echo "--- runs V2: gateway boundary — no raw httpx import outside gateway module ---"
    # Covers both 'import httpx' and 'from httpx import ...' import forms.
    # apps/api/tests/ is outside the grep root (dataplat_api/) so no test-exclusion
    # clause is needed.
    # NOTE: This same stronger pattern should also be applied to the existing
    # dagster) layer's boundary grep when that layer is next revised — keeping
    # the boundary check uniform across both layers (INFO, not a blocker for F-005).
    RAW_HTTPX=$(grep -rln -E '(import httpx|from httpx import)' \
      apps/api/dataplat_api --include='*.py' \
      | grep -v 'dataplat_api/dagster/' \
      || true)
    test -z "$RAW_HTTPX" || { echo "FAIL: raw httpx import outside gateway: $RAW_HTTPX"; exit 1; }
    echo "  V2 OK: gateway boundary intact"

    # F-018: POST /api/runs — trigger MinerU extraction backfill.
    # $RUNS_TOKEN and $FASTAPI_HOST_PORT already set by the F-005 block above.
    COMPOSE_F018="docker/docker-compose.dev.yml"

    echo "--- runs F018 setup: upload a source for backfill test ---"
    F018_PDF=$(mktemp /tmp/f018-XXXXXX.pdf)
    python3 -c "
pdf = (b'%PDF-1.4\n1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n'
       b'2 0 obj<</Type /Pages /Kids[3 0 R] /Count 1>>endobj\n'
       b'3 0 obj<</Type /Page /MediaBox[0 0 612 792] /Parent 2 0 R>>endobj\n'
       b'xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n'
       b'0000000058 00000 n \n0000000115 00000 n \n'
       b'trailer<</Size 4 /Root 1 0 R>>\nstartxref\n182\n%%EOF\nf018')
open('$F018_PDF', 'wb').write(pdf)
"
    F018_UP_BODY=$(mktemp)
    F018_UP_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/upload" \
      -H "Authorization: Bearer $RUNS_TOKEN" \
      -F "file=@${F018_PDF};type=application/pdf" \
      -w '%{http_code}' -o "$F018_UP_BODY")
    rm -f "$F018_PDF"
    test "$F018_UP_STATUS" = "201" \
      || { echo "FAIL: runs F018 setup upload returned $F018_UP_STATUS: $(cat "$F018_UP_BODY")"; rm -f "$F018_UP_BODY"; exit 1; }
    F018_SRC_ID=$(python3 -c "import json; print(json.load(open('$F018_UP_BODY'))['id'])")
    rm -f "$F018_UP_BODY"
    echo "  uploaded source id=$F018_SRC_ID"

    echo "--- runs F018-V1: POST /api/runs returns 202 with dagster_run_id + run_id ---"
    V1_BODY=$(mktemp)
    V1_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/runs" \
      -H "Authorization: Bearer $RUNS_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"asset\": \"extract_mineru\", \"source_ids\": [${F018_SRC_ID}]}" \
      -w '%{http_code}' -o "$V1_BODY")
    test "$V1_STATUS" = "202" \
      || { echo "FAIL: F018-V1 returned $V1_STATUS: $(cat "$V1_BODY")"; rm -f "$V1_BODY"; exit 1; }
    python3 -c "
import json, sys
body = json.load(open('$V1_BODY'))
assert 'dagster_run_id' in body, f'missing dagster_run_id: {body}'
assert 'run_id' in body, f'missing run_id: {body}'
assert isinstance(body['dagster_run_id'], str) and len(body['dagster_run_id']) > 0, \
  f'dagster_run_id empty or not str: {body}'
assert isinstance(body['run_id'], int), f'run_id not int: {body}'
print('  F018-V1 OK: dagster_run_id=%s run_id=%d' % (body['dagster_run_id'], body['run_id']))
" || { echo "FAIL: F018-V1 response shape incorrect"; rm -f "$V1_BODY"; exit 1; }
    F018_BACKFILL_ID=$(python3 -c "import json; print(json.load(open('$V1_BODY'))['dagster_run_id'])")
    F018_RUN_ID=$(python3 -c "import json; print(json.load(open('$V1_BODY'))['run_id'])")
    rm -f "$V1_BODY"

    echo "--- runs F018-V2: run row exists with kind=extract, status=pending ---"
    docker compose -f "$COMPOSE_F018" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "SELECT kind || '|' || status FROM run WHERE id=${F018_RUN_ID}" \
      | grep -q '^extract|pending$' \
      || { echo "FAIL: F018-V2 — run row missing or wrong kind/status (expected extract|pending)"; exit 1; }
    echo "  F018-V2 OK: run row kind=extract status=pending"

    echo "--- runs F018-V3: Dagster shows backfill for extract_mineru ---"
    BACKFILL_CHECK=$(docker compose -f "$COMPOSE_F018" exec -T dagster-webserver \
      python3 -c "
import urllib.request, json, sys
url = 'http://localhost:3000/graphql'
query = json.dumps({
    'query': '''query GetBackfill(\$id: String!) {
        partitionBackfillOrError(backfillId: \$id) {
            __typename
            ... on PartitionBackfill {
                id
                isAssetBackfill
                status
                assetSelection { path }
            }
            ... on BackfillNotFoundError { message }
            ... on PythonError { message }
        }
    }''',
    'variables': {'id': '$F018_BACKFILL_ID'}
})
req = urllib.request.Request(url, data=query.encode(), headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req)
data = json.load(resp)
result = data['data']['partitionBackfillOrError']
typename = result.get('__typename')
if typename != 'PartitionBackfill':
    print(f'FAIL: partitionBackfillOrError returned {typename}: {result}', file=sys.stderr)
    sys.exit(1)
assert result.get('isAssetBackfill'), f'isAssetBackfill not true: {result}'
paths = [seg for sel in result.get('assetSelection', []) for seg in sel.get('path', [])]
assert 'extract_mineru' in paths, f'extract_mineru not in assetSelection paths: {paths}'
print(f'  F018-V3 OK: backfillId={result[\"id\"]} status={result[\"status\"]} assets={paths}')
" 2>&1)
    echo "$BACKFILL_CHECK" | grep -q "FAIL" \
      && { echo "$BACKFILL_CHECK"; exit 1; } || echo "$BACKFILL_CHECK"

    # ── F024: chunks asset backfill ─────────────────────────────────────────
    echo "--- runs F024-setup: upload a fresh PDF for chunks trigger test ---"
    F024_PDF=$(mktemp --suffix=.pdf)
    python3 -c "
import struct, zlib, io

def obj(n, s): return f'{n} 0 obj\n{s}\nendobj\n'
catalog = obj(1, '<</Type /Catalog /Pages 2 0 R>>')
pages   = obj(2, '<</Type /Pages /Kids [3 0 R] /Count 1>>')
page    = obj(3, '<</Type /Page /MediaBox [0 0 612 792] /Parent 2 0 R>>')

body = catalog + pages + page
xref_offset = len(b'%PDF-1.4\n') + len(body.encode())
xref = (
    'xref\n0 4\n'
    '0000000000 65535 f \n'
    '0000000009 00000 n \n'
    '0000000058 00000 n \n'
    '0000000115 00000 n \n'
)
trailer = '<</Size 4 /Root 1 0 R>>'
pdf = f'%PDF-1.4\n{body}{xref}trailer{trailer}\nstartxref\n{xref_offset}\n%%EOF\n'
import sys; sys.stdout.buffer.write(pdf.encode())
" > "$F024_PDF" 2>/dev/null || printf '%%PDF-1.4\n1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n2 0 obj<</Type /Pages /Kids[3 0 R] /Count 1>>endobj\n3 0 obj<</Type /Page /MediaBox[0 0 612 792] /Parent 2 0 R>>endobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \ntrailer<</Size 4 /Root 1 0 R>>\nstartxref\n182\n%%%%EOF\n' > "$F024_PDF"
    F024_UP_BODY=$(mktemp)
    F024_UP_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/upload" \
      -H "Authorization: Bearer $RUNS_TOKEN" \
      -F "file=@${F024_PDF};type=application/pdf" \
      -w '%{http_code}' -o "$F024_UP_BODY")
    rm -f "$F024_PDF"
    test "$F024_UP_STATUS" = "201" \
      || { echo "FAIL: runs F024 setup upload returned $F024_UP_STATUS: $(cat "$F024_UP_BODY")"; rm -f "$F024_UP_BODY"; exit 1; }
    F024_SRC_ID=$(python3 -c "import json; print(json.load(open('$F024_UP_BODY'))['id'])")
    rm -f "$F024_UP_BODY"
    echo "  uploaded source id=$F024_SRC_ID"

    echo "--- runs F024-V1: POST /api/runs?asset=chunks returns 202 with dagster_run_id + run_id ---"
    F024_V1_BODY=$(mktemp)
    F024_V1_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/runs" \
      -H "Authorization: Bearer $RUNS_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"asset\": \"chunks\", \"source_ids\": [${F024_SRC_ID}]}" \
      -w '%{http_code}' -o "$F024_V1_BODY")
    test "$F024_V1_STATUS" = "202" \
      || { echo "FAIL: F024-V1 returned $F024_V1_STATUS: $(cat "$F024_V1_BODY")"; rm -f "$F024_V1_BODY"; exit 1; }
    python3 -c "
import json, sys
body = json.load(open('$F024_V1_BODY'))
assert 'dagster_run_id' in body, f'missing dagster_run_id: {body}'
assert 'run_id' in body, f'missing run_id: {body}'
assert isinstance(body['dagster_run_id'], str) and len(body['dagster_run_id']) > 0, \
  f'dagster_run_id empty or not str: {body}'
assert isinstance(body['run_id'], int), f'run_id not int: {body}'
print('  F024-V1 OK: dagster_run_id=%s run_id=%d' % (body['dagster_run_id'], body['run_id']))
" || { echo "FAIL: F024-V1 response shape incorrect"; rm -f "$F024_V1_BODY"; exit 1; }
    F024_BACKFILL_ID=$(python3 -c "import json; print(json.load(open('$F024_V1_BODY'))['dagster_run_id'])")
    F024_RUN_ID=$(python3 -c "import json; print(json.load(open('$F024_V1_BODY'))['run_id'])")
    rm -f "$F024_V1_BODY"

    echo "--- runs F024-V2: run row exists with kind=chunk, status=pending ---"
    docker compose -f "$COMPOSE_F018" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "SELECT kind || '|' || status FROM run WHERE id=${F024_RUN_ID}" \
      | grep -q '^chunk|pending$' \
      || { echo "FAIL: F024-V2 — run row missing or wrong kind/status (expected chunk|pending)"; exit 1; }
    echo "  F024-V2 OK: run row kind=chunk status=pending"

    echo "--- runs F024-V3: Dagster shows backfill for chunks asset ---"
    F024_BACKFILL_CHECK=$(docker compose -f "$COMPOSE_F018" exec -T dagster-webserver \
      python3 -c "
import urllib.request, json, sys
url = 'http://localhost:3000/graphql'
query = json.dumps({
    'query': '''query GetChunksBackfill(\$id: String!) {
        partitionBackfillOrError(backfillId: \$id) {
            __typename
            ... on PartitionBackfill {
                id
                isAssetBackfill
                status
                assetSelection { path }
            }
            ... on BackfillNotFoundError { message }
            ... on PythonError { message }
        }
    }''',
    'variables': {'id': '$F024_BACKFILL_ID'}
})
req = urllib.request.Request(url, data=query.encode(), headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req)
data = json.load(resp)
result = data['data']['partitionBackfillOrError']
typename = result.get('__typename')
if typename != 'PartitionBackfill':
    print(f'FAIL: partitionBackfillOrError returned {typename}: {result}', file=sys.stderr)
    sys.exit(1)
assert result.get('isAssetBackfill'), f'isAssetBackfill not true: {result}'
paths = [seg for sel in result.get('assetSelection', []) for seg in sel.get('path', [])]
assert 'chunks' in paths, f'chunks not in assetSelection paths: {paths}'
print(f'  F024-V3 OK: backfillId={result[\"id\"]} status={result[\"status\"]} assets={paths}')
" 2>&1)
    echo "$F024_BACKFILL_CHECK" | grep -q "FAIL" \
      && { echo "$F024_BACKFILL_CHECK"; exit 1; } || echo "$F024_BACKFILL_CHECK"
    ;;
  auth)
    # F-007: seed-admin CLI + POST /api/auth/token
    COMPOSE="docker/docker-compose.dev.yml"
    [[ -f "$COMPOSE" ]] || { echo "no $COMPOSE yet"; exit 0; }

    FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"

    echo "--- auth V1: seed creates exactly one row ---"
    docker compose -f "$COMPOSE" exec -T fastapi \
      python -m dataplat_api.cli seed-admin \
      --email admin@example.com --password testpassword123
    docker compose -f "$COMPOSE" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "SELECT COUNT(*) FROM users WHERE email='admin@example.com'" \
      | grep -q '^1$' \
      || { echo "FAIL: auth V1 seed did not create exactly one row"; exit 1; }
    echo "auth V1 seed: OK"

    echo "--- auth V2: POST /api/auth/token correct credentials → 200 ---"
    AUTH_TOKEN_BODY=$(mktemp)
    RESP=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
      -d "username=admin@example.com&password=testpassword123" \
      -H "Content-Type: application/x-www-form-urlencoded" \
      -w '\n%{http_code}' -o "$AUTH_TOKEN_BODY")
    STATUS_CODE=$(echo "$RESP" | tail -n1)
    test "$STATUS_CODE" = "200" \
      || { echo "FAIL: auth V2 token returned $STATUS_CODE: $(cat "$AUTH_TOKEN_BODY")"; rm -f "$AUTH_TOKEN_BODY"; exit 1; }
    python3 -c "
import json, sys
body = json.load(open('$AUTH_TOKEN_BODY'))
assert 'access_token' in body, f'missing access_token: {body}'
assert body.get('token_type') == 'bearer', f'wrong token_type: {body}'
print('  V2 OK: access_token present, token_type=bearer')
" || { echo "FAIL: auth V2 response shape incorrect"; rm -f "$AUTH_TOKEN_BODY"; exit 1; }
    rm -f "$AUTH_TOKEN_BODY"
    echo "auth V2 correct credentials: OK"

    echo "--- auth V3: POST /api/auth/token wrong password → 401 ---"
    STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
      -d "username=admin@example.com&password=WRONG_PASSWORD_XYZ" \
      -H "Content-Type: application/x-www-form-urlencoded" \
      -o /dev/null -w '%{http_code}')
    test "$STATUS" = "401" \
      || { echo "FAIL: auth V3 wrong password returned $STATUS (expected 401)"; exit 1; }
    echo "auth V3 wrong password: OK"

    echo "--- auth V4: GET /api/sources/collections without token → 401 ---"
    STATUS=$(curl -sS -o /dev/null -w '%{http_code}' \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections")
    test "$STATUS" = "401" \
      || { echo "FAIL: auth V4 returned $STATUS (expected 401)"; exit 1; }
    echo "auth V4 no-token 401: OK"

    echo "--- auth V5: GET /api/sources/collections with valid token → 200 ---"
    TOKEN_BODY=$(mktemp)
    RESP=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
      -d "username=admin@example.com&password=testpassword123" \
      -H "Content-Type: application/x-www-form-urlencoded" \
      -w '\n%{http_code}' -o "$TOKEN_BODY")
    STATUS_CODE=$(echo "$RESP" | tail -n1)
    test "$STATUS_CODE" = "200" \
      || { echo "FAIL: auth V5 could not mint token (status $STATUS_CODE): $(cat "$TOKEN_BODY")"; rm -f "$TOKEN_BODY"; exit 1; }
    TOKEN=$(python3 -c "import json; print(json.load(open('$TOKEN_BODY'))['access_token'])")
    rm -f "$TOKEN_BODY"

    STATUS=$(curl -sS -o /dev/null -w '%{http_code}' \
      -H "Authorization: Bearer $TOKEN" \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections")
    test "$STATUS" = "200" \
      || { echo "FAIL: auth V5 returned $STATUS (expected 200)"; exit 1; }
    echo "auth V5 valid-token 200: OK"

    echo "--- auth V6: GET /api/sources/collections with expired token → 401 ---"
    EXPIRED_TOKEN=$(docker compose -f "$COMPOSE" exec -T fastapi \
      python -c "
import jwt, time, os
payload = {
    'sub': '1',
    'email': 'admin@example.com',
    'iat': int(time.time()) - 7200,
    'exp': int(time.time()) - 3600,
}
token = jwt.encode(payload, os.environ['SECRET_KEY'], algorithm='HS256')
print(token, end='')
")
    STATUS=$(curl -sS -o /dev/null -w '%{http_code}' \
      -H "Authorization: Bearer $EXPIRED_TOKEN" \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections")
    test "$STATUS" = "401" \
      || { echo "FAIL: auth V6 expired token returned $STATUS (expected 401)"; exit 1; }
    echo "auth V6 expired-token 401: OK"
    ;;
  collections)
    # F-009: POST /api/sources/collections — create, 201/409 verification + owner_id
    COMPOSE="docker/docker-compose.dev.yml"
    [[ -f "$COMPOSE" ]] || { echo "no $COMPOSE yet"; exit 0; }

    FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"

    echo "--- collections: mint Bearer token ---"
    COLL_TOKEN_BODY=$(mktemp)
    COLL_TOKEN_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
      -d "username=admin@example.com&password=testpassword123" \
      -H "Content-Type: application/x-www-form-urlencoded" \
      -w '%{http_code}' -o "$COLL_TOKEN_BODY")
    test "$COLL_TOKEN_STATUS" = "200" \
      || { echo "FAIL: collections) could not mint token (status $COLL_TOKEN_STATUS) — run 'bash $0 auth' first"; rm -f "$COLL_TOKEN_BODY"; exit 1; }
    COLL_TOKEN=$(python3 -c "import json; print(json.load(open('$COLL_TOKEN_BODY'))['access_token'])")
    rm -f "$COLL_TOKEN_BODY"

    echo "--- collections V1: POST returns 201 with id (int) and name ---"
    COLL_BODY=$(mktemp)
    COLL_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections" \
      -H "Authorization: Bearer $COLL_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"name": "test-coll-checks", "dataset_card_md": "desc"}' \
      -w '%{http_code}' -o "$COLL_BODY")
    test "$COLL_STATUS" = "201" \
      || { echo "FAIL: collections V1 returned $COLL_STATUS: $(cat "$COLL_BODY")"; rm -f "$COLL_BODY"; exit 1; }
    python3 -c "
import json, sys
body = json.load(open('$COLL_BODY'))
assert isinstance(body.get('id'), int), f'id not int: {body}'
assert body.get('name') == 'test-coll-checks', f'name mismatch: {body}'
print('  V1 OK: id =', body['id'], 'name =', body['name'])
" || { echo "FAIL: collections V1 response shape incorrect"; rm -f "$COLL_BODY"; exit 1; }
    rm -f "$COLL_BODY"

    echo "--- collections V2: row exists in DB with owner_id IS NOT NULL ---"
    docker compose -f "$COMPOSE" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "SELECT id FROM source_collection WHERE name='test-coll-checks' AND owner_id IS NOT NULL" \
      | grep -qE '^[0-9]+$' \
      || { echo "FAIL: collections V2 row not found or owner_id is null"; exit 1; }
    echo "  V2 OK: row exists with non-null owner_id"

    echo "--- collections V3: duplicate name returns 409 ---"
    DUP_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections" \
      -H "Authorization: Bearer $COLL_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"name": "test-coll-checks"}' \
      -o /dev/null -w '%{http_code}')
    test "$DUP_STATUS" = "409" \
      || { echo "FAIL: collections V3 returned $DUP_STATUS (expected 409)"; exit 1; }
    echo "  V3 OK: duplicate name → 409"

    echo "--- collections LIST-V1/V2 setup: create 3 deterministic collections ---"
    for COLL_NAME in test-coll-list-a test-coll-list-b test-coll-list-c; do
      SETUP_STATUS=$(curl -sS -X POST \
        "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections" \
        -H "Authorization: Bearer $COLL_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"$COLL_NAME\"}" \
        -o /dev/null -w '%{http_code}')
      # Accept 201 (created) or 409 (already exists from a previous run) — both OK.
      [[ "$SETUP_STATUS" == "201" || "$SETUP_STATUS" == "409" ]] \
        || { echo "FAIL: collections LIST setup for $COLL_NAME returned $SETUP_STATUS"; exit 1; }
      echo "  setup $COLL_NAME: $SETUP_STATUS"
    done

    echo "--- collections LIST-V1: GET (no limit) returns total == items count >= 3 ---"
    LIST_BODY=$(mktemp)
    LIST_STATUS=$(curl -sS -X GET \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections" \
      -H "Authorization: Bearer $COLL_TOKEN" \
      -w '%{http_code}' -o "$LIST_BODY")
    test "$LIST_STATUS" = "200" \
      || { echo "FAIL: collections LIST-V1 returned $LIST_STATUS: $(cat "$LIST_BODY")"; rm -f "$LIST_BODY"; exit 1; }
    python3 -c "
import json, sys
body = json.load(open('$LIST_BODY'))
assert 'items' in body, f'missing items key: {body}'
assert 'total' in body, f'missing total key: {body}'
assert isinstance(body['total'], int), f'total not int: {body}'
assert body['total'] >= 3, f'expected total >= 3, got {body[\"total\"]}: {body}'
assert len(body['items']) >= 3, f'expected >= 3 items, got {len(body[\"items\"])}'
assert body['total'] == len(body['items']), \
  f'with no limit param, total should equal items count; got total={body[\"total\"]}, items={len(body[\"items\"])}'
print('  LIST-V1 OK: total =', body['total'], 'items count =', len(body['items']))
" || { echo "FAIL: collections LIST-V1 response shape incorrect"; rm -f "$LIST_BODY"; exit 1; }
    rm -f "$LIST_BODY"

    echo "--- collections LIST-V2: GET ?limit=2 returns 2 items but total >= 3 ---"
    LIST2_BODY=$(mktemp)
    LIST2_STATUS=$(curl -sS -X GET \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections?limit=2" \
      -H "Authorization: Bearer $COLL_TOKEN" \
      -w '%{http_code}' -o "$LIST2_BODY")
    test "$LIST2_STATUS" = "200" \
      || { echo "FAIL: collections LIST-V2 returned $LIST2_STATUS: $(cat "$LIST2_BODY")"; rm -f "$LIST2_BODY"; exit 1; }
    python3 -c "
import json, sys
body = json.load(open('$LIST2_BODY'))
assert len(body.get('items', [])) == 2, f'expected 2 items with limit=2, got {len(body.get(\"items\", []))}: {body}'
assert body.get('total', 0) >= 3, f'expected total >= 3, got {body.get(\"total\")}: {body}'
print('  LIST-V2 OK: items =', len(body['items']), 'total =', body['total'])
" || { echo "FAIL: collections LIST-V2 response shape incorrect"; rm -f "$LIST2_BODY"; exit 1; }
    rm -f "$LIST2_BODY"
    ;;
  sources)
    # F-011: POST /api/sources/upload — store PDF in MinIO, source row with sha256+storage_uri
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
print(__import__('hashlib').sha256(pdf).hexdigest())
" > /tmp/src_expected_sha256.txt \
      || { echo "FAIL: sources) could not generate PDF fixture"; rm -f "$PDF_FILE"; exit 1; }
    EXPECTED_SHA256=$(cat /tmp/src_expected_sha256.txt)

    echo "--- sources UPLOAD-V1: POST /api/sources/upload returns 201 ---"
    UPLOAD_BODY=$(mktemp)
    UPLOAD_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/upload" \
      -H "Authorization: Bearer $SRC_TOKEN" \
      -F "file=@${PDF_FILE};type=application/pdf" \
      -w '%{http_code}' -o "$UPLOAD_BODY")
    test "$UPLOAD_STATUS" = "201" \
      || { echo "FAIL: sources UPLOAD-V1 returned $UPLOAD_STATUS: $(cat "$UPLOAD_BODY")"; rm -f "$UPLOAD_BODY" "$PDF_FILE" /tmp/src_expected_sha256.txt; exit 1; }
    SRC_ID=$(python3 -c "
import json, re, sys
body = json.load(open('$UPLOAD_BODY'))
assert isinstance(body.get('id'), int), f'id not int: {body}'
uri = body.get('storage_uri', '')
assert re.match(r'^s3://sources/[0-9]+/original\.pdf$', uri), f'storage_uri shape wrong: {uri}'
assert uri == f\"s3://sources/{body['id']}/original.pdf\", f'id/uri mismatch: {body}'
print(body['id'])
" 2>&1) || { echo "FAIL: sources UPLOAD-V1 response shape incorrect: $SRC_ID"; rm -f "$UPLOAD_BODY" "$PDF_FILE" /tmp/src_expected_sha256.txt; exit 1; }
    echo "  UPLOAD-V1 OK: id=$SRC_ID storage_uri=s3://sources/${SRC_ID}/original.pdf"
    rm -f "$UPLOAD_BODY"

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
" || { echo "FAIL: sources UPLOAD-V2 MinIO head_object failed"; rm -f "$PDF_FILE" /tmp/src_expected_sha256.txt; exit 1; }

    echo "--- sources UPLOAD-V3: Postgres sha256 matches uploaded file ---"
    DB_SHA256=$(docker compose -f "$COMPOSE" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "SELECT sha256 FROM source WHERE id=${SRC_ID}")
    DB_SHA256=$(echo "$DB_SHA256" | tr -d '[:space:]')
    test "$DB_SHA256" = "$EXPECTED_SHA256" \
      || { echo "FAIL: sha256 mismatch: DB='$DB_SHA256' expected='$EXPECTED_SHA256'"; rm -f "$PDF_FILE" /tmp/src_expected_sha256.txt; exit 1; }
    echo "  UPLOAD-V3 OK: sha256=$DB_SHA256"

    echo "--- sources UPLOAD-V4: kind='file', mime_type='application/pdf' in Postgres ---"
    ROW=$(docker compose -f "$COMPOSE" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "SELECT kind, mime_type FROM source WHERE id=${SRC_ID}")
    echo "$ROW" | grep -q "file" \
      || { echo "FAIL: kind != 'file': $ROW"; rm -f "$PDF_FILE" /tmp/src_expected_sha256.txt; exit 1; }
    echo "$ROW" | grep -q "application/pdf" \
      || { echo "FAIL: mime_type != 'application/pdf': $ROW"; rm -f "$PDF_FILE" /tmp/src_expected_sha256.txt; exit 1; }
    echo "  UPLOAD-V4 OK: kind=file mime_type=application/pdf"

    echo "--- sources F013-V1: GET /api/sources/{id} returns 200 with required fields ---"
    DETAIL_BODY=$(mktemp)
    DETAIL_STATUS=$(curl -sS -X GET \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/${SRC_ID}" \
      -H "Authorization: Bearer $SRC_TOKEN" \
      -w '%{http_code}' -o "$DETAIL_BODY")
    test "$DETAIL_STATUS" = "200" \
      || { echo "FAIL: F013-V1 returned $DETAIL_STATUS: $(cat "$DETAIL_BODY")"; rm -f "$DETAIL_BODY" "$PDF_FILE" /tmp/src_expected_sha256.txt; exit 1; }
    python3 -c "
import json, sys
body = json.load(open('$DETAIL_BODY'))
required = ['id', 'storage_uri', 'sha256', 'size', 'mime_type', 'collection_id',
            'kind', 'original_name', 'dagster_partition_key', 'uploaded_at']
for field in required:
    assert field in body, f'missing field {field}: {body}'
assert body['id'] == ${SRC_ID}, f'id mismatch: {body}'
assert body['storage_uri'] == f\"s3://sources/${SRC_ID}/original.pdf\", f'storage_uri wrong: {body}'
assert body['sha256'], f'sha256 empty: {body}'
assert body['mime_type'] == 'application/pdf', f'mime_type wrong: {body}'
assert body['kind'] == 'file', f'kind wrong: {body}'
print('  F013-V1 OK: all required fields present, id=%d' % body['id'])
" || { echo "FAIL: F013-V1 response shape incorrect"; rm -f "$DETAIL_BODY" "$PDF_FILE" /tmp/src_expected_sha256.txt; exit 1; }
    rm -f "$DETAIL_BODY"

    echo "--- sources F013-V2: GET /api/sources/99999 returns 404 ---"
    NOTFOUND_STATUS=$(curl -sS -X GET \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/99999" \
      -H "Authorization: Bearer $SRC_TOKEN" \
      -o /dev/null -w '%{http_code}')
    test "$NOTFOUND_STATUS" = "404" \
      || { echo "FAIL: F013-V2 returned $NOTFOUND_STATUS (expected 404)"; rm -f "$PDF_FILE" /tmp/src_expected_sha256.txt; exit 1; }
    echo "  F013-V2 OK: /api/sources/99999 -> 404"

    echo "--- sources F014-V1: create collection and upload 3 sources to it ---"
    # Create a collection dedicated to F-014 tests (idempotent across reruns).
    F014_COLL_BODY=$(mktemp)
    F014_COLL_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections" \
      -H "Authorization: Bearer $SRC_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"name": "f014-test-collection"}' \
      -w '%{http_code}' -o "$F014_COLL_BODY")
    # Accept 201 (created) or 409 (already exists from a previous run).
    [[ "$F014_COLL_STATUS" == "201" || "$F014_COLL_STATUS" == "409" ]] \
      || { echo "FAIL: F014-V1 collection create returned $F014_COLL_STATUS: $(cat "$F014_COLL_BODY")"; rm -f "$F014_COLL_BODY" "$PDF_FILE" /tmp/src_expected_sha256.txt; exit 1; }
    if [[ "$F014_COLL_STATUS" == "201" ]]; then
      F014_COLL_ID=$(python3 -c "import json; print(json.load(open('$F014_COLL_BODY'))['id'])")
    else
      F014_COLL_ID=$(docker compose -f "$COMPOSE" exec -T postgres \
        psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
          "SELECT id FROM source_collection WHERE name='f014-test-collection'" \
        | tr -d '[:space:]')
    fi
    rm -f "$F014_COLL_BODY"
    echo "  F014 collection id=$F014_COLL_ID"

    # Upload 3 minimal PDFs to the collection (idempotent — duplicates are OK;
    # sha256 uniqueness is per-collection not enforced at DB level).
    for i in 1 2 3; do
      F014_PDF=$(mktemp /tmp/f014-XXXXXX.pdf)
      python3 -c "
pdf = (b'%PDF-1.4\n1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n'
       b'2 0 obj<</Type /Pages /Kids[3 0 R] /Count 1>>endobj\n'
       b'3 0 obj<</Type /Page /MediaBox[0 0 612 792] /Parent 2 0 R>>endobj\n'
       b'xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n'
       b'0000000058 00000 n \n0000000115 00000 n \n'
       b'trailer<</Size 4 /Root 1 0 R>>\nstartxref\n182\n%%EOF\n')
# Add a unique byte so each PDF has a distinct sha256.
pdf = pdf + b' ' * $i
open('$F014_PDF', 'wb').write(pdf)
"
      F014_UP_BODY=$(mktemp)
      F014_UP_STATUS=$(curl -sS -X POST \
        "http://localhost:${FASTAPI_HOST_PORT}/api/sources/upload" \
        -H "Authorization: Bearer $SRC_TOKEN" \
        -F "file=@${F014_PDF};type=application/pdf" \
        -F "collection_id=${F014_COLL_ID}" \
        -w '%{http_code}' -o "$F014_UP_BODY")
      rm -f "$F014_PDF"
      test "$F014_UP_STATUS" = "201" \
        || { echo "FAIL: F014-V1 upload $i returned $F014_UP_STATUS: $(cat "$F014_UP_BODY")"; rm -f "$F014_UP_BODY" "$PDF_FILE" /tmp/src_expected_sha256.txt; exit 1; }
      F014_SRC_ID=$(python3 -c "import json; print(json.load(open('$F014_UP_BODY'))['id'])")
      rm -f "$F014_UP_BODY"
      echo "  uploaded source $i: id=$F014_SRC_ID"
    done

    echo "--- sources F014-V1: GET /api/sources/collections/{id}/sources returns 200, total>=3 ---"
    F014_LIST_BODY=$(mktemp)
    F014_LIST_STATUS=$(curl -sS -X GET \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections/${F014_COLL_ID}/sources" \
      -H "Authorization: Bearer $SRC_TOKEN" \
      -w '%{http_code}' -o "$F014_LIST_BODY")
    test "$F014_LIST_STATUS" = "200" \
      || { echo "FAIL: F014-V1 GET returned $F014_LIST_STATUS: $(cat "$F014_LIST_BODY")"; rm -f "$F014_LIST_BODY" "$PDF_FILE" /tmp/src_expected_sha256.txt; exit 1; }
    python3 -c "
import json, sys
body = json.load(open('$F014_LIST_BODY'))
assert 'items' in body, f'missing items key: {body}'
assert 'total' in body, f'missing total key: {body}'
assert isinstance(body['total'], int), f'total not int: {body}'
assert body['total'] >= 3, f'expected total >= 3, got {body[\"total\"]}'
assert len(body['items']) >= 3, f'expected >= 3 items, got {len(body[\"items\"])}'
required = ['id', 'original_name', 'storage_uri', 'sha256', 'uploaded_at']
for item in body['items']:
    for field in required:
        assert field in item, f'item missing field {field}: {item}'
print('  F014-V1 OK: total =', body['total'], 'items =', len(body['items']))
" || { echo "FAIL: F014-V1 response shape incorrect"; rm -f "$F014_LIST_BODY" "$PDF_FILE" /tmp/src_expected_sha256.txt; exit 1; }
    rm -f "$F014_LIST_BODY"

    echo "--- sources F014-V2: GET on non-existent collection returns 404 ---"
    F014_NOTFOUND_STATUS=$(curl -sS -X GET \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections/999999/sources" \
      -H "Authorization: Bearer $SRC_TOKEN" \
      -o /dev/null -w '%{http_code}')
    test "$F014_NOTFOUND_STATUS" = "404" \
      || { echo "FAIL: F014-V2 returned $F014_NOTFOUND_STATUS (expected 404)"; rm -f "$PDF_FILE" /tmp/src_expected_sha256.txt; exit 1; }
    echo "  F014-V2 OK: /api/sources/collections/999999/sources -> 404"

    rm -f "$PDF_FILE" /tmp/src_expected_sha256.txt
    ;;
  operators)
    # F-015: seed-operators CLI — inserts MinerU extractor row into operator table.
    COMPOSE="docker/docker-compose.dev.yml"
    [[ -f "$COMPOSE" ]] || { echo "no $COMPOSE yet"; exit 0; }

    echo "--- operators V1: seed-operators creates exactly one mineru row ---"
    docker compose -f "$COMPOSE" exec -T fastapi \
      python -m dataplat_api.cli seed-operators

    # Criterion 1a: row exists with correct category, input_kind, output_kind.
    # The '|' in the grep pattern is a BRE *literal* character (not alternation) —
    # it matches the literal '|' separator produced by the SQL '||' concatenation
    # above.  Do NOT escape it as \| (that would make it alternation in ERE).
    docker compose -f "$COMPOSE" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "SELECT category || '|' || input_kind || '|' || output_kind
           FROM operator WHERE name='mineru' AND version='0.1.0'" \
      | grep -q '^extractor|source|document$' \
      || { echo "FAIL: operators V1 — row missing or wrong category/input_kind/output_kind"; exit 1; }
    echo "operators V1 row values: OK"

    # Criterion 1b: exactly 1 row with name='mineru'.
    docker compose -f "$COMPOSE" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "SELECT COUNT(*) FROM operator WHERE name='mineru'" \
      | grep -q '^1$' \
      || { echo "FAIL: operators V1 — expected exactly 1 mineru row"; exit 1; }
    echo "operators V1 row count: OK"

    echo "--- operators V2: config_schema is valid non-null JSONB ---"
    # Criterion 2: config_schema is non-null and parseable as JSONB.
    # The JSONB ->> operator extracts a top-level key as text; if config_schema
    # is NULL or not a valid JSON object, this returns NULL/empty and the grep fails.
    docker compose -f "$COMPOSE" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "SELECT config_schema->>'type' FROM operator WHERE name='mineru' AND version='0.1.0'" \
      | grep -q '^object$' \
      || { echo "FAIL: operators V2 — config_schema is null or not a JSON object with type=object"; exit 1; }
    echo "operators V2 config_schema valid JSONB: OK"

    # Idempotency: re-running must exit 0 and not create a second row.
    echo "--- operators V3: second run is idempotent (no duplicate row) ---"
    docker compose -f "$COMPOSE" exec -T fastapi \
      python -m dataplat_api.cli seed-operators
    docker compose -f "$COMPOSE" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "SELECT COUNT(*) FROM operator WHERE name='mineru'" \
      | grep -q '^1$' \
      || { echo "FAIL: operators V3 — second seed run created a duplicate row"; exit 1; }
    echo "operators V3 idempotency: OK"

    # F-016: GET /api/operators endpoint — list active operators by category.
    FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"

    echo "--- operators F016: mint Bearer token ---"
    OP_TOKEN_BODY=$(mktemp)
    OP_TOKEN_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
      -d "username=admin@example.com&password=testpassword123" \
      -H "Content-Type: application/x-www-form-urlencoded" \
      -w '%{http_code}' -o "$OP_TOKEN_BODY")
    test "$OP_TOKEN_STATUS" = "200" \
      || { echo "FAIL: operators F016 could not mint token (status $OP_TOKEN_STATUS) — run 'bash $0 auth' first"; rm -f "$OP_TOKEN_BODY"; exit 1; }
    OP_TOKEN=$(python3 -c "import json; print(json.load(open('$OP_TOKEN_BODY'))['access_token'])")
    rm -f "$OP_TOKEN_BODY"

    echo "--- operators F016-AUTH: no token → 401 ---"
    AUTH_STATUS=$(curl -sS -o /dev/null -w '%{http_code}' \
      "http://localhost:${FASTAPI_HOST_PORT}/api/operators")
    test "$AUTH_STATUS" = "401" \
      || { echo "FAIL: F016-AUTH returned $AUTH_STATUS (expected 401)"; exit 1; }
    echo "  F016-AUTH OK: no-token → 401"

    echo "--- operators F016-V1: category=extractor contains mineru ---"
    V1_BODY=$(mktemp)
    V1_STATUS=$(curl -sS -X GET \
      "http://localhost:${FASTAPI_HOST_PORT}/api/operators?category=extractor" \
      -H "Authorization: Bearer $OP_TOKEN" \
      -w '%{http_code}' -o "$V1_BODY")
    test "$V1_STATUS" = "200" \
      || { echo "FAIL: F016-V1 returned $V1_STATUS: $(cat "$V1_BODY")"; rm -f "$V1_BODY"; exit 1; }
    python3 -c "
import json, sys
body = json.load(open('$V1_BODY'))
assert isinstance(body, list), f'expected list, got {type(body).__name__}: {body}'
names = [op.get('name') for op in body]
assert 'mineru' in names, f'mineru not in operator names: {names}'
print('  F016-V1 OK: array len =', len(body), 'names =', names)
" || { echo "FAIL: F016-V1 assertion failed"; rm -f "$V1_BODY"; exit 1; }
    rm -f "$V1_BODY"

    echo "--- operators F016-V2: item shape — id, name, version, category, config_schema ---"
    V2_BODY=$(mktemp)
    V2_STATUS=$(curl -sS -X GET \
      "http://localhost:${FASTAPI_HOST_PORT}/api/operators?category=extractor" \
      -H "Authorization: Bearer $OP_TOKEN" \
      -w '%{http_code}' -o "$V2_BODY")
    test "$V2_STATUS" = "200" \
      || { echo "FAIL: F016-V2 returned $V2_STATUS: $(cat "$V2_BODY")"; rm -f "$V2_BODY"; exit 1; }
    python3 -c "
import json, sys
body = json.load(open('$V2_BODY'))
assert isinstance(body, list) and len(body) > 0, f'expected non-empty list: {body}'
required = ['id', 'name', 'version', 'category', 'config_schema']
for op in body:
    for field in required:
        assert field in op, f'item missing field {field!r}: {op}'
    assert isinstance(op['id'], int), f'id not int: {op}'
    assert op['category'] == 'extractor', f'category mismatch: {op}'
mineru = next((op for op in body if op['name'] == 'mineru'), None)
assert mineru is not None, f'mineru not found in {body}'
assert mineru['version'] == '0.1.0', f'version wrong: {mineru}'
assert isinstance(mineru['config_schema'], dict), f'config_schema not dict: {mineru}'
print('  F016-V2 OK: all items have required fields; mineru v0.1.0 config_schema is dict')
" || { echo "FAIL: F016-V2 shape assertion failed"; rm -f "$V2_BODY"; exit 1; }
    rm -f "$V2_BODY"

    echo "--- operators F016-V3: category=tagger returns 200 + array (empty ok) ---"
    # No tagger operator is seeded; the array will be empty. The check asserts
    # HTTP 200 + a JSON array where every item (vacuously) has category='tagger'.
    V3_BODY=$(mktemp)
    V3_STATUS=$(curl -sS -X GET \
      "http://localhost:${FASTAPI_HOST_PORT}/api/operators?category=tagger" \
      -H "Authorization: Bearer $OP_TOKEN" \
      -w '%{http_code}' -o "$V3_BODY")
    test "$V3_STATUS" = "200" \
      || { echo "FAIL: F016-V3 returned $V3_STATUS: $(cat "$V3_BODY")"; rm -f "$V3_BODY"; exit 1; }
    python3 -c "
import json, sys
body = json.load(open('$V3_BODY'))
assert isinstance(body, list), f'expected list, got {type(body).__name__}: {body}'
for op in body:
    assert op.get('category') == 'tagger', f'item category != tagger: {op}'
print('  F016-V3 OK: category=tagger -> 200 + list of len', len(body),
      '(empty is expected — no tagger seeded yet)')
" || { echo "FAIL: F016-V3 assertion failed"; rm -f "$V3_BODY"; exit 1; }
    rm -f "$V3_BODY"

    # F-017: GET /api/operators/{operator_id} — operator detail endpoint.
    # $OP_TOKEN and $FASTAPI_HOST_PORT are already in scope from the F016 block above.

    echo "--- operators F017: derive mineru id dynamically ---"
    # Fetch from the list endpoint rather than hardcoding id=1. Robust against
    # re-seeding or DB truncation between runs. No 2>&1: stderr stays on terminal
    # and is never captured into $MINERU_ID. The || guard catches python sys.exit(1).
    MINERU_ID_BODY=$(mktemp)
    curl -sS -X GET \
      "http://localhost:${FASTAPI_HOST_PORT}/api/operators?category=extractor" \
      -H "Authorization: Bearer $OP_TOKEN" \
      -o "$MINERU_ID_BODY"
    MINERU_ID=$(python3 -c "
import json, sys
body = json.load(open('$MINERU_ID_BODY'))
mineru = next((op for op in body if op['name'] == 'mineru'), None)
if mineru is None:
    sys.exit(1)
print(mineru['id'], end='')
") || { echo "FAIL: F017 could not derive mineru id from extractor list"; rm -f "$MINERU_ID_BODY"; exit 1; }
    rm -f "$MINERU_ID_BODY"
    echo "  F017 mineru id derived: $MINERU_ID"

    echo "--- operators F017-V1: GET /api/operators/${MINERU_ID} returns 200 + full fields ---"
    V1_DETAIL_BODY=$(mktemp)
    V1_DETAIL_STATUS=$(curl -sS -X GET \
      "http://localhost:${FASTAPI_HOST_PORT}/api/operators/${MINERU_ID}" \
      -H "Authorization: Bearer $OP_TOKEN" \
      -w '%{http_code}' -o "$V1_DETAIL_BODY")
    test "$V1_DETAIL_STATUS" = "200" \
      || { echo "FAIL: F017-V1 returned $V1_DETAIL_STATUS: $(cat "$V1_DETAIL_BODY")"; rm -f "$V1_DETAIL_BODY"; exit 1; }
    python3 -c "
import json, sys
body = json.load(open('$V1_DETAIL_BODY'))
required_base = ['id', 'name', 'version', 'category', 'input_kind', 'output_kind', 'image', 'is_active']
required_jsonb = ['config_schema', 'output_schema', 'default_config']
for field in required_base + required_jsonb:
    assert field in body, f'missing field {field!r}: {body}'
assert body['id'] == ${MINERU_ID}, f'id mismatch: {body}'
assert body['name'] == 'mineru', f'name mismatch: {body}'
assert body['version'] == '0.1.0', f'version wrong: {body}'
assert body['category'] == 'extractor', f'category wrong: {body}'
# config_schema: must be a dict with type=object (per seed).
assert isinstance(body['config_schema'], dict), f'config_schema not dict: {body[\"config_schema\"]}'
assert body['config_schema'].get('type') == 'object', f'config_schema type != object: {body[\"config_schema\"]}'
# output_schema: seed never sets it — key present but value is None.
assert 'output_schema' in body, f'output_schema key missing: {body}'
# default_config: server_default fires at INSERT; a fresh SELECT returns the stored
# value {}. Strict dict assertion — None is not accepted (agreed.md §5 OQ-2 DECIDED).
assert isinstance(body['default_config'], dict), f'default_config not a dict: {body[\"default_config\"]}'
print('  F017-V1 OK: id=%d name=%s config_schema.type=%s output_schema=%s default_config=%s' % (
  body['id'], body['name'], body['config_schema']['type'],
  type(body['output_schema']).__name__, body['default_config']))
" || { echo "FAIL: F017-V1 assertion failed"; rm -f "$V1_DETAIL_BODY"; exit 1; }
    rm -f "$V1_DETAIL_BODY"

    echo "--- operators F017-V2: GET /api/operators/99999 returns 404 ---"
    V2_NOTFOUND_STATUS=$(curl -sS -X GET \
      "http://localhost:${FASTAPI_HOST_PORT}/api/operators/99999" \
      -H "Authorization: Bearer $OP_TOKEN" \
      -o /dev/null -w '%{http_code}')
    test "$V2_NOTFOUND_STATUS" = "404" \
      || { echo "FAIL: F017-V2 returned $V2_NOTFOUND_STATUS (expected 404)"; exit 1; }
    echo "  F017-V2 OK: /api/operators/99999 → 404"
    ;;
  all)
    # smoke first: cheapest check, fails fast if stack is not up at all.
    # apps/api confirmed present since F-001 passes:true.
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
    bash "$0" operators   # F-015
    bash "$0" extract     # F-019
    bash "$0" documents   # F-020
    bash "$0" lance       # F-023
    bash "$0" chunks      # F-025
    ;;
  lance)
    # F-023: Lance global chunks table schema initialisation.
    COMPOSE="docker/docker-compose.dev.yml"
    [[ -f "$COMPOSE" ]] || { echo "no $COMPOSE yet"; exit 0; }

    MINIO_USER="${MINIO_ROOT_USER:-minioadmin}"
    MINIO_PASS="${MINIO_ROOT_PASSWORD:-devpassword}"

    echo "--- lance V1: get_or_create_chunks_table creates table with correct schema ---"
    # The fastapi container already has MINIO_ENDPOINT/ROOT_USER/ROOT_PASSWORD injected
    # by docker-compose.dev.yml; make_lance_storage_options() reads them via settings.
    docker compose -f "$COMPOSE" exec -T fastapi python -c "
from dataplat_api.storage.lance import get_or_create_chunks_table
dataset = get_or_create_chunks_table()
schema_names = dataset.schema.names
required = [
    'chunk_id', 'source_id', 'text', 'token_count',
    'attr_quality_score', 'attr_lang_code', 'attr_minhash_signature',
]
for field in required:
    assert field in schema_names, f'missing field {field!r}; schema has: {schema_names}'
assert len(schema_names) == 24, f'expected 24 fields, got {len(schema_names)}: {schema_names}'
print('  V1 OK: all required fields present; schema field count =', len(schema_names))
import sys; sys.exit(0)
" || { echo "FAIL: lance V1 schema check failed"; exit 1; }

    echo "--- lance V2: table path accessible via MinIO SDK ---"
    docker compose -f "$COMPOSE" exec -T \
      -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
      fastapi python -c "
import boto3, os, sys
s3 = boto3.client('s3', endpoint_url='http://minio:9000',
    aws_access_key_id=os.environ['S3_USER'],
    aws_secret_access_key=os.environ['S3_PASS'])
result = s3.list_objects_v2(Bucket='lance', Prefix='chunks/')
keys = [obj['Key'] for obj in result.get('Contents', [])]
if not keys:
    print('FAIL: no objects found at s3://lance/chunks/ — table was not written', file=sys.stderr)
    sys.exit(1)
print('  V2 OK: found', len(keys), 'objects at s3://lance/chunks/; first =', keys[0])
sys.exit(0)
" || { echo "FAIL: lance V2 MinIO SDK check failed"; exit 1; }
    ;;
  extract)
    # F-019: extract_mineru asset — PDF→DoclingDocument + document_variant row.
    # Requires: dagster image rebuilt with boto3/docling-core/pytest, and
    # MINIO_*/PLATFORM_DB_URL env injected into dagster-webserver/dagster-daemon.
    COMPOSE="docker/docker-compose.dev.yml"
    [[ -f "$COMPOSE" ]] || { echo "no $COMPOSE yet"; exit 0; }

    FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"
    # Mirror buckets) block exactly — block-local vars under set -euo pipefail.
    MINIO_USER="${MINIO_ROOT_USER:-minioadmin}"
    MINIO_PASS="${MINIO_ROOT_PASSWORD:-devpassword}"

    echo "--- extract: unit tests for extractor.py helpers (inside dagster-webserver) ---"
    # docling-core is only in the Dagster image, not in the apps/api venv.
    # The bind-mount makes /app/dagster/tests/test_extractor.py available without rebuild.
    docker compose -f "$COMPOSE" exec -T dagster-webserver \
      python -m pytest /app/dagster/tests/test_extractor.py -q \
      || { echo "FAIL: extractor unit tests failed"; exit 1; }
    echo "  extractor unit tests: OK"

    echo "--- extract: mint Bearer token ---"
    EX_TOKEN_BODY=$(mktemp)
    EX_TOKEN_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
      -d "username=admin@example.com&password=testpassword123" \
      -H "Content-Type: application/x-www-form-urlencoded" \
      -w '%{http_code}' -o "$EX_TOKEN_BODY")
    test "$EX_TOKEN_STATUS" = "200" \
      || { echo "FAIL: extract) could not mint token (status $EX_TOKEN_STATUS) — run 'bash $0 auth' first"; rm -f "$EX_TOKEN_BODY"; exit 1; }
    EX_TOKEN=$(python3 -c "import json; print(json.load(open('$EX_TOKEN_BODY'))['access_token'])")
    rm -f "$EX_TOKEN_BODY"

    echo "--- extract: upload source PDF ---"
    EX_PDF=$(mktemp /tmp/f019-XXXXXX.pdf)
    python3 -c "
pdf = (b'%PDF-1.4\n1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n'
       b'2 0 obj<</Type /Pages /Kids[3 0 R] /Count 1>>endobj\n'
       b'3 0 obj<</Type /Page /MediaBox[0 0 612 792] /Parent 2 0 R>>endobj\n'
       b'xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n'
       b'0000000058 00000 n \n0000000115 00000 n \n'
       b'trailer<</Size 4 /Root 1 0 R>>\nstartxref\n182\n%%EOF\n')
open('$EX_PDF', 'wb').write(pdf)
"
    EX_UP_BODY=$(mktemp)
    EX_UP_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/upload" \
      -H "Authorization: Bearer $EX_TOKEN" \
      -F "file=@${EX_PDF};type=application/pdf" \
      -w '%{http_code}' -o "$EX_UP_BODY")
    rm -f "$EX_PDF"
    test "$EX_UP_STATUS" = "201" \
      || { echo "FAIL: extract) upload returned $EX_UP_STATUS: $(cat "$EX_UP_BODY")"; rm -f "$EX_UP_BODY"; exit 1; }
    EX_SRC_ID=$(python3 -c "import json; print(json.load(open('$EX_UP_BODY'))['id'])")
    rm -f "$EX_UP_BODY"
    echo "  uploaded source id=$EX_SRC_ID"

    echo "--- extract: POST /api/runs extract_mineru ---"
    EX_RUN_BODY=$(mktemp)
    EX_RUN_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/runs" \
      -H "Authorization: Bearer $EX_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"asset\": \"extract_mineru\", \"source_ids\": [${EX_SRC_ID}]}" \
      -w '%{http_code}' -o "$EX_RUN_BODY")
    test "$EX_RUN_STATUS" = "202" \
      || { echo "FAIL: extract) POST /api/runs returned $EX_RUN_STATUS: $(cat "$EX_RUN_BODY")"; rm -f "$EX_RUN_BODY"; exit 1; }
    EX_BACKFILL_ID=$(python3 -c "import json; print(json.load(open('$EX_RUN_BODY'))['dagster_run_id'])")
    rm -f "$EX_RUN_BODY"
    echo "  backfill launched: id=$EX_BACKFILL_ID"

    echo "--- extract: poll backfill to COMPLETED_SUCCESS (≤120s) ---"
    EX_BF_STATUS="UNKNOWN"
    for i in $(seq 1 40); do
      EX_BF_STATUS=$(docker compose -f "$COMPOSE" exec -T dagster-webserver \
        python3 -c "
import urllib.request, json, sys
url = 'http://localhost:3000/graphql'
query = json.dumps({
    'query': '''query GetBackfill(\$id: String!) {
        partitionBackfillOrError(backfillId: \$id) {
            __typename
            ... on PartitionBackfill { status }
            ... on BackfillNotFoundError { message }
            ... on PythonError { message }
        }
    }''',
    'variables': {'id': '$EX_BACKFILL_ID'}
})
req = urllib.request.Request(url, data=query.encode(), headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req, timeout=5)
data = json.load(resp)
result = data['data']['partitionBackfillOrError']
print(result.get('status', result.get('message', 'UNKNOWN')))
" 2>&1 | tail -1)
      echo "  [$i/40] backfill status: $EX_BF_STATUS"
      case "$EX_BF_STATUS" in
        COMPLETED_SUCCESS) break ;;
        COMPLETED_FAILED|CANCELED|*FAIL*)
          echo "FAIL: backfill reached terminal non-success state: $EX_BF_STATUS"; exit 1 ;;
      esac
      sleep 3
    done
    test "$EX_BF_STATUS" = "COMPLETED_SUCCESS" \
      || { echo "FAIL: timeout waiting for backfill COMPLETED_SUCCESS (last status=$EX_BF_STATUS)"; exit 1; }
    echo "  backfill COMPLETED_SUCCESS"

    echo "--- extract: assert per-partition run reached SUCCESS ---"
    EX_RUN_CHECK=$(docker compose -f "$COMPOSE" exec -T dagster-webserver \
      python3 -c "
import urllib.request, json, sys
url = 'http://localhost:3000/graphql'
query = json.dumps({
    'query': '''query {
        runsOrError(filter: {tags: [{key: \"dagster/backfill\", value: \"$EX_BACKFILL_ID\"}]}) {
            __typename
            ... on Runs { results { runId status } }
            ... on PythonError { message }
        }
    }'''
})
req = urllib.request.Request(url, data=query.encode(), headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req, timeout=5)
data = json.load(resp)
runs = data['data']['runsOrError'].get('results', [])
success = [r for r in runs if r['status'] == 'SUCCESS']
if not success:
    print('FAIL: no per-partition run with status=SUCCESS. runs=%s' % runs, file=sys.stderr)
    sys.exit(1)
print('  per-partition run SUCCESS: runId=%s' % success[0]['runId'])
" 2>&1)
    echo "$EX_RUN_CHECK" | grep -q "FAIL" \
      && { echo "$EX_RUN_CHECK"; exit 1; } || echo "$EX_RUN_CHECK"

    echo "--- extract V1-proxy: document_variant row in Postgres ---"
    # Criterion 1 proxy: literal GET /api/sources/{id}/documents deferred to F-020.
    docker compose -f "$COMPOSE" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "SELECT extractor_name || '|' || extractor_version
         FROM document_variant WHERE source_id=${EX_SRC_ID} AND extractor_name='mineru'" \
      | grep -q '^mineru|0\.1\.0$' \
      || { echo "FAIL: V1-proxy — document_variant row missing or extractor_name/version wrong"; exit 1; }
    echo "  V1-proxy OK: document_variant row exists (extractor_name=mineru, version=0.1.0)"

    echo "--- extract V2: MinIO doc.docling.json exists + valid JSON + schema_name ---"
    docker compose -f "$COMPOSE" exec -T \
      -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
      -e SRC_ID="${EX_SRC_ID}" \
      fastapi python -c "
import boto3, os, json, sys
s3 = boto3.client('s3', endpoint_url='http://minio:9000',
    aws_access_key_id=os.environ['S3_USER'],
    aws_secret_access_key=os.environ['S3_PASS'])
src_id = os.environ['SRC_ID']
key = f'{src_id}/extract_mineru/doc.docling.json'
try:
    resp = s3.get_object(Bucket='documents', Key=key)
    body = resp['Body'].read()
    data = json.loads(body)
    assert data.get('schema_name') == 'DoclingDocument', \
        f'schema_name wrong: {data.get(\"schema_name\")}'
    print(f'  V2 OK: doc.docling.json exists at {key}, {len(body)} bytes, schema_name=DoclingDocument')
except Exception as e:
    print(f'FAIL: V2 — {e}', file=sys.stderr)
    sys.exit(1)
" || { echo "FAIL: V2 check failed"; exit 1; }

    echo "--- extract V3: is_canonical = TRUE ---"
    docker compose -f "$COMPOSE" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "SELECT is_canonical FROM document_variant
         WHERE source_id=${EX_SRC_ID} AND extractor_name='mineru'" \
      | grep -q '^t$' \
      || { echo "FAIL: V3 — is_canonical is not TRUE"; exit 1; }
    echo "  V3 OK: is_canonical=TRUE"

    echo "--- extract V4: dagster_run_id NOT NULL ---"
    # dagster_run_id is a UUID (e.g. 0a0ee6b7-1212-42bb-a0c7-37c70a7610d1)
    docker compose -f "$COMPOSE" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "SELECT dagster_run_id FROM document_variant
         WHERE source_id=${EX_SRC_ID} AND extractor_name='mineru'" \
      | grep -qE '^[0-9a-f-]{30,}$' \
      || { echo "FAIL: V4 — dagster_run_id is NULL or not a UUID"; exit 1; }
    echo "  V4 OK: dagster_run_id is non-null UUID"
    ;;
  documents)
    # F-020: GET /api/sources/{source_id}/documents — list document variants.
    # Self-contained: uploads a source, triggers extraction, waits for success,
    # then asserts V1 (200 + correct fields) and V2 (99999 → 404).
    COMPOSE="docker/docker-compose.dev.yml"
    [[ -f "$COMPOSE" ]] || { echo "no $COMPOSE yet"; exit 0; }

    FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"

    echo "--- documents: mint Bearer token ---"
    DOC_TOKEN_BODY=$(mktemp)
    DOC_TOKEN_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
      -d "username=admin@example.com&password=testpassword123" \
      -H "Content-Type: application/x-www-form-urlencoded" \
      -w '%{http_code}' -o "$DOC_TOKEN_BODY")
    test "$DOC_TOKEN_STATUS" = "200" \
      || { echo "FAIL: documents) could not mint token (status $DOC_TOKEN_STATUS) — run 'bash $0 auth' first"; rm -f "$DOC_TOKEN_BODY"; exit 1; }
    DOC_TOKEN=$(python3 -c "import json; print(json.load(open('$DOC_TOKEN_BODY'))['access_token'])")
    rm -f "$DOC_TOKEN_BODY"

    echo "--- documents: upload source PDF ---"
    DOC_PDF=$(mktemp /tmp/f020-XXXXXX.pdf)
    python3 -c "
pdf = (b'%PDF-1.4\n1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n'
       b'2 0 obj<</Type /Pages /Kids[3 0 R] /Count 1>>endobj\n'
       b'3 0 obj<</Type /Page /MediaBox[0 0 612 792] /Parent 2 0 R>>endobj\n'
       b'xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n'
       b'0000000058 00000 n \n0000000115 00000 n \n'
       b'trailer<</Size 4 /Root 1 0 R>>\nstartxref\n182\n%%EOF\nf020')
open('$DOC_PDF', 'wb').write(pdf)
"
    DOC_UP_BODY=$(mktemp)
    DOC_UP_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/upload" \
      -H "Authorization: Bearer $DOC_TOKEN" \
      -F "file=@${DOC_PDF};type=application/pdf" \
      -w '%{http_code}' -o "$DOC_UP_BODY")
    rm -f "$DOC_PDF"
    test "$DOC_UP_STATUS" = "201" \
      || { echo "FAIL: documents) upload returned $DOC_UP_STATUS: $(cat "$DOC_UP_BODY")"; rm -f "$DOC_UP_BODY"; exit 1; }
    DOC_SRC_ID=$(python3 -c "import json; print(json.load(open('$DOC_UP_BODY'))['id'])")
    rm -f "$DOC_UP_BODY"
    echo "  uploaded source id=$DOC_SRC_ID"

    echo "--- documents: trigger extract_mineru backfill ---"
    DOC_RUN_BODY=$(mktemp)
    DOC_RUN_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/runs" \
      -H "Authorization: Bearer $DOC_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"asset\": \"extract_mineru\", \"source_ids\": [${DOC_SRC_ID}]}" \
      -w '%{http_code}' -o "$DOC_RUN_BODY")
    test "$DOC_RUN_STATUS" = "202" \
      || { echo "FAIL: documents) POST /api/runs returned $DOC_RUN_STATUS: $(cat "$DOC_RUN_BODY")"; rm -f "$DOC_RUN_BODY"; exit 1; }
    DOC_BACKFILL_ID=$(python3 -c "import json; print(json.load(open('$DOC_RUN_BODY'))['dagster_run_id'])")
    rm -f "$DOC_RUN_BODY"
    echo "  backfill launched: id=$DOC_BACKFILL_ID"

    echo "--- documents: poll backfill to COMPLETED_SUCCESS (≤120s) ---"
    DOC_BF_STATUS="UNKNOWN"
    for i in $(seq 1 40); do
      DOC_BF_STATUS=$(docker compose -f "$COMPOSE" exec -T dagster-webserver \
        python3 -c "
import urllib.request, json, sys
url = 'http://localhost:3000/graphql'
query = json.dumps({
    'query': '''query GetBackfill(\$id: String!) {
        partitionBackfillOrError(backfillId: \$id) {
            __typename
            ... on PartitionBackfill { status }
            ... on BackfillNotFoundError { message }
            ... on PythonError { message }
        }
    }''',
    'variables': {'id': '$DOC_BACKFILL_ID'}
})
req = urllib.request.Request(url, data=query.encode(), headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req, timeout=5)
data = json.load(resp)
result = data['data']['partitionBackfillOrError']
print(result.get('status', result.get('message', 'UNKNOWN')))
" 2>&1 | tail -1)
      echo "  [$i/40] backfill status: $DOC_BF_STATUS"
      case "$DOC_BF_STATUS" in
        COMPLETED_SUCCESS) break ;;
        COMPLETED_FAILED|CANCELED|*FAIL*)
          echo "FAIL: backfill reached terminal non-success state: $DOC_BF_STATUS"; exit 1 ;;
      esac
      sleep 3
    done
    test "$DOC_BF_STATUS" = "COMPLETED_SUCCESS" \
      || { echo "FAIL: timeout waiting for backfill COMPLETED_SUCCESS (last status=$DOC_BF_STATUS)"; exit 1; }
    echo "  backfill COMPLETED_SUCCESS"

    echo "--- documents F020-V1: GET /api/sources/{id}/documents returns 200 + correct fields ---"
    V1_BODY=$(mktemp)
    V1_STATUS=$(curl -sS -X GET \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/${DOC_SRC_ID}/documents" \
      -H "Authorization: Bearer $DOC_TOKEN" \
      -w '%{http_code}' -o "$V1_BODY")
    test "$V1_STATUS" = "200" \
      || { echo "FAIL: F020-V1 returned $V1_STATUS: $(cat "$V1_BODY")"; rm -f "$V1_BODY"; exit 1; }
    python3 -c "
import json, sys
body = json.load(open('$V1_BODY'))
assert isinstance(body, list), f'expected list, got {type(body).__name__}: {body}'
assert len(body) >= 1, f'expected at least 1 variant, got {len(body)}: {body}'
item = body[0]
required = ['extractor_name', 'extractor_version', 'storage_prefix', 'is_canonical', 'materialized_at']
for field in required:
    assert field in item, f'item missing required field {field!r}: {item}'
assert item['extractor_name'] == 'mineru', f'extractor_name wrong: {item}'
assert isinstance(item['is_canonical'], bool), f'is_canonical not bool: {item}'
assert item['materialized_at'] is not None, f'materialized_at is None: {item}'
assert item['storage_prefix'], f'storage_prefix is empty: {item}'
print('  F020-V1 OK: array len=%d, extractor_name=%s, is_canonical=%s' % (
    len(body), item['extractor_name'], item['is_canonical']))
" || { echo "FAIL: F020-V1 assertion failed"; rm -f "$V1_BODY"; exit 1; }
    rm -f "$V1_BODY"

    echo "--- documents F020-V2: GET /api/sources/99999/documents returns 404 ---"
    V2_STATUS=$(curl -sS -X GET \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/99999/documents" \
      -H "Authorization: Bearer $DOC_TOKEN" \
      -o /dev/null -w '%{http_code}')
    test "$V2_STATUS" = "404" \
      || { echo "FAIL: F020-V2 returned $V2_STATUS (expected 404)"; exit 1; }
    echo "  F020-V2 OK: /api/sources/99999/documents → 404"

    # F-021: POST /api/sources/{source_id}/documents/{extractor_name}/set-canonical
    echo "--- documents F021-V1: POST set-canonical returns 200 with correct fields ---"
    SC_BODY=$(mktemp)
    SC_STATUS=$(curl -sS -X POST \
      -H "Authorization: Bearer $DOC_TOKEN" \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/${DOC_SRC_ID}/documents/mineru/set-canonical" \
      -w '%{http_code}' -o "$SC_BODY")
    test "$SC_STATUS" = "200" \
      || { echo "FAIL: F021-V1 returned $SC_STATUS: $(cat "$SC_BODY")"; rm -f "$SC_BODY"; exit 1; }
    python3 -c "
import json, sys
body = json.load(open('$SC_BODY'))
assert body.get('extractor_name') == 'mineru', f'extractor_name wrong: {body}'
assert body.get('is_canonical') is True, f'is_canonical not True: {body}'
print('  F021-V1 OK: extractor_name=%s is_canonical=%s' % (body['extractor_name'], body['is_canonical']))
" || { echo "FAIL: F021-V1 response assertion failed"; rm -f "$SC_BODY"; exit 1; }
    rm -f "$SC_BODY"

    echo "--- documents F021-V2: exactly 1 canonical row; extractor_name=mineru ---"
    CANON_COUNT=$(docker compose -f "$COMPOSE" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "SELECT COUNT(*) FROM document_variant
         WHERE source_id=${DOC_SRC_ID} AND is_canonical=TRUE" \
      | tr -d '[:space:]')
    test "$CANON_COUNT" = "1" \
      || { echo "FAIL: F021-V2 expected 1 canonical row, got '$CANON_COUNT'"; exit 1; }
    echo "  F021-V2 OK: canonical row count = $CANON_COUNT"

    CANON_NAME=$(docker compose -f "$COMPOSE" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "SELECT extractor_name FROM document_variant
         WHERE source_id=${DOC_SRC_ID} AND is_canonical=TRUE" \
      | tr -d '[:space:]')
    test "$CANON_NAME" = "mineru" \
      || { echo "FAIL: F021-V2 canonical extractor_name='$CANON_NAME' (expected 'mineru')"; exit 1; }
    echo "  F021-V2 OK: canonical extractor_name = $CANON_NAME"

    echo "--- documents F021-V3a: idx_doc_canonical index exists with is_canonical filter ---"
    IDX_DEF=$(docker compose -f "$COMPOSE" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "SELECT indexdef FROM pg_indexes
         WHERE tablename='document_variant' AND indexname='idx_doc_canonical'" \
      | tr -d '[:space:]')
    echo "$IDX_DEF" | grep -qi "is_canonical" \
      || { echo "FAIL: F021-V3a — idx_doc_canonical missing or lacks is_canonical filter: '$IDX_DEF'"; exit 1; }
    echo "  F021-V3a OK: idx_doc_canonical exists with is_canonical filter"

    echo "--- documents F021-V3b: unique index rejects a second TRUE row ---"
    # Insert a probe variant (idempotent via ON CONFLICT DO NOTHING).
    docker compose -f "$COMPOSE" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "INSERT INTO document_variant
           (source_id, extractor_name, extractor_version, config_hash, storage_prefix, is_canonical)
         VALUES
           (${DOC_SRC_ID}, 'probe', '0.0.1', 'aabbcc', 's3://documents/probe/', FALSE)
         ON CONFLICT (source_id, extractor_name, config_hash) DO NOTHING"
    # Attempt to make the probe row canonical — should ERROR because another
    # row for this source already has is_canonical=TRUE.
    V3B_OUT=$(docker compose -f "$COMPOSE" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -c \
        "UPDATE document_variant SET is_canonical=TRUE
         WHERE extractor_name='probe' AND source_id=${DOC_SRC_ID}" \
      2>&1 || true)
    echo "$V3B_OUT" | grep -qi "ERROR" \
      || { echo "FAIL: F021-V3b — unique constraint was NOT enforced: $V3B_OUT"; exit 1; }
    echo "  F021-V3b OK: unique constraint rejected second is_canonical=TRUE row"
    # Cleanup probe row.
    docker compose -f "$COMPOSE" exec -T postgres \
      psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
        "DELETE FROM document_variant WHERE extractor_name='probe' AND source_id=${DOC_SRC_ID}"
    echo "  F021-V3b cleanup OK: probe row deleted"
    ;;
  chunks)
    # F-025: chunks asset — fixed-size token chunking into Lance table.
    # Requires: dagster image rebuilt with lancedb/pyarrow/tiktoken,
    # MINIO_LANCE_BUCKET env injected into all dagster services,
    # and the Lance chunks table already initialised (lance) layer).
    COMPOSE="docker/docker-compose.dev.yml"
    [[ -f "$COMPOSE" ]] || { echo "no $COMPOSE yet"; exit 0; }

    FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"
    MINIO_USER="${MINIO_ROOT_USER:-minioadmin}"
    MINIO_PASS="${MINIO_ROOT_PASSWORD:-devpassword}"

    echo "--- chunks: unit tests for chunker.py helpers (inside dagster-webserver) ---"
    # tiktoken and lancedb are only in the Dagster image, not in the apps/api venv.
    # The bind-mount makes /app/dagster/tests/test_chunker.py available without rebuild.
    docker compose -f "$COMPOSE" exec -T dagster-webserver \
      python -m pytest /app/dagster/tests/test_chunker.py -q \
      || { echo "FAIL: chunker unit tests failed"; exit 1; }
    echo "  chunker unit tests: OK"

    echo "--- chunks: mint Bearer token ---"
    CH_TOKEN_BODY=$(mktemp)
    CH_TOKEN_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
      -d "username=admin@example.com&password=testpassword123" \
      -H "Content-Type: application/x-www-form-urlencoded" \
      -w '%{http_code}' -o "$CH_TOKEN_BODY")
    test "$CH_TOKEN_STATUS" = "200" \
      || { echo "FAIL: chunks) could not mint token (status $CH_TOKEN_STATUS) — run 'bash $0 auth' first"; rm -f "$CH_TOKEN_BODY"; exit 1; }
    CH_TOKEN=$(python3 -c "import json; print(json.load(open('$CH_TOKEN_BODY'))['access_token'])")
    rm -f "$CH_TOKEN_BODY"

    echo "--- chunks: create collection (source_collection_id required for Lance row) ---"
    CH_COLL_BODY=$(mktemp)
    CH_COLL_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections" \
      -H "Authorization: Bearer $CH_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"name": "test-chunks-f025", "dataset_card_md": "F025 chunks test collection"}' \
      -w '%{http_code}' -o "$CH_COLL_BODY")
    # Allow 409 (already exists from a previous run) — read the id either way.
    if test "$CH_COLL_STATUS" = "409"; then
      CH_COLL_ID=$(docker compose -f "$COMPOSE" exec -T postgres \
        psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
          "SELECT id FROM source_collection WHERE name='test-chunks-f025' LIMIT 1" \
        | tr -d '[:space:]')
    else
      test "$CH_COLL_STATUS" = "201" \
        || { echo "FAIL: chunks) collection create returned $CH_COLL_STATUS: $(cat "$CH_COLL_BODY")"; rm -f "$CH_COLL_BODY"; exit 1; }
      CH_COLL_ID=$(python3 -c "import json; print(json.load(open('$CH_COLL_BODY'))['id'])")
    fi
    rm -f "$CH_COLL_BODY"
    echo "  collection id=$CH_COLL_ID"

    echo "--- chunks: upload source PDF (with collection_id=$CH_COLL_ID) ---"
    CH_PDF=$(mktemp /tmp/f025-XXXXXX.pdf)
    python3 -c "
pdf = (b'%PDF-1.4\n1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n'
       b'2 0 obj<</Type /Pages /Kids[3 0 R] /Count 1>>endobj\n'
       b'3 0 obj<</Type /Page /MediaBox[0 0 612 792] /Parent 2 0 R>>endobj\n'
       b'xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n'
       b'0000000058 00000 n \n0000000115 00000 n \n'
       b'trailer<</Size 4 /Root 1 0 R>>\nstartxref\n182\n%%EOF\n')
open('$CH_PDF', 'wb').write(pdf)
"
    CH_UP_BODY=$(mktemp)
    CH_UP_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/sources/upload" \
      -H "Authorization: Bearer $CH_TOKEN" \
      -F "file=@${CH_PDF};type=application/pdf" \
      -F "collection_id=${CH_COLL_ID}" \
      -w '%{http_code}' -o "$CH_UP_BODY")
    rm -f "$CH_PDF"
    test "$CH_UP_STATUS" = "201" \
      || { echo "FAIL: chunks) upload returned $CH_UP_STATUS: $(cat "$CH_UP_BODY")"; rm -f "$CH_UP_BODY"; exit 1; }
    CH_SRC_ID=$(python3 -c "import json; print(json.load(open('$CH_UP_BODY'))['id'])")
    rm -f "$CH_UP_BODY"
    echo "  uploaded source id=$CH_SRC_ID"

    echo "--- chunks: POST /api/runs extract_mineru (prerequisite — no graph dep per D9) ---"
    CH_EX_RUN_BODY=$(mktemp)
    CH_EX_RUN_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/runs" \
      -H "Authorization: Bearer $CH_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"asset\": \"extract_mineru\", \"source_ids\": [${CH_SRC_ID}]}" \
      -w '%{http_code}' -o "$CH_EX_RUN_BODY")
    test "$CH_EX_RUN_STATUS" = "202" \
      || { echo "FAIL: chunks) POST /api/runs extract_mineru returned $CH_EX_RUN_STATUS: $(cat "$CH_EX_RUN_BODY")"; rm -f "$CH_EX_RUN_BODY"; exit 1; }
    CH_EX_BACKFILL_ID=$(python3 -c "import json; print(json.load(open('$CH_EX_RUN_BODY'))['dagster_run_id'])")
    rm -f "$CH_EX_RUN_BODY"
    echo "  extract_mineru backfill launched: id=$CH_EX_BACKFILL_ID"

    echo "--- chunks: poll extract_mineru backfill to COMPLETED_SUCCESS (<=120s) ---"
    CH_EX_BF_STATUS="UNKNOWN"
    for i in $(seq 1 40); do
      CH_EX_BF_STATUS=$(docker compose -f "$COMPOSE" exec -T dagster-webserver \
        python3 -c "
import urllib.request, json, sys
url = 'http://localhost:3000/graphql'
query = json.dumps({
    'query': '''query GetBackfill(\$id: String!) {
        partitionBackfillOrError(backfillId: \$id) {
            __typename
            ... on PartitionBackfill { status }
            ... on BackfillNotFoundError { message }
            ... on PythonError { message }
        }
    }''',
    'variables': {'id': '$CH_EX_BACKFILL_ID'}
})
req = urllib.request.Request(url, data=query.encode(), headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req, timeout=5)
data = json.load(resp)
result = data['data']['partitionBackfillOrError']
print(result.get('status', result.get('message', 'UNKNOWN')))
" 2>&1 | tail -1)
      echo "  [$i/40] extract_mineru backfill status: $CH_EX_BF_STATUS"
      case "$CH_EX_BF_STATUS" in
        COMPLETED_SUCCESS) break ;;
        COMPLETED_FAILED|CANCELED|*FAIL*)
          echo "FAIL: extract_mineru backfill reached terminal non-success state: $CH_EX_BF_STATUS"; exit 1 ;;
      esac
      sleep 3
    done
    test "$CH_EX_BF_STATUS" = "COMPLETED_SUCCESS" \
      || { echo "FAIL: timeout waiting for extract_mineru COMPLETED_SUCCESS (last status=$CH_EX_BF_STATUS)"; exit 1; }
    echo "  extract_mineru COMPLETED_SUCCESS"

    echo "--- chunks: POST /api/runs chunks ---"
    CH_RUN_BODY=$(mktemp)
    CH_RUN_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/runs" \
      -H "Authorization: Bearer $CH_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"asset\": \"chunks\", \"source_ids\": [${CH_SRC_ID}]}" \
      -w '%{http_code}' -o "$CH_RUN_BODY")
    test "$CH_RUN_STATUS" = "202" \
      || { echo "FAIL: chunks) POST /api/runs chunks returned $CH_RUN_STATUS: $(cat "$CH_RUN_BODY")"; rm -f "$CH_RUN_BODY"; exit 1; }
    CH_BACKFILL_ID=$(python3 -c "import json; print(json.load(open('$CH_RUN_BODY'))['dagster_run_id'])")
    rm -f "$CH_RUN_BODY"
    echo "  chunks backfill launched: id=$CH_BACKFILL_ID"

    echo "--- chunks: poll chunks backfill to COMPLETED_SUCCESS (<=120s) ---"
    CH_BF_STATUS="UNKNOWN"
    for i in $(seq 1 40); do
      CH_BF_STATUS=$(docker compose -f "$COMPOSE" exec -T dagster-webserver \
        python3 -c "
import urllib.request, json, sys
url = 'http://localhost:3000/graphql'
query = json.dumps({
    'query': '''query GetBackfill(\$id: String!) {
        partitionBackfillOrError(backfillId: \$id) {
            __typename
            ... on PartitionBackfill { status }
            ... on BackfillNotFoundError { message }
            ... on PythonError { message }
        }
    }''',
    'variables': {'id': '$CH_BACKFILL_ID'}
})
req = urllib.request.Request(url, data=query.encode(), headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req, timeout=5)
data = json.load(resp)
result = data['data']['partitionBackfillOrError']
print(result.get('status', result.get('message', 'UNKNOWN')))
" 2>&1 | tail -1)
      echo "  [$i/40] chunks backfill status: $CH_BF_STATUS"
      case "$CH_BF_STATUS" in
        COMPLETED_SUCCESS) break ;;
        COMPLETED_FAILED|CANCELED|*FAIL*)
          echo "FAIL: chunks backfill reached terminal non-success state: $CH_BF_STATUS"; exit 1 ;;
      esac
      sleep 3
    done
    test "$CH_BF_STATUS" = "COMPLETED_SUCCESS" \
      || { echo "FAIL: timeout waiting for chunks COMPLETED_SUCCESS (last status=$CH_BF_STATUS)"; exit 1; }
    echo "  chunks COMPLETED_SUCCESS"

    echo "--- chunks V1: Lance row count > 0 for source ---"
    docker compose -f "$COMPOSE" exec -T \
      -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
      -e SRC_ID="${CH_SRC_ID}" \
      fastapi python -c "
import lancedb, os, sys
src_id = int(os.environ['SRC_ID'])
db = lancedb.connect(
    's3://lance/chunks',
    storage_options={
        'aws_access_key_id': os.environ['S3_USER'],
        'aws_secret_access_key': os.environ['S3_PASS'],
        'endpoint': 'http://minio:9000',
        'aws_region': 'us-east-1',
        'allow_http': 'true',
    })
t = db.open_table('chunks')
n = t.count_rows(f'source_id = {src_id} AND producer_asset = \'chunks\'')
assert n > 0, f'expected >0 rows for source_id={src_id}, got {n}'
print(f'  V1 OK: {n} chunk rows written for source_id={src_id}')
sys.exit(0)
" || { echo "FAIL: chunks V1 row count check failed"; exit 1; }

    echo "--- chunks V2: chunk_id matches {source_id}_{seq} pattern ---"
    docker compose -f "$COMPOSE" exec -T \
      -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
      -e SRC_ID="${CH_SRC_ID}" \
      fastapi python -c "
import lancedb, os, sys, re
src_id = int(os.environ['SRC_ID'])
db = lancedb.connect(
    's3://lance/chunks',
    storage_options={
        'aws_access_key_id': os.environ['S3_USER'],
        'aws_secret_access_key': os.environ['S3_PASS'],
        'endpoint': 'http://minio:9000',
        'aws_region': 'us-east-1',
        'allow_http': 'true',
    })
t = db.open_table('chunks')
rows = t.search().where(f'source_id = {src_id} AND producer_asset = \'chunks\'').select(['chunk_id']).to_list()
assert rows, f'no rows returned for source_id={src_id}'
pattern = re.compile(rf'^{src_id}_\d+$')
for row in rows:
    cid = row['chunk_id']
    assert pattern.fullmatch(cid), f'chunk_id {cid!r} does not match pattern'
seqs = sorted(int(r['chunk_id'].split('_')[1]) for r in rows)
assert seqs == list(range(len(seqs))), f'chunk_id sequences not contiguous: {seqs}'
print(f'  V2 OK: all {len(rows)} chunk_ids match pattern, seq 0..{len(rows)-1}')
sys.exit(0)
" || { echo "FAIL: chunks V2 chunk_id pattern check failed"; exit 1; }

    echo "--- chunks V3: text non-null, token_count > 0 and <= 512 ---"
    docker compose -f "$COMPOSE" exec -T \
      -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
      -e SRC_ID="${CH_SRC_ID}" \
      fastapi python -c "
import lancedb, os, sys
src_id = int(os.environ['SRC_ID'])
db = lancedb.connect(
    's3://lance/chunks',
    storage_options={
        'aws_access_key_id': os.environ['S3_USER'],
        'aws_secret_access_key': os.environ['S3_PASS'],
        'endpoint': 'http://minio:9000',
        'aws_region': 'us-east-1',
        'allow_http': 'true',
    })
t = db.open_table('chunks')
rows = t.search().where(f'source_id = {src_id} AND producer_asset = \'chunks\'').select(['text', 'token_count']).to_list()
assert rows, f'no rows for source_id={src_id}'
for row in rows:
    assert row['text'], f'text is empty/null for a chunk'
    tc = row['token_count']
    assert tc > 0, f'token_count={tc} is not > 0'
    assert tc <= 512, f'token_count={tc} > 512 (window boundary exceeded)'
print(f'  V3 OK: {len(rows)} rows all have non-empty text and 0 < token_count <= 512')
sys.exit(0)
" || { echo "FAIL: chunks V3 text/token_count check failed"; exit 1; }

    echo "--- chunks V4: augmented_from null, attr_* null, producer_asset='chunks' ---"
    docker compose -f "$COMPOSE" exec -T \
      -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
      -e SRC_ID="${CH_SRC_ID}" \
      fastapi python -c "
import lancedb, os, sys
src_id = int(os.environ['SRC_ID'])
db = lancedb.connect(
    's3://lance/chunks',
    storage_options={
        'aws_access_key_id': os.environ['S3_USER'],
        'aws_secret_access_key': os.environ['S3_PASS'],
        'endpoint': 'http://minio:9000',
        'aws_region': 'us-east-1',
        'allow_http': 'true',
    })
t = db.open_table('chunks')
rows = t.search().where(f'source_id = {src_id} AND producer_asset = \'chunks\'') \
    .select(['producer_asset', 'augmented_from', 'attr_quality_score', 'attr_lang_code', 'attr_embed_vector']) \
    .to_list()
assert rows, f'no rows for source_id={src_id}'
for row in rows:
    assert row['producer_asset'] == 'chunks', f\"producer_asset={row['producer_asset']!r} expected 'chunks'\"
    assert row['augmented_from'] is None, f\"augmented_from={row['augmented_from']!r} expected None\"
    assert row['attr_quality_score'] is None, f'attr_quality_score should be None'
    assert row['attr_lang_code'] is None, f'attr_lang_code should be None'
    assert row['attr_embed_vector'] is None, f'attr_embed_vector should be None'
print(f'  V4 OK: {len(rows)} rows have producer_asset=chunks, augmented_from=None, attr_*=None')
sys.exit(0)
" || { echo "FAIL: chunks V4 metadata check failed"; exit 1; }

    echo "--- chunks V5: idempotency — re-run does not double row count ---"
    # C2 fix: dedicated count-only Python snippet (prints integer, not human-readable string).
    CH_COUNT1=$(docker compose -f "$COMPOSE" exec -T \
      -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
      -e SRC_ID="${CH_SRC_ID}" \
      fastapi python -c "
import lancedb, os
src_id = int(os.environ['SRC_ID'])
db = lancedb.connect(
    's3://lance/chunks',
    storage_options={
        'aws_access_key_id': os.environ['S3_USER'],
        'aws_secret_access_key': os.environ['S3_PASS'],
        'endpoint': 'http://minio:9000',
        'aws_region': 'us-east-1',
        'allow_http': 'true',
    })
t = db.open_table('chunks')
n = t.count_rows(f'source_id = {src_id} AND producer_asset = \'chunks\'')
print(n)
" 2>/dev/null)
    echo "  V5: row count after first run = $CH_COUNT1"

    # Trigger a second backfill of the same chunks partition.
    CH_RUN2_BODY=$(mktemp)
    CH_RUN2_STATUS=$(curl -sS -X POST \
      "http://localhost:${FASTAPI_HOST_PORT}/api/runs" \
      -H "Authorization: Bearer $CH_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"asset\": \"chunks\", \"source_ids\": [${CH_SRC_ID}]}" \
      -w '%{http_code}' -o "$CH_RUN2_BODY")
    test "$CH_RUN2_STATUS" = "202" \
      || { echo "FAIL: chunks V5 second backfill returned $CH_RUN2_STATUS: $(cat "$CH_RUN2_BODY")"; rm -f "$CH_RUN2_BODY"; exit 1; }
    CH_BACKFILL2_ID=$(python3 -c "import json; print(json.load(open('$CH_RUN2_BODY'))['dagster_run_id'])")
    rm -f "$CH_RUN2_BODY"
    echo "  V5: second chunks backfill launched: id=$CH_BACKFILL2_ID"

    echo "--- chunks V5: poll second chunks backfill to COMPLETED_SUCCESS (<=120s) ---"
    CH_BF2_STATUS="UNKNOWN"
    for i in $(seq 1 40); do
      CH_BF2_STATUS=$(docker compose -f "$COMPOSE" exec -T dagster-webserver \
        python3 -c "
import urllib.request, json, sys
url = 'http://localhost:3000/graphql'
query = json.dumps({
    'query': '''query GetBackfill(\$id: String!) {
        partitionBackfillOrError(backfillId: \$id) {
            __typename
            ... on PartitionBackfill { status }
            ... on BackfillNotFoundError { message }
            ... on PythonError { message }
        }
    }''',
    'variables': {'id': '$CH_BACKFILL2_ID'}
})
req = urllib.request.Request(url, data=query.encode(), headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req, timeout=5)
data = json.load(resp)
result = data['data']['partitionBackfillOrError']
print(result.get('status', result.get('message', 'UNKNOWN')))
" 2>&1 | tail -1)
      echo "  [$i/40] second chunks backfill status: $CH_BF2_STATUS"
      case "$CH_BF2_STATUS" in
        COMPLETED_SUCCESS) break ;;
        COMPLETED_FAILED|CANCELED|*FAIL*)
          echo "FAIL: second chunks backfill reached terminal non-success state: $CH_BF2_STATUS"; exit 1 ;;
      esac
      sleep 3
    done
    test "$CH_BF2_STATUS" = "COMPLETED_SUCCESS" \
      || { echo "FAIL: timeout waiting for second chunks COMPLETED_SUCCESS (last status=$CH_BF2_STATUS)"; exit 1; }

    # Capture count after second run (same dedicated snippet).
    CH_COUNT2=$(docker compose -f "$COMPOSE" exec -T \
      -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
      -e SRC_ID="${CH_SRC_ID}" \
      fastapi python -c "
import lancedb, os
src_id = int(os.environ['SRC_ID'])
db = lancedb.connect(
    's3://lance/chunks',
    storage_options={
        'aws_access_key_id': os.environ['S3_USER'],
        'aws_secret_access_key': os.environ['S3_PASS'],
        'endpoint': 'http://minio:9000',
        'aws_region': 'us-east-1',
        'allow_http': 'true',
    })
t = db.open_table('chunks')
n = t.count_rows(f'source_id = {src_id} AND producer_asset = \'chunks\'')
print(n)
" 2>/dev/null)
    echo "  V5: row count after second run = $CH_COUNT2"
    [ "$CH_COUNT2" -eq "$CH_COUNT1" ] \
      || { echo "FAIL V5: row count changed from $CH_COUNT1 to $CH_COUNT2 (expected no change — idempotency broken)"; exit 1; }
    echo "  V5 OK: idempotent row count $CH_COUNT1 == $CH_COUNT2"

    echo "--- chunks V6: no duplicate chunk_ids for source ---"
    docker compose -f "$COMPOSE" exec -T \
      -e S3_USER="${MINIO_USER}" -e S3_PASS="${MINIO_PASS}" \
      -e SRC_ID="${CH_SRC_ID}" \
      fastapi python -c "
import lancedb, os, sys
src_id = int(os.environ['SRC_ID'])
db = lancedb.connect(
    's3://lance/chunks',
    storage_options={
        'aws_access_key_id': os.environ['S3_USER'],
        'aws_secret_access_key': os.environ['S3_PASS'],
        'endpoint': 'http://minio:9000',
        'aws_region': 'us-east-1',
        'allow_http': 'true',
    })
t = db.open_table('chunks')
rows = t.search().where(f'source_id = {src_id} AND producer_asset = \'chunks\'') \
    .select(['chunk_id']).to_list()
ids = [r['chunk_id'] for r in rows]
assert len(ids) == len(set(ids)), f'duplicate chunk_ids: {len(ids)} rows, {len(set(ids))} unique'
print(f'  V6 OK: {len(ids)} chunk_ids, all unique')
sys.exit(0)
" || { echo "FAIL V6: duplicate chunk_ids detected"; exit 1; }
    ;;
  *)
    echo "Unknown layer: $LAYER" >&2
    exit 2
    ;;
esac

echo "✓ $LAYER passed"
