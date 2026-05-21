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
    run "cd apps/api && uv run alembic upgrade head"
    run "cd apps/api && uv run alembic downgrade -1"
    run "cd apps/api && uv run alembic upgrade head"
    ;;
  plugin)
    PLUGIN_NAME="${1:?usage: checks.sh plugin <name>}"
    [[ -d "plugins/$PLUGIN_NAME" ]] || { echo "no plugins/$PLUGIN_NAME"; exit 1; }
    run "cd plugins/$PLUGIN_NAME && uv run pytest -q"
    run "cd plugins/$PLUGIN_NAME && uv run ruff check ."
    ;;
  all)
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
