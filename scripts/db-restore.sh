#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Restore PostgreSQL database to Docker Compose (local or remote via SSH)
# =============================================================================
# Restores from a .dump file (pg_dump -Fc custom format) into the Docker
# Compose database. Stops api service during restore, runs Alembic migrations
# after restore to ensure schema version table is in sync.
# =============================================================================

# ─── Defaults ────────────────────────────────────────────────────────────────
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
REMOTE_MODE=false
TEST_MODE=false
FORCE_MODE=false
LIST_MODE=false
CUSTOM_HOST=false
CUSTOM_HOST_SPEC=""
CUSTOM_PATH_SET=false
CUSTOM_PATH_VALUE=""
RESTORE_FILE=""

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
    --test)    TEST_MODE=true; shift ;;
    --prod)    PROD_MODE=true; REMOTE_MODE=true; shift ;;
    --force)   FORCE_MODE=true; shift ;;
    --list)    LIST_MODE=true; shift ;;
    --host)
       CUSTOM_HOST=true
       [[ $# -ge 2 ]] || die "--host requires an argument (USER@HOST[:PORT])"
       CUSTOM_HOST_SPEC="$2"; shift 2
       ;;
    --path)
       CUSTOM_PATH_SET=true
       [[ $# -ge 2 ]] || die "--path requires an argument"
       CUSTOM_PATH_VALUE="$2"; shift 2
       ;;
    --help|-h)
      echo "Usage: $0 [--remote] [--test] [--prod] [--host USER@HOST[:PORT]] [--path DIR] [--force] [--list] [backup_file]"
      echo ""
      echo "  --remote      Restore to staging VM (requires STAGING_HOST or .env.staging)"
      echo "  --prod        Restore to production VM (requires .env.production)"
      echo "  --test        Restore to test VM (10.0.1.8)"
      echo "  --host        Restore to an arbitrary VM (USER@HOST[:PORT]), overrides env files"
      echo "  --path        Project dir on target VM (default: /root/ups, used with --host)"
      echo "  --force       Skip confirmation prompt"
      echo "  --list        List available backups"
      echo "  backup_file   Path to .dump file (default: latest backup)"
      echo ""
      echo "Environment variables:"
      echo "  BACKUP_DIR        Backups directory (default: ./backups)"
      echo "  COMPOSE_FILE      Docker Compose file (default: docker-compose.yml)"
      echo "  STAGING_HOST      Remote VM IP/hostname (required for --remote)"
      echo "  STAGING_USER      SSH username (default: root)"
      echo "  STAGING_PATH      Project dir on VM (default: /root/ups)"
      echo "  STAGING_SSH_KEY   SSH private key (default: ~/.ssh/id_ed25519)"
      echo "  STAGING_SSH_PORT  SSH port (default: 22)"
      exit 0
      ;;
    --*) die "Unknown option: $1" ;;
    *)   RESTORE_FILE="$1"; shift ;;
  esac
done

# ─── Load env files ──────────────────────────────────────────────────────────
if $TEST_MODE; then
  STAGING_HOST="${TEST_HOST:-10.0.1.8}"
  STAGING_USER="${TEST_USER:-root}"
  STAGING_PATH="${TEST_PATH:-/root/ups}"
  REMOTE_MODE=true
  info "Test VM mode: ${STAGING_USER}@${STAGING_HOST} (${STAGING_PATH})"
elif $PROD_MODE; then
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
  info "Production VM mode: ${STAGING_USER}@${STAGING_HOST} (${STAGING_PATH})"
elif $REMOTE_MODE; then
  if [[ -f "$PROJECT_ROOT/.env.staging" ]]; then
    info "Loading .env.staging"
    set -a
    # shellcheck disable=SC1091
    . "$PROJECT_ROOT/.env.staging"
    set +a
  fi
  if [[ -z "$STAGING_HOST" ]]; then
    die "STAGING_HOST is required for --remote mode."
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

# ─── Custom host override (--host) ───────────────────────────────────────────
if $CUSTOM_HOST; then
  [[ "$CUSTOM_HOST_SPEC" == *@* ]] || die "--host format: USER@HOST[:PORT] (got: $CUSTOM_HOST_SPEC)"
  STAGING_USER="${CUSTOM_HOST_SPEC%%@*}"
  HOSTPORT="${CUSTOM_HOST_SPEC#*@}"
  STAGING_HOST="${HOSTPORT%%:*}"
  if [[ "$HOSTPORT" == *:* ]]; then
    STAGING_SSH_PORT="${HOSTPORT##*:}"
  fi
  STAGING_PATH="${CUSTOM_PATH_VALUE:-/root/ups}"
  REMOTE_MODE=true
  info "Custom host mode: ${STAGING_USER}@${STAGING_HOST}:${STAGING_SSH_PORT} (${STAGING_PATH})"
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

# ─── List backups ────────────────────────────────────────────────────────────
if $LIST_MODE; then
  info "Available backups in $BACKUP_DIR:"
  echo ""
  if [[ ! -d "$BACKUP_DIR" ]] || [[ -z "$(ls -A "$BACKUP_DIR" 2>/dev/null)" ]]; then
    warn "No backups found"
    exit 0
  fi
  printf "%-28s %10s  %s\n" "TIMESTAMP" "SIZE" "HOST"
  printf "%-28s %10s  %s\n" "-------------------------" "----------" "----------"
  for dir in $(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d | sort); do
    meta="${dir}/metadata.json"
    if [[ -f "$meta" ]]; then
      ts=$(python3 -c "import json; print(json.load(open('$meta'))['timestamp'])" 2>/dev/null || echo "unknown")
      sz=$(python3 -c "import json; print(json.load(open('$meta'))['dump_size'])" 2>/dev/null || echo "unknown")
      host=$(python3 -c "import json; print(json.load(open('$meta'))['host'])" 2>/dev/null || echo "unknown")
    else
      ts="$(basename "$dir")"
      sz="unknown"
      host="unknown"
    fi
    printf "%-28s %10s  %s\n" "$ts" "$sz" "$host"
  done
  echo ""
  exit 0
fi

# ─── Find backup file ────────────────────────────────────────────────────────
if [[ -z "$RESTORE_FILE" ]]; then
  LATEST=$(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T+ %p\n' 2>/dev/null | sort | tail -1 | awk '{print $2}')
  if [[ -z "$LATEST" ]]; then
    die "No backups found in $BACKUP_DIR"
  fi
  RESTORE_FILE="${LATEST}/dump.dump"
  info "Using latest backup: $(basename "$LATEST")"
fi

if [[ ! -f "$RESTORE_FILE" ]]; then
  die "Backup file not found: $RESTORE_FILE"
fi

BACKUP_DIR_PARENT="$(dirname "$RESTORE_FILE")"
META_FILE="${BACKUP_DIR_PARENT}/metadata.json"

# ─── Validate backup integrity ───────────────────────────────────────────────
info "=== Validating backup ==="

CURRENT_SHA256=$(sha256sum "$RESTORE_FILE" | cut -d' ' -f1)
DUMP_SIZE=$(du -h "$RESTORE_FILE" | cut -f1)
info "File: $DUMP_SIZE (SHA-256: ${CURRENT_SHA256:0:16}...)"

if [[ -f "$META_FILE" ]]; then
  EXPECTED_SHA256=$(python3 -c "import json; print(json.load(open('$META_FILE'))['sha256'])" 2>/dev/null || echo "")
  if [[ -n "$EXPECTED_SHA256" ]]; then
    if [[ "$CURRENT_SHA256" != "$EXPECTED_SHA256" ]]; then
      die "SHA-256 mismatch! Backup may be corrupted."
    fi
    success "Checksum verified"
  fi
  BACKUP_HOST=$(python3 -c "import json; print(json.load(open('$META_FILE'))['host'])" 2>/dev/null || echo "unknown")
  BACKUP_TS=$(python3 -c "import json; print(json.load(open('$META_FILE'))['timestamp'])" 2>/dev/null || echo "unknown")
  info "Backup from: ${BACKUP_HOST} at ${BACKUP_TS}"
else
  warn "No metadata.json found, skipping integrity check"
fi

# ─── Confirmation ────────────────────────────────────────────────────────────
if ! $FORCE_MODE; then
  echo ""
  warn "WARNING: This will REPLACE the entire database!"
  echo ""
  if $CUSTOM_HOST; then
    echo "  Target: ${STAGING_USER}@${STAGING_HOST}:${STAGING_SSH_PORT} (CUSTOM VM)"
  elif $TEST_MODE; then
    echo "  Target: ${STAGING_USER}@${STAGING_HOST} (TEST VM)"
  elif $PROD_MODE; then
    echo "  Target: ${STAGING_USER}@${STAGING_HOST} (PRODUCTION VM)"
  elif $REMOTE_MODE; then
    echo "  Target: ${STAGING_USER}@${STAGING_HOST} (STAGING VM)"
  else
    echo "  Target: local Docker Compose ($COMPOSE_FILE)"
  fi
  echo "  Backup:  $RESTORE_FILE"
  echo ""
  read -r -p "Type 'RESTORE' to confirm: " confirm
  if [[ "$confirm" != "RESTORE" ]]; then
    info "Restore cancelled"
    exit 0
  fi
fi

# ─── Step 1: Validate connectivity ──────────────────────────────────────────
info "=== Step 1: Validate ==="

if $REMOTE_MODE; then
  for cmd in ssh scp; do
    command -v "$cmd" >/dev/null 2>&1 || die "$cmd is not installed"
  done
  if ! run_ssh "echo Connected" >/dev/null 2>&1; then
    die "Cannot connect to ${STAGING_USER}@${STAGING_HOST}"
  fi
  success "SSH connection successful"
else
  command -v docker >/dev/null 2>&1 || die "docker is not installed"
fi

# ─── Step 2: Stop api service ───────────────────────────────────────────────
info "=== Step 2: Stop api service ==="

if $REMOTE_MODE; then
  run_ssh "cd ${STAGING_PATH} && docker compose -f ${COMPOSE_FILE} stop api" || warn "api service was not running"
else
  docker compose -f "$COMPOSE_FILE" stop api || warn "api service was not running"
fi
success "api service stopped"

# ─── Step 3: Ensure db is running ────────────────────────────────────────────
info "=== Step 3: Ensure database is running ==="

if $REMOTE_MODE; then
  run_ssh "cd ${STAGING_PATH} && docker compose -f ${COMPOSE_FILE} up -d db"
else
  docker compose -f "$COMPOSE_FILE" up -d db
fi

for i in $(seq 1 60); do
  if $REMOTE_MODE; then
    if run_ssh "cd ${STAGING_PATH} && docker compose -f ${COMPOSE_FILE} exec -T db pg_isready" >/dev/null 2>&1; then
      break
    fi
  else
    if docker compose -f "$COMPOSE_FILE" exec -T db pg_isready >/dev/null 2>&1; then
      break
    fi
  fi
  [[ $i -eq 60 ]] && die "Database failed to start within 60s"
  sleep 1
done
success "Database is ready"

# ─── Step 4: Drop and recreate database ─────────────────────────────────────
info "=== Step 4: Drop and recreate database ==="

DB_NAME="${POSTGRES_DB:-dmo}"
DB_USER="${POSTGRES_USER:-postgres}"

if $REMOTE_MODE; then
  run_ssh "cd ${STAGING_PATH} && docker compose -f ${COMPOSE_FILE} exec -T db psql -U \${POSTGRES_USER:-postgres} -d postgres -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${DB_NAME}' AND pid <> pg_backend_pid();\""
  run_ssh "cd ${STAGING_PATH} && docker compose -f ${COMPOSE_FILE} exec -T db psql -U \${POSTGRES_USER:-postgres} -d postgres -c \"DROP DATABASE IF EXISTS ${DB_NAME};\""
  run_ssh "cd ${STAGING_PATH} && docker compose -f ${COMPOSE_FILE} exec -T db psql -U \${POSTGRES_USER:-postgres} -d postgres -c \"CREATE DATABASE ${DB_NAME};\""
else
  docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$DB_USER" -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${DB_NAME}' AND pid <> pg_backend_pid();"
  docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS ${DB_NAME};"
  docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$DB_USER" -d postgres -c "CREATE DATABASE ${DB_NAME};"
fi
success "Database recreated: ${DB_NAME}"

# ─── Step 5: Restore dump ───────────────────────────────────────────────────
info "=== Step 5: Restoring database ==="

if $REMOTE_MODE; then
  HOST_DUMP="${STAGING_PATH}/tmp/dump.restore.dump"
  CONTAINER_DUMP="/tmp/dump.restore.dump"
  info "Uploading dump to remote host..."
  run_ssh "mkdir -p ${STAGING_PATH}/tmp"
  run_scp "$RESTORE_FILE" "${STAGING_USER}@${STAGING_HOST}:${HOST_DUMP}"
  info "Copying dump from host to container..."
  if ! run_ssh "cd ${STAGING_PATH} && docker compose -f ${COMPOSE_FILE} cp ${HOST_DUMP} db:${CONTAINER_DUMP}"; then
    run_ssh "rm -f ${HOST_DUMP}" || true
    die "Failed to copy dump into container"
  fi
  info "Running pg_restore on remote..."
  if ! run_ssh "cd ${STAGING_PATH} && docker compose -f ${COMPOSE_FILE} exec -T db pg_restore -U \${POSTGRES_USER:-postgres} -d \${POSTGRES_DB:-dmo} --no-owner --no-privileges ${CONTAINER_DUMP}"; then
    run_ssh "rm -f ${HOST_DUMP}" || true
    run_ssh "cd ${STAGING_PATH} && docker compose -f ${COMPOSE_FILE} exec -T db rm -f ${CONTAINER_DUMP}" || true
    die "Remote pg_restore failed"
  fi
  run_ssh "rm -f ${HOST_DUMP}" || true
  run_ssh "cd ${STAGING_PATH} && docker compose -f ${COMPOSE_FILE} exec -T db rm -f ${CONTAINER_DUMP}" || true
else
  TMPFILE="/tmp/dump.restore.dump"
  info "Copying dump to container..."
  docker compose -f "$COMPOSE_FILE" cp "$RESTORE_FILE" "db:${TMPFILE}"
  info "Running pg_restore..."
  if ! docker compose -f "$COMPOSE_FILE" exec -T db pg_restore -U "$DB_USER" -d "$DB_NAME" --no-owner --no-privileges "$TMPFILE"; then
    docker compose -f "$COMPOSE_FILE" exec -T db rm -f "$TMPFILE" || true
    die "pg_restore failed"
  fi
  docker compose -f "$COMPOSE_FILE" exec -T db rm -f "$TMPFILE" || true
fi
success "Database restored"

# ─── Step 6: Run Alembic migrations ─────────────────────────────────────────
info "=== Step 6: Running Alembic migrations ==="

if $REMOTE_MODE; then
  run_ssh "cd ${STAGING_PATH} && docker compose -f ${COMPOSE_FILE} up -d db"
  for i in $(seq 1 30); do
    if run_ssh "cd ${STAGING_PATH} && docker compose -f ${COMPOSE_FILE} exec -T db pg_isready" >/dev/null 2>&1; then
      break
    fi
    [[ $i -eq 30 ]] && die "Database not ready for migrations"
    sleep 1
  done
  if run_ssh "cd ${STAGING_PATH} && docker compose -f ${COMPOSE_FILE} run --rm api alembic upgrade head"; then
    success "Alembic migrations applied"
  else
    warn "Alembic migration had issues — check logs"
  fi
else
  if docker compose -f "$COMPOSE_FILE" run --rm api alembic upgrade head; then
    success "Alembic migrations applied"
  else
    warn "Alembic migration had issues — check logs"
  fi
fi

# ─── Step 7: Start api service ──────────────────────────────────────────────
info "=== Step 7: Start api service ==="

if $REMOTE_MODE; then
  run_ssh "cd ${STAGING_PATH} && docker compose -f ${COMPOSE_FILE} start api"
else
  docker compose -f "$COMPOSE_FILE" start api
fi
success "api service started"

# ─── Done ────────────────────────────────────────────────────────────────────
success "Restore complete!"
  if $CUSTOM_HOST; then
    info "Restored on: ${STAGING_USER}@${STAGING_HOST}:${STAGING_SSH_PORT} (CUSTOM VM)"
  elif $TEST_MODE; then
    info "Restored on: ${STAGING_USER}@${STAGING_HOST} (TEST VM)"
  elif $PROD_MODE; then
    info "Restored on: ${STAGING_USER}@${STAGING_HOST} (PRODUCTION VM)"
  elif $REMOTE_MODE; then
    info "Restored on: ${STAGING_USER}@${STAGING_HOST} (STAGING VM)"
  else
    info "Restored on: local Docker Compose"
  fi
info "Backup used: $RESTORE_FILE"
