#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Deploy unified-my3pai-place-service to staging Proxmox VM
# =============================================================================
# Uploads via rsync, then builds/deploys with Docker Compose on the VM.
# =============================================================================

# ─── Defaults ────────────────────────────────────────────────────────────────
STAGING_HOST="${STAGING_HOST:-192.168.1.100}"
STAGING_USER="${STAGING_USER:-deploy}"
STAGING_PATH="${STAGING_PATH:-/opt/dmo-staging}"
STAGING_SSH_KEY="${STAGING_SSH_KEY:-$HOME/.ssh/id_ed25519}"
STAGING_SSH_PORT="${STAGING_SSH_PORT:-22}"
STAGING_COMPOSE_FILE="${STAGING_COMPOSE_FILE:-docker-compose.prod.yml}"
STAGING_DB_MIGRATE="${STAGING_DB_MIGRATE:-true}"
HEALTH_CHECK_TIMEOUT="${HEALTH_CHECK_TIMEOUT:-60}"
HEALTH_CHECK_PORT="${HEALTH_CHECK_PORT:-8000}"

DRY_RUN=false
SYNC_ONLY=false

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
    --dry-run)   DRY_RUN=true; shift ;;
    --sync-only) SYNC_ONLY=true; shift ;;
    --help|-h)
      echo "Usage: $0 [--dry-run] [--sync-only]"
      echo ""
      echo "  --dry-run   Show rsync changes without deploying"
      echo "  --sync-only Upload files only, skip Docker steps"
      echo ""
      echo "Environment variables:"
      echo "  STAGING_HOST      VM IP or hostname (default: 192.168.1.100)"
      echo "  STAGING_USER      SSH username (default: deploy)"
      echo "  STAGING_PATH      Destination dir on VM (default: /opt/dmo-staging)"
      echo "  STAGING_SSH_KEY   SSH private key path (default: ~/.ssh/id_ed25519)"
      echo "  STAGING_SSH_PORT  SSH port (default: 22)"
      echo "  STAGING_DB_MIGRATE Run Alembic migrations (default: true)"
      exit 0
      ;;
    *) die "Unknown option: $1" ;;
  esac
done

# ─── Load .env.staging if present ───────────────────────────────────────────
if [[ -f .env.staging ]]; then
  info "Loading .env.staging"
  while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" =~ ^# ]] && continue
    key=$(echo "$key" | xargs)
    value=$(echo "$value" | xargs | sed 's/^"\(.*\)"$/\1/')
    export "$key=$value"
  done < .env.staging
fi

# ─── SSH config ──────────────────────────────────────────────────────────────
# Expand ~ in SSH key path (may come from .env.staging as literal ~)
case "$STAGING_SSH_KEY" in
  ~/*) STAGING_SSH_KEY="$HOME/${STAGING_SSH_KEY#\~/}" ;;
esac

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p "$STAGING_SSH_PORT")

# Use arrays for safe word splitting
SSH_BASE=(ssh "${SSH_OPTS[@]}" -i "$STAGING_SSH_KEY")
RSYNC_SSH="ssh ${SSH_OPTS[*]} -i $STAGING_SSH_KEY"

run_ssh() {
  ssh "${SSH_OPTS[@]}" -i "$STAGING_SSH_KEY" "${STAGING_USER}@${STAGING_HOST}" "$@"
}

# ─── Rsync exclusions ────────────────────────────────────────────────────────
EXCLUDES=(
  ".git/"
  ".venv/"
  "__pycache__/"
  "*.pyc"
  ".pytest_cache/"
  ".ruff_cache/"
  "loadtest/"
  "plans/"
  "docs/"
  "backups/"
  ".env"
  ".env.staging"
  ".DS_Store"
  "*.egg-info"
  ".opencode/"
  "opencode.json"
  "opencode.jsonc"
  "AGENTS.md"
  "*.swp"
  "*~"
)

# Build rsync exclude args
EXCLUDE_ARGS=()
for exc in "${EXCLUDES[@]}"; do
  EXCLUDE_ARGS+=("--exclude=$exc")
done

# ─── Step 1: Validate ───────────────────────────────────────────────────────
info "=== Step 1: Validate ==="

# Check local tools
for cmd in rsync ssh; do
  command -v "$cmd" >/dev/null 2>&1 || die "$cmd is not installed locally"
done

# Check SSH connectivity
info "Testing SSH connection to ${STAGING_USER}@${STAGING_HOST}..."
if ! ssh "${SSH_OPTS[@]}" -i "$STAGING_SSH_KEY" "${STAGING_USER}@${STAGING_HOST}" "echo 'Connected'" >/dev/null 2>&1; then
  die "Cannot connect to ${STAGING_USER}@${STAGING_HOST} via SSH"
fi
success "SSH connection successful"

# Check Docker on VM
info "Checking Docker on VM..."
if ! run_ssh "docker version >/dev/null 2>&1"; then
  die "Docker is not installed or not running on the VM"
fi
success "Docker is available on VM"

# ─── Step 2: Sync ───────────────────────────────────────────────────────────
info "=== Step 2: Sync files via rsync ==="
info "Source:     $(pwd)/"
info "Destination: ${STAGING_USER}@${STAGING_HOST}:${STAGING_PATH}/"

if $DRY_RUN; then
  info "DRY RUN — would run:"
  echo "rsync -azvh --delete -e \"$RSYNC_SSH\" ${EXCLUDE_ARGS[@]} --dry-run ./ ${STAGING_USER}@${STAGING_HOST}:${STAGING_PATH}/"
  rsync -azvh --delete -e "$RSYNC_SSH" "${EXCLUDE_ARGS[@]}" --dry-run ./ "${STAGING_USER}@${STAGING_HOST}:${STAGING_PATH}/"
  success "Dry run complete"
  exit 0
fi

# Create remote directory if it doesn't exist
run_ssh "mkdir -p ${STAGING_PATH}"

# Run rsync
rsync -azvh --delete -e "$RSYNC_SSH" "${EXCLUDE_ARGS[@]}" ./ "${STAGING_USER}@${STAGING_HOST}:${STAGING_PATH}/"
success "Files synced to ${STAGING_PATH}"

# ─── Step 2b: Provision .env ────────────────────────────────────────────────
if ! run_ssh "test -f ${STAGING_PATH}/.env"; then
  if [[ -f .env.template ]]; then
    info "Provisioning .env from .env.template..."
    run_ssh "cp ${STAGING_PATH}/.env.template ${STAGING_PATH}/.env"
    success ".env created — edit on VM to set production secrets"
  else
    warn ".env.template not found — ensure .env exists on VM manually"
  fi
fi

if $SYNC_ONLY; then
  success "Sync-only mode — skipping Docker deployment"
  exit 0
fi

# ─── Step 3: Docker Build ───────────────────────────────────────────────────
info "=== Step 3: Docker build on VM ==="
info "Building images with ${STAGING_COMPOSE_FILE}..."

if run_ssh "cd ${STAGING_PATH} && docker compose -f ${STAGING_COMPOSE_FILE} build --no-cache"; then
  success "Docker images built successfully"
else
  die "Docker build failed"
fi

# ─── Step 4: Migrate ────────────────────────────────────────────────────────
if [[ "$STAGING_DB_MIGRATE" == "true" ]]; then
  info "=== Step 4: Alembic migrations ==="

  # Ensure db is running first
  info "Starting database container..."
  run_ssh "cd ${STAGING_PATH} && docker compose -f ${STAGING_COMPOSE_FILE} up -d db"

  # Wait for DB to be ready
  info "Waiting for database to be ready..."
  for i in $(seq 1 90); do
    if run_ssh "cd ${STAGING_PATH} && docker compose -f ${STAGING_COMPOSE_FILE} exec -T db pg_isready >/dev/null 2>&1"; then
      success "Database is ready"
      break
    fi
    if [[ $i -eq 90 ]]; then
      die "Database failed to start within 90 seconds"
    fi
    sleep 1
  done

  # Run migrations
  info "Running Alembic migrations..."
  if run_ssh "cd ${STAGING_PATH} && docker compose -f ${STAGING_COMPOSE_FILE} exec -T api alembic upgrade head"; then
    success "Migrations applied"
  else
    warn "Migration failed — check container logs"
  fi
else
  info "=== Step 4: Skipping migrations (STAGING_DB_MIGRATE=false) ==="
fi

# ─── Step 5: Restart services ───────────────────────────────────────────────
info "=== Step 5: Restart services ==="

if run_ssh "cd ${STAGING_PATH} && docker compose -f ${STAGING_COMPOSE_FILE} up -d"; then
  success "Services started"
else
  die "Failed to start services"
fi

# ─── Step 6: Health check ───────────────────────────────────────────────────
info "=== Step 6: Health check ==="
info "Polling http://${STAGING_HOST}:${HEALTH_CHECK_PORT}/health (timeout: ${HEALTH_CHECK_TIMEOUT}s)..."

HEALTH_URL="http://${STAGING_HOST}:${HEALTH_CHECK_PORT}/health"
for i in $(seq 1 $HEALTH_CHECK_TIMEOUT); do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo "000")
  if [[ "$HTTP_CODE" == "200" ]]; then
    success "Health check passed (HTTP $HTTP_CODE)"
    info "Staging deployment complete!"
    info "API:    $HEALTH_URL"
    info "PgAdmin: http://${STAGING_HOST}:8888"
    exit 0
  fi
  if [[ $i -eq $HEALTH_CHECK_TIMEOUT ]]; then
    die "Health check timed out after ${HEALTH_CHECK_TIMEOUT}s (last HTTP: $HTTP_CODE)"
  fi
  sleep 1
done