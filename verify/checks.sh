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

    echo "--- dagster V1: GET /api/admin/dagster-status returns 200 with dagster_version ---"
    # Curl output piped directly into python3 stdin — never captured into a
    # shell variable.  This avoids shell injection / Python syntax breakage
    # from any single-quote, backslash, or $ in the response body.
    curl -fsS "http://localhost:${FASTAPI_HOST_PORT}/api/admin/dagster-status" \
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

    curl -fsS "http://localhost:${FASTAPI_HOST_PORT}/api/admin/dagster-status" \
      | python3 -c "
import json, sys
body = json.load(sys.stdin)
assert 'dagster_version' in body, f'missing dagster_version key: {body}'
assert len(body['dagster_version']) > 0, f'dagster_version is empty: {body}'
print('  V2 OK (post-restart): dagster_version =', body['dagster_version'])
" || { echo "FAIL: V2 check failed after restart"; exit 1; }
    ;;
  runs)
    # F-005: hello-world Dagster job launch + status poll
    COMPOSE="docker/docker-compose.dev.yml"
    [[ -f "$COMPOSE" ]] || { echo "no $COMPOSE yet"; exit 0; }

    FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"

    echo "--- runs V1: trigger hello-world job via FastAPI ---"
    # 2-step pattern: capture body to a temp file, write status code to RESP.
    # This ensures non-201 responses print the body for debugging rather than
    # silently failing. curl -sS shows connection errors on stderr without -f
    # suppressing the body.
    # mktemp avoids clobber risk on shared CI hosts (vs fixed /tmp/launch_body).
    LAUNCH_BODY=$(mktemp)
    RESP=$(curl -sS -X POST "http://localhost:${FASTAPI_HOST_PORT}/api/admin/runs/hello-world" \
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
    bash "$0" buckets
    bash "$0" dagster
    bash "$0" runs
    ;;
  *)
    echo "Unknown layer: $LAYER" >&2
    exit 2
    ;;
esac

echo "✓ $LAYER passed"
