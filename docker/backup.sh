#!/usr/bin/env bash
#
# Full offsite backup for CRM prod (Frappe in Docker).
#   DB + files  ->  pulled to host  ->  pushed to S3 via rclone  ->  pruned.
#
# Run on the DOCKER HOST (where the `crm` compose stack runs), not inside a container.
# Schedule via cron (Linux) or launchd (macOS). See BACKUP.md.
#
# Config: override any value via environment variable or edit the defaults below.

set -euo pipefail

# ---- config -----------------------------------------------------------------
COMPOSE_PROJECT="${COMPOSE_PROJECT:-crm}"          # docker compose -p name
FRAPPE_SERVICE="${FRAPPE_SERVICE:-frappe}"         # compose service name
SITE="${SITE:-crm.localhost}"                      # Frappe site
BENCH_DIR="${BENCH_DIR:-frappe-bench}"             # bench dir inside container ($HOME/<this>)
HOST_BACKUP_ROOT="${HOST_BACKUP_ROOT:-/var/backups/crm}"
RCLONE_REMOTE="${RCLONE_REMOTE:-s3crm:greenfortune-crm-backups}"  # rclone remote:bucket/prefix
LOCAL_RETENTION_DAYS="${LOCAL_RETENTION_DAYS:-7}"  # prune host copies older than N days
RCLONE_EXTRA="${RCLONE_EXTRA:---s3-storage-class STANDARD_IA}"
# Optional encryption: set GPG_RECIPIENT to a key id/email to encrypt the DB dump before upload.
GPG_RECIPIENT="${GPG_RECIPIENT:-}"
# Optional failure alert: a Slack/webhook URL that receives a POST on failure.
ALERT_WEBHOOK="${ALERT_WEBHOOK:-}"
# -----------------------------------------------------------------------------

STAMP="$(date +%F_%H%M%S)"
WORKDIR="${HOST_BACKUP_ROOT}/${STAMP}"
LOG_PREFIX="[crm-backup ${STAMP}]"

log() { echo "${LOG_PREFIX} $*"; }

# Resolve the running container name from the compose project + service.
resolve_container() {
  local c
  c="$(docker compose -p "${COMPOSE_PROJECT}" ps -q "${FRAPPE_SERVICE}" 2>/dev/null || true)"
  if [ -z "${c}" ]; then
    # fallback: conventional name crm-frappe-1 / crm_frappe_1
    c="$(docker ps --filter "name=${COMPOSE_PROJECT}[-_]${FRAPPE_SERVICE}" --format '{{.ID}}' | head -n1)"
  fi
  [ -n "${c}" ] || { echo "could not find running '${FRAPPE_SERVICE}' container in project '${COMPOSE_PROJECT}'" >&2; return 1; }
  echo "${c}"
}

alert() {
  local msg="$1"
  log "FAILURE: ${msg}"
  if [ -n "${ALERT_WEBHOOK}" ]; then
    curl -fsS -X POST -H 'Content-Type: application/json' \
      -d "{\"text\":\"CRM backup FAILED ${STAMP}: ${msg}\"}" "${ALERT_WEBHOOK}" || true
  fi
}

main() {
  CONTAINER="$(resolve_container)"
  log "using container ${CONTAINER}"

  local backups_path="/home/frappe/${BENCH_DIR}/sites/${SITE}/private/backups"

  # 1) consistent full backup inside the container (DB single-transaction + files)
  log "running bench backup --with-files"
  docker exec "${CONTAINER}" bash -lc \
    "cd ${BENCH_DIR} && bench --site ${SITE} backup --with-files"

  # 2) identify the artifact set from THIS run (4 files sharing the newest timestamp prefix)
  local newest
  newest="$(docker exec "${CONTAINER}" bash -lc \
    "ls -1t ${backups_path}/*-database.sql.gz | head -n1")"
  [ -n "${newest}" ] || { alert "no database dump produced"; exit 1; }
  local base
  base="$(basename "${newest}" | sed 's/-database\.sql\.gz$//')"
  log "artifact set prefix: ${base}"

  # 3) copy this run's artifacts to the host
  mkdir -p "${WORKDIR}"
  local f
  for f in database.sql.gz files.tar private-files.tar site_config_backup.json; do
    docker cp "${CONTAINER}:${backups_path}/${base}-${f}" "${WORKDIR}/" 2>/dev/null \
      || log "warning: ${base}-${f} not present (ok for site_config on some versions)"
  done

  # 4) optional encryption of the DB dump
  if [ -n "${GPG_RECIPIENT}" ]; then
    log "encrypting DB dump for ${GPG_RECIPIENT}"
    gpg --batch --yes --encrypt --recipient "${GPG_RECIPIENT}" \
      "${WORKDIR}/${base}-database.sql.gz"
    rm -f "${WORKDIR}/${base}-database.sql.gz"
  fi

  # 5) push offsite
  log "uploading to ${RCLONE_REMOTE}/${STAMP}/"
  # shellcheck disable=SC2086
  rclone copy "${WORKDIR}" "${RCLONE_REMOTE}/${STAMP}/" ${RCLONE_EXTRA}

  # 6) prune local host copies (remote retention via bucket lifecycle policy — see BACKUP.md)
  log "pruning host copies older than ${LOCAL_RETENTION_DAYS}d"
  find "${HOST_BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d \
    -mtime "+${LOCAL_RETENTION_DAYS}" -exec rm -rf {} +

  log "done"
}

trap 'alert "script error on line ${LINENO}"' ERR
main "$@"
