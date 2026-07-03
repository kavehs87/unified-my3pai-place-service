#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Deploy unified-my3pai-place-service to test/staging/production VM
# =============================================================================
# Uploads via rsync, then builds/deploys with Docker Compose on the VM.
# Each environment gets its own compose file, deployed as docker-compose.yml.
# =============================================================================

# ─── Defaults ────────────────────────────────────────────────────────────────
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_HOST=""
REMOTE_USER=""
REMOTE_PATH=""
REMOTE_SSH_KEY="${HOME}/.ssh/id_ed25519"
REMOTE_SSH_PORT=22
COMPOSE_SOURCE=""
COMPOSE_TARGET="docker-compose.yml"
DB_MIGRATE="${DB_MIGRATE:-true}"
HEALTH_CHECK_TIMEOUT=60
HEALTH_CHECK_PORT=8000

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
ENVIRONMENT=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --test)
      if [[ -n "$ENVIRONMENT" ]]; then
        die "Specify only one environment: --test, --staging, or --prod"
      fi
      ENVIRONMENT="test"
      shift
      ;;
    --staging)
      if [[ -n "$ENVIRONMENT" ]]; then
        die "Specify only one environment: --test, --staging, or --prod"
      fi
      ENVIRONMENT="staging"
      shift
      ;;
    --prod)
      if [[ -n "$ENVIRONMENT" ]]; then
        die "Specify only one environment: --test, --staging, or --prod"
      fi
      ENVIRONMENT="production"
      shift
      ;;
    --no-migrate)
      DB_MIGRATE=false
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --sync-only)
      SYNC_ONLY=true
      shift
      ;;
    --help|-h)
      echo "Usage: $0 (--test|--staging|--prod) [--dry-run] [--sync-only] [--no-migrate]"
      echo ""
      echo "  --test        Deploy to test VM (loads .env.test)"
      echo "  --staging     Deploy to staging VM (loads .env.staging)"
      echo "  --prod        Deploy to production VM (loads .env.production)"
      echo "  --dry-run     Show rsync changes without deploying"
      echo "  --sync-only   Upload files only, skip Docker steps"
      echo "  --no-migrate  Skip Alembic migrations"
      echo ""
      echo "Environment files:"
      echo "  .env.test       TEST_HOST, TEST_USER, TEST_PATH, TEST_SSH_KEY"
      echo "  .env.staging    STAGING_HOST, STAGING_USER, STAGING_PATH, STAGING_SSH_KEY"
      echo "  .env.production PROD_HOST, PROD_USER, PROD_PATH, PROD_SSH_KEY"
      exit 0
      ;;
    *) die "Unknown option: $1" ;;
  esac
done

if [[ -z "$ENVIRONMENT" ]]; then
  die "Environment is required. Use --test, --staging, or --prod"
fi

# ─── Load env file ──────────────────────────────────────────────────────────
ENV_FILE="$PROJECT_ROOT/.env.${ENVIRONMENT}"
if [[ ! -f "$ENV_FILE" ]]; then
  die ".env.${ENVIRONMENT} not found at $ENV_FILE"
fi

info "Loading .env.${ENVIRONMENT}"
set -a
# shellcheck disable=SC1091
. "$ENV_FILE"
set +a

# ─── Map environment variables ──────────────────────────────────────────────
case "$ENVIRONMENT" in
  test)
    REMOTE_HOST="${TEST_HOST:-$REMOTE_HOST}"
    REMOTE_USER="${TEST_USER:-$REMOTE_USER}"
    REMOTE_PATH="${TEST_PATH:-$REMOTE_PATH}"
    REMOTE_SSH_KEY="${TEST_SSH_KEY:-$REMOTE_SSH_KEY}"
    REMOTE_SSH_PORT="${TEST_SSH_PORT:-$REMOTE_SSH_PORT}"
    COMPOSE_SOURCE="docker-compose.test.yml"
    ;;
  staging)
    REMOTE_HOST="${STAGING_HOST:-$REMOTE_HOST}"
    REMOTE_USER="${STAGING_USER:-$REMOTE_USER}"
    REMOTE_PATH="${STAGING_PATH:-$REMOTE_PATH}"
    REMOTE_SSH_KEY="${STAGING_SSH_KEY:-$REMOTE_SSH_KEY}"
    REMOTE_SSH_PORT="${STAGING_SSH_PORT:-$REMOTE_SSH_PORT}"
    COMPOSE_SOURCE="docker-compose.staging.yml"
    ;;
  production)
    REMOTE_HOST="${PROD_HOST:-$REMOTE_HOST}"
    REMOTE_USER="${PROD_USER:-$REMOTE_USER}"
    REMOTE_PATH="${PROD_PATH:-$REMOTE_PATH}"
    REMOTE_SSH_KEY="${PROD_SSH_KEY:-$REMOTE_SSH_KEY}"
    REMOTE_SSH_PORT="${PROD_SSH_PORT:-$REMOTE_SSH_PORT}"
    COMPOSE_SOURCE="docker-compose.prod.yml"
    ;;
esac

# Expand literal ~ in SSH key path (only if it starts with ~/)
case "$REMOTE_SSH_KEY" in
  ~/*) REMOTE_SSH_KEY="$HOME/${REMOTE_SSH_KEY#\~/}" ;;
  ~/)  REMOTE_SSH_KEY="$HOME/" ;;
esac

if [[ -z "$REMOTE_HOST" ]]; then
  die "${ENVIRONMENT^^}_HOST is not set in .env.${ENVIRONMENT}"
fi

info "Target: ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}"
info "Compose: ${COMPOSE_SOURCE} → ${COMPOSE_TARGET}"

# ─── SSH config ──────────────────────────────────────────────────────────────
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p "$REMOTE_SSH_PORT")
RSYNC_SSH="ssh ${SSH_OPTS[*]} -i $REMOTE_SSH_KEY"

run_ssh() {
  ssh "${SSH_OPTS[@]}" -i "$REMOTE_SSH_KEY" "${REMOTE_USER}@${REMOTE_HOST}" "$@"
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
  ".env.test"
  ".env.staging"
  ".env.production"
  ".DS_Store"
  "*.egg-info"
  ".opencode/"
  "opencode.json"
  "opencode.jsonc"
  "AGENTS.md"
  "*.swp"
  "*~"
  "docker-compose.yml"
)

# Exclude compose files NOT being deployed
for f in docker-compose.test.yml docker-compose.staging.yml docker-compose.prod.yml; do
  if [[ "$f" != "$COMPOSE_SOURCE" ]]; then
    EXCLUDES+=("$f")
  fi
done

# Build rsync exclude args
EXCLUDE_ARGS=()
for exc in "${EXCLUDES[@]}"; do
  EXCLUDE_ARGS+=("--exclude=$exc")
done

# ─── Step 1: Validate ───────────────────────────────────────────────────────
info "=== Step 1: Validate ==="

for cmd in rsync ssh; do
  command -v "$cmd" >/dev/null 2>&1 || die "$cmd is not installed locally"
done

info "Testing SSH connection to ${REMOTE_USER}@${REMOTE_HOST}..."
if ! ssh "${SSH_OPTS[@]}" -i "$REMOTE_SSH_KEY" "${REMOTE_USER}@${REMOTE_HOST}" "echo 'Connected'" >/dev/null 2>&1; then
  die "Cannot connect to ${REMOTE_USER}@${REMOTE_HOST} via SSH"
fi
success "SSH connection successful"

info "Checking Docker on VM..."
if ! run_ssh "docker version >/dev/null 2>&1"; then
  die "Docker is not installed or not running on the VM"
fi
success "Docker is available on VM"

# ─── Step 2: Sync ───────────────────────────────────────────────────────────
info "=== Step 2: Sync files via rsync ==="
info "Source:      $(pwd)/"
info "Destination: ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/"
info "Compose:     ${COMPOSE_SOURCE} → ${COMPOSE_TARGET}"

if $DRY_RUN; then
  info "DRY RUN — would run:"
  echo "rsync -azvh --delete -e \"$RSYNC_SSH\" ${EXCLUDE_ARGS[@]} --dry-run ./ ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/"
  rsync -azvh --delete -e "$RSYNC_SSH" "${EXCLUDE_ARGS[@]}" --dry-run ./ "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/"
  success "Dry run complete"
  exit 0
fi

run_ssh "mkdir -p ${REMOTE_PATH}"

rsync -azvh --delete -e "$RSYNC_SSH" "${EXCLUDE_ARGS[@]}" ./ "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/"
success "Files synced to ${REMOTE_PATH}"

# ─── Step 2b: Provision .env ────────────────────────────────────────────────
if ! run_ssh "test -f ${REMOTE_PATH}/.env"; then
  if [[ -f "$PROJECT_ROOT/.env.template" ]]; then
    info "Provisioning .env from .env.template..."
    rsync -avz -e "$RSYNC_SSH" --include=".env.template" --exclude="*" "$PROJECT_ROOT/" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/"
    run_ssh "cp ${REMOTE_PATH}/.env.template ${REMOTE_PATH}/.env"
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
info "Building images with ${COMPOSE_TARGET}..."

if run_ssh "cd ${REMOTE_PATH} && docker compose -f ${COMPOSE_TARGET} build --no-cache"; then
  success "Docker images built successfully"
else
  die "Docker build failed"
fi

# ─── Step 4: Migrate ────────────────────────────────────────────────────────
if [[ "$DB_MIGRATE" == "true" ]]; then
  info "=== Step 4: Alembic migrations ==="

  info "Starting database container..."
  run_ssh "cd ${REMOTE_PATH} && docker compose -f ${COMPOSE_TARGET} up -d db"

  info "Waiting for database to be ready..."
  for i in $(seq 1 90); do
    if run_ssh "cd ${REMOTE_PATH} && docker compose -f ${COMPOSE_TARGET} exec -T db pg_isready >/dev/null 2>&1"; then
      success "Database is ready"
      break
    fi
    if [[ $i -eq 90 ]]; then
      die "Database failed to start within 90 seconds"
    fi
    sleep 1
  done

  info "Running Alembic migrations..."
  if run_ssh "cd ${REMOTE_PATH} && docker compose -f ${COMPOSE_TARGET} exec -T api alembic upgrade head"; then
    success "Migrations applied"
  else
    warn "Migration failed — check container logs"
  fi
else
  info "=== Step 4: Skipping migrations (--no-migrate) ==="
fi

# ─── Step 5: Start services ─────────────────────────────────────────────────
info "=== Step 5: Start services ==="

if run_ssh "cd ${REMOTE_PATH} && docker compose -f ${COMPOSE_TARGET} up -d"; then
  success "Services started"
else
  die "Failed to start services"
fi

# ─── Step 6: Health check ───────────────────────────────────────────────────
info "=== Step 6: Health check ==="
info "Polling http://${REMOTE_HOST}:${HEALTH_CHECK_PORT}/health (timeout: ${HEALTH_CHECK_TIMEOUT}s)..."

HEALTH_URL="http://${REMOTE_HOST}:${HEALTH_CHECK_PORT}/health"
for i in $(seq 1 $HEALTH_CHECK_TIMEOUT); do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo "000")
  if [[ "$HTTP_CODE" == "200" ]]; then
    success "Health check passed (HTTP $HTTP_CODE)"
    success "${ENVIRONMENT^} deployment complete!"
    info "API: $HEALTH_URL"
    exit 0
  fi
  if [[ $i -eq $HEALTH_CHECK_TIMEOUT ]]; then
    die "Health check timed out after ${HEALTH_CHECK_TIMEOUT}s (last HTTP: $HTTP_CODE)"
  fi
  sleep 1
done
