#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Pull-based deploy for an immutable image (runs on the target VM)
# =============================================================================
# Usage: IMAGE_TAG=sha-<commit> ./deploy-remote.sh
#
# Pulls the pinned image, runs migrations in a one-shot container, restarts the
# api service, health-gates, and rolls back to the previously active tag on
# failure. The runtime .env stays on the host and is never touched.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/docker-compose.yml" ]]; then
  cd "${SCRIPT_DIR}"
else
  cd "${SCRIPT_DIR}/.."
fi

IMAGE_TAG="${IMAGE_TAG:?IMAGE_TAG is required (e.g. sha-abc1234)}"
export IMAGE_TAG
COMPOSE="docker compose -f docker-compose.yml"
HEALTH_URL="http://localhost:8000/health"
BACKUP_DIR="pre_deploy_backups"
BACKUP_RETENTION=5

info()  { echo "[deploy] $*"; }
fail()  { echo "[deploy][ERROR] $*" >&2; }

PREV_TAG=""
if [[ -f .active_tag ]]; then
  PREV_TAG="$(cat .active_tag)"
fi
info "Deploying ${IMAGE_TAG} (previous: ${PREV_TAG:-none})"

# ── Pre-deploy database backup (best effort, container-side dump then copy) ──
mkdir -p "$BACKUP_DIR"
SAFE_TAG="${IMAGE_TAG//[^A-Za-z0-9_.-]/_}"
if $COMPOSE exec -T db pg_dump -Fc -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-dmo}" -f /tmp/predeploy.dump 2>/dev/null; then
  if $COMPOSE cp db:/tmp/predeploy.dump "${BACKUP_DIR}/pre_${SAFE_TAG}.dump" 2>/dev/null; then
    info "Pre-deploy backup: ${BACKUP_DIR}/pre_${SAFE_TAG}.dump"
  fi
  $COMPOSE exec -T db rm -f /tmp/predeploy.dump 2>/dev/null || true
else
  info "WARNING: pre-deploy backup skipped (db unavailable)"
fi
ls -1t "$BACKUP_DIR"/pre_*.dump 2>/dev/null | tail -n +$((BACKUP_RETENTION + 1)) | xargs -r rm -f || true

# ── Registry login (only needed while the GHCR package is private) ───────────
if [[ -n "${GHCR_TOKEN:-}" && -n "${GHCR_USER:-}" ]]; then
  echo "${GHCR_TOKEN}" | docker login ghcr.io -u "${GHCR_USER}" --password-stdin
fi

# ── Pull immutable image, migrate, restart api only ─────────────────────────
IMAGE_TAG="$IMAGE_TAG" $COMPOSE pull api
IMAGE_TAG="$IMAGE_TAG" $COMPOSE run --rm api alembic upgrade head
IMAGE_TAG="$IMAGE_TAG" $COMPOSE up -d --no-deps api

# ── Health gate ──────────────────────────────────────────────────────────────
info "Waiting for health..."
for _ in $(seq 1 60); do
  if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    echo "$IMAGE_TAG" > .active_tag
    info "Deploy OK: ${IMAGE_TAG}"
    exit 0
  fi
  sleep 2
done

fail "Health check failed after 120s"
if [[ -n "$PREV_TAG" ]]; then
  info "Rolling back to ${PREV_TAG}"
  IMAGE_TAG="$PREV_TAG" $COMPOSE up -d --no-deps api
fi
exit 1
