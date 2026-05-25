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

    rm -f "$PDF_FILE" /tmp/src_expected_sha256.txt
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
    ;;
  *)
    echo "Unknown layer: $LAYER" >&2
    exit 2
    ;;
esac

echo "✓ $LAYER passed"
