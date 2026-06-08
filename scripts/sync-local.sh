#!/usr/bin/env bash
# Sync local CRM code into the docker container, rebuild, and restart.
# Use after any pull / code change on Mac when the container's apps/crm
# directory is not volume-mounted to the repo.
#
# Usage:
#   ./scripts/sync-local.sh             # sync everything (default)
#   ./scripts/sync-local.sh backend     # backend only (Python + fixtures, no rebuild)
#   ./scripts/sync-local.sh frontend    # frontend only (Vue + rebuild)
#   ./scripts/sync-local.sh migrate     # migrate only (assumes code is in sync)

set -euo pipefail

CONTAINER="crm-frappe-1"
SITE="crm.localhost"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER_APP="/home/frappe/frappe-bench/apps/crm"
BENCH_DIR="/home/frappe/frappe-bench"

MODE="${1:-all}"

step() { printf "\n\033[1;34m▶ %s\033[0m\n" "$1"; }
ok()   { printf "\033[1;32m✓ %s\033[0m\n" "$1"; }

sync_backend() {
  step "Syncing backend (crm/) → container"
  docker cp "$REPO_ROOT/crm/." "$CONTAINER:$CONTAINER_APP/crm/"
  docker exec -u root "$CONTAINER" chown -R frappe:frappe "$CONTAINER_APP/crm"
  ok "Backend synced"
}

sync_frontend() {
  step "Syncing frontend (frontend/) → container"
  docker cp "$REPO_ROOT/frontend/." "$CONTAINER:$CONTAINER_APP/frontend/"
  docker exec -u root "$CONTAINER" chown -R frappe:frappe "$CONTAINER_APP/frontend"
  ok "Frontend synced"
}

build_frontend() {
  step "Building frontend (yarn build)"
  docker exec -w "$CONTAINER_APP/frontend" "$CONTAINER" yarn build
  ok "Frontend built"
}

migrate() {
  step "Running migrate"
  docker exec -w "$BENCH_DIR" "$CONTAINER" bench --site "$SITE" migrate
  ok "Migrate done"
}

clear_cache() {
  step "Clearing caches"
  docker exec -w "$BENCH_DIR" "$CONTAINER" bench --site "$SITE" clear-cache
  docker exec -w "$BENCH_DIR" "$CONTAINER" bench --site "$SITE" clear-website-cache
  ok "Caches cleared"
}

restart_bench() {
  step "Restarting bench"
  docker exec -w "$BENCH_DIR" "$CONTAINER" bench restart || true
  ok "Bench restarted"
}

case "$MODE" in
  backend)
    sync_backend
    migrate
    clear_cache
    restart_bench
    ;;
  frontend)
    sync_frontend
    build_frontend
    clear_cache
    ;;
  migrate)
    migrate
    clear_cache
    restart_bench
    ;;
  all|"")
    sync_backend
    sync_frontend
    build_frontend
    migrate
    clear_cache
    restart_bench
    ;;
  *)
    echo "Unknown mode: $MODE"
    echo "Usage: $0 [all|backend|frontend|migrate]"
    exit 1
    ;;
esac

printf "\n\033[1;32m✅ Done. Hard-refresh browser: Cmd+Shift+R\033[0m\n"
