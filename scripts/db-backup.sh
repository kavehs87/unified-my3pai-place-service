#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Backup PostgreSQL database from Docker Compose (local or remote via SSH)
# =============================================================================
# Creates timestamped .dump files (pg_dump -Fc custom format) in backups/.
# Supports local Docker Compose and remote VM via SSH.
# =============================================================================

# ─── Defaults ────────────────────────────────────────────────────────────────
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"
BACKUP_RETENTION="${BACKUP_RETENTION:-999999}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
REMOTE_MODE=false

STAGING_HOST="${STAGING_HOST:-}"
STAGING_USER="${STAGING_USER:-root}"
STAGING_PATH="${STAGING_PATH:-/root/ups}"
STAGING_SSH_KEY="${STAGING_SSH_KEY:-$HOME/.ssh/id_ed25519}"
STAGING_SSH_PORT="${STAGING_SSH_PORT:-22}"

PROD_MODE=false

# ─── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ─── Helpers ─────────────────────────────────────────────────────────────────
info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }
die()     { error "$*"; exit 1; }

# ─── Parse args ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    --remote)  REMOTE_MODE=true; shift ;;
    --prod)    PROD_MODE=true; REMOTE_MODE=true; shift ;;
    --help|-h)
      echo "Usage: $0 [--remote] [--prod]"
      echo ""
      echo "  --remote  Backup from staging VM (requires STAGING_HOST or .env.staging)"
      echo "  --prod    Backup from production VM (requires .env.production)"
      echo ""
      echo "Environment variables:"
      echo "  BACKUP_DIR        Output directory (default: ./backups)"
      echo "  BACKUP_RETENTION  Number of backups to keep (default: 7)"
      echo "  COMPOSE_FILE      Docker Compose file (default: docker-compose.yml)"
      echo "  STAGING_HOST      Remote VM IP/hostname (required for --remote)"
      echo "  STAGING_USER      SSH username (default: root)"
      echo "  STAGING_PATH      Project dir on VM (default: /root/ups)"
      echo "  STAGING_SSH_KEY   SSH private key (default: ~/.ssh/id_ed25519)"
      echo "  STAGING_SSH_PORT  SSH port (default: 22)"
      exit 0
      ;;
    *) die "Unknown option: $1" ;;
  esac
done

# ─── Load env files ──────────────────────────────────────────────────────────
if $PROD_MODE; then
  if [[ -f "$PROJECT_ROOT/.env.production" ]]; then
    info "Loading .env.production"
    set -a
    # shellcheck disable=SC1091
    . "$PROJECT_ROOT/.env.production"
    set +a
  fi
  STAGING_HOST="${PROD_HOST:-$STAGING_HOST}"
  STAGING_USER="${PROD_USER:-$STAGING_USER}"
  STAGING_PATH="${PROD_PATH:-$STAGING_PATH}"
  STAGING_SSH_KEY="${PROD_SSH_KEY:-$STAGING_SSH_KEY}"
  STAGING_SSH_PORT="${PROD_SSH_PORT:-$STAGING_SSH_PORT}"
  if [[ -z "$STAGING_HOST" ]]; then
    die "PROD_HOST is required for --prod mode. Set it or use .env.production."
  fi
elif $REMOTE_MODE; then
  if [[ -f "$PROJECT_ROOT/.env.staging" ]]; then
    info "Loading .env.staging"
    set -a
    # shellcheck disable=SC1091
    . "$PROJECT_ROOT/.env.staging"
    set +a
  fi
  if [[ -z "$STAGING_HOST" ]]; then
    die "STAGING_HOST is required for --remote mode. Set it or use .env.staging."
  fi
else
  if [[ -f "$PROJECT_ROOT/.env" ]]; then
    info "Loading .env"
    set -a
    # shellcheck disable=SC1091
    . "$PROJECT_ROOT/.env"
    set +a
  fi
fi

# ─── SSH config ──────────────────────────────────────────────────────────────
# Expand literal ~ in SSH key path (may come from .env as literal ~)
if [[ "$STAGING_SSH_KEY" == ~* ]]; then
  STAGING_SSH_KEY="$HOME/${STAGING_SSH_KEY#\~/}"
fi

SSH_COMMON_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)

run_ssh() {
  ssh "${SSH_COMMON_OPTS[@]}" -p "$STAGING_SSH_PORT" -i "$STAGING_SSH_KEY" "${STAGING_USER}@${STAGING_HOST}" "$@"
}

run_scp() {
  scp "${SSH_COMMON_OPTS[@]}" -P "$STAGING_SSH_PORT" -i "$STAGING_SSH_KEY" "$@"
}

# ─── Step 1: Validate ───────────────────────────────────────────────────────
info "=== Step 1: Validate ==="

if $REMOTE_MODE; then
  MODE_LABEL="$PROD_MODE" && MODE_LABEL="production" || MODE_LABEL="staging"
  info "Mode: remote (${MODE_LABEL} — ${STAGING_USER}@${STAGING_HOST})"
  for cmd in ssh scp; do
    command -v "$cmd" >/dev/null 2>&1 || die "$cmd is not installed"
  done
  if ! run_ssh "echo Connected" >/dev/null 2>&1; then
    die "Cannot connect to ${STAGING_USER}@${STAGING_HOST}"
  fi
  success "SSH connection successful"
else
  info "Mode: local"
  command -v docker >/dev/null 2>&1 || die "docker is not installed"
  if ! docker compose -f "$COMPOSE_FILE" ps >/dev/null 2>&1; then
    die "Docker Compose services are not running (compose file: $COMPOSE_FILE)"
  fi
  success "Docker Compose is running"
fi

# ─── Step 2: Wait for DB health ─────────────────────────────────────────────
info "=== Step 2: Wait for database ==="

if $REMOTE_MODE; then
  for i in $(seq 1 60); do
    if run_ssh "cd ${STAGING_PATH} && docker compose -f ${COMPOSE_FILE} exec -T db pg_isready" >/dev/null 2>&1; then
      success "Remote database is ready"
      break
    fi
    [[ $i -eq 60 ]] && die "Remote database failed to become ready within 60s"
    sleep 1
  done
else
  for i in $(seq 1 30); do
    if docker compose -f "$COMPOSE_FILE" exec -T db pg_isready >/dev/null 2>&1; then
      success "Local database is ready"
      break
    fi
    [[ $i -eq 30 ]] && die "Local database failed to become ready within 30s"
    sleep 1
  done
fi

# ─── Step 3: Create backup directory ────────────────────────────────────────
TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"
BACKUP_PATH="${BACKUP_DIR}/${TIMESTAMP}"
if $REMOTE_MODE; then
  MODE_LABEL="$PROD_MODE" && MODE_LABEL="prod" || MODE_LABEL="staging"
  BACKUP_PATH="${BACKUP_PATH}_${MODE_LABEL}_${STAGING_HOST//\./_}"
fi
mkdir -p "$BACKUP_PATH"
info "=== Step 3: Backup directory === $BACKUP_PATH"

# ─── Step 4: Run pg_dump ────────────────────────────────────────────────────
info "=== Step 4: Running pg_dump ==="

DUMP_FILE="dump.dump"
DUMP_LOCAL="${BACKUP_PATH}/${DUMP_FILE}"

if $REMOTE_MODE; then
  CONTAINER_TMP="/tmp/${DUMP_FILE}"
  HOST_TMP="${STAGING_PATH}/tmp/${DUMP_FILE}"
  mkdir -p "${STAGING_PATH}/tmp" 2>/dev/null || true
  info "Dumping remote database to container ${CONTAINER_TMP}..."
  if ! run_ssh "cd ${STAGING_PATH} && docker compose -f ${COMPOSE_FILE} exec -T db pg_dump -Fc -U \${POSTGRES_USER:-postgres} -d \${POSTGRES_DB:-dmo} -f ${CONTAINER_TMP}"; then
    die "Remote pg_dump failed"
  fi
  success "Remote pg_dump complete"
  info "Copying dump from container to VM host..."
  if ! run_ssh "cd ${STAGING_PATH} && mkdir -p tmp && docker compose -f ${COMPOSE_FILE} cp db:${CONTAINER_TMP} ${HOST_TMP}"; then
    die "Failed to copy dump from container to VM host"
  fi
  info "Copying dump to local machine..."
  if ! run_scp "${STAGING_USER}@${STAGING_HOST}:${HOST_TMP}" "$DUMP_LOCAL"; then
    die "Failed to copy dump from remote"
  fi
  run_ssh "rm -f ${HOST_TMP} ${CONTAINER_TMP}" || true
  run_ssh "cd ${STAGING_PATH} && docker compose -f ${COMPOSE_FILE} exec -T db rm -f ${CONTAINER_TMP}" || true
else
  TMPFILE="/tmp/${DUMP_FILE}"
  info "Dumping local database..."
  if ! docker compose -f "$COMPOSE_FILE" exec -T db pg_dump -Fc -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-dmo}" -f "$TMPFILE"; then
    die "Local pg_dump failed"
  fi
  success "Local pg_dump complete"
  info "Copying dump from container..."
  if ! docker compose -f "$COMPOSE_FILE" cp "db:${TMPFILE}" "$DUMP_LOCAL"; then
    die "Failed to copy dump from container"
  fi
  docker compose -f "$COMPOSE_FILE" exec -T db rm -f "$TMPFILE" || true
fi

DUMP_SIZE=$(du -h "$DUMP_LOCAL" | cut -f1)
DUMP_SHA256=$(sha256sum "$DUMP_LOCAL" | cut -d' ' -f1)
info "Dump file: ${DUMP_SIZE} (SHA-256: ${DUMP_SHA256:0:16}...)"

# ─── Step 5: Collect metadata ───────────────────────────────────────────────
info "=== Step 5: Writing metadata ==="

if $REMOTE_MODE; then
  PG_VERSION=$(run_ssh "cd ${STAGING_PATH} && docker compose -f ${COMPOSE_FILE} exec -T db psql -U \${POSTGRES_USER:-postgres} -d \${POSTGRES_DB:-dmo} -t -c 'SELECT version()'" 2>/dev/null | head -1 | sed 's/^[[:space:]]*//')
  TABLE_COUNT=$(run_ssh "cd ${STAGING_PATH} && docker compose -f ${COMPOSE_FILE} exec -T db psql -U \${POSTGRES_USER:-postgres} -d \${POSTGRES_DB:-dmo} -t -c 'SELECT count(*) FROM information_schema.tables WHERE table_schema = '\''public'\'''" 2>/dev/null | head -1 | xargs)
else
  PG_VERSION=$(docker compose -f "$COMPOSE_FILE" exec -T db psql -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-dmo}" -t -c "SELECT version()" 2>/dev/null | head -1 | sed 's/^[[:space:]]*//')
  TABLE_COUNT=$(docker compose -f "$COMPOSE_FILE" exec -T db psql -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-dmo}" -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'" 2>/dev/null | head -1 | xargs)
fi

MODE_VALUE="$PROD_MODE" && MODE_VALUE="production" || MODE_VALUE="${REMOTE_MODE}"
cat > "${BACKUP_PATH}/metadata.json" <<EOF
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "host": "${STAGING_HOST:-localhost}",
  "mode": "${MODE_VALUE}",
  "database": "${POSTGRES_DB:-dmo}",
  "pg_version": "${PG_VERSION:-unknown}",
  "table_count": "${TABLE_COUNT:-unknown}",
  "dump_file": "${DUMP_FILE}",
  "dump_size": "${DUMP_SIZE}",
  "sha256": "${DUMP_SHA256}"
}
EOF
success "Metadata written to ${BACKUP_PATH}/metadata.json"

# ─── Step 6: Cleanup old backups ────────────────────────────────────────────
info "=== Step 6: Retention cleanup (keeping last ${BACKUP_RETENTION}) ==="

BACKUP_COUNT=$(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l | xargs)
if [[ $BACKUP_COUNT -gt $BACKUP_RETENTION ]]; then
  TO_DELETE=$((BACKUP_COUNT - BACKUP_RETENTION))
  info "Removing ${TO_DELETE} old backup(s)..."
  find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T+ %p\n' | sort | head -n "$TO_DELETE" | awk '{print $2}' | while read -r old_dir; do
    info "  Removing: $(basename "$old_dir")"
    rm -rf "$old_dir"
  done
  success "Cleanup complete"
else
  info "No cleanup needed (${BACKUP_COUNT}/${BACKUP_RETENTION} backups)"
fi

# ─── Done ────────────────────────────────────────────────────────────────────
success "Backup complete: $BACKUP_PATH"
info "Backups directory: $BACKUP_DIR"
info "Total backups: $(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l | xargs)"
