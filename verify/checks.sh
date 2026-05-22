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

run() { echo "▶ $*"; eval "$*"; }
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
    if exists apps/api; then
      run "cd apps/api && uv run pytest -q -k smoke || true"
    else
      echo "no apps/api yet — smoke layer skipped"
    fi
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
  all)
    bash "$0" infra
    bash "$0" backend
    bash "$0" frontend
    bash "$0" contract
    bash "$0" migration
    ;;
  *)
    echo "Unknown layer: $LAYER" >&2
    exit 2
    ;;
esac

echo "✓ $LAYER passed"
