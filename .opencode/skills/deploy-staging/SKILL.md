---
name: deploy-staging
description: Upload project to staging Proxmox VM via rsync and run full deployment (docker build, DB migrate, service restart, health check). Use when the user says "deploy to staging", "push to staging", "upload to VM", or "staging deploy".
---

# Deploy to Staging Proxmox VM

Uploads the entire project to a staging Proxmox VM using rsync, then runs the full deployment pipeline. The VM runs Docker Compose with the same stack as production.

## Prerequisites

- SSH key-based access to the staging VM (no password prompt)
- Docker + Docker Compose installed on the VM
- `rsync` available locally

## Configuration

Set these environment variables or create `.env.staging` in project root:

| Variable | Default | Description |
|---|---|---|
| `STAGING_HOST` | `192.168.1.100` | VM IP or hostname |
| `STAGING_USER` | `deploy` | SSH username on VM |
| `STAGING_PATH` | `/opt/dmo-staging` | Destination directory on VM |
| `STAGING_SSH_KEY` | `~/.ssh/id_ed25519` | Path to SSH private key |
| `STAGING_SSH_PORT` | `22` | SSH port on VM |
| `STAGING_COMPOSE_FILE` | `docker-compose.prod.yml` | Docker Compose file to use |
| `STAGING_DB_MIGRATE` | `true` | Run Alembic migrations after deploy |

## Deployment Pipeline

The deploy script (`scripts/deploy-staging.sh`) executes these steps in order:

1. **Validate** — Check SSH connectivity and required tools
2. **Sync** — rsync project files to VM (excluding `.git`, `.venv`, `__pycache__`, `tests/`, `plans/`, `docs/`, `.env*`, etc.)
3. **Docker Build** — SSH into VM and run `docker compose build`
4. **Migrate** — SSH into VM and run Alembic migrations inside the api container
5. **Restart** — SSH into VM and run `docker compose up -d`
6. **Health Check** — Poll `http://$STAGING_HOST:8000/health` until 200 or timeout

## Usage

### Manual execution

```bash
# Option A: Set env vars directly
STAGING_HOST=192.168.1.100 STAGING_USER=deploy ./scripts/deploy-staging.sh

# Option B: Create .env.staging file (auto-loaded by script)
echo "STAGING_HOST=192.168.1.100" > .env.staging
echo "STAGING_USER=deploy" >> .env.staging
./scripts/deploy-staging.sh
```

### Dry run (rsync only, no deploy)

```bash
./scripts/deploy-staging.sh --dry-run
```

### Sync only (skip docker steps)

```bash
./scripts/deploy-staging.sh --sync-only
```

## rsync Exclusions

The following are excluded from upload:

- `.git/` — version control
- `.venv/` — local Python virtual environment
- `__pycache__/`, `*.pyc` — Python cache
- `.pytest_cache/`, `.ruff_cache/` — tool caches
- `tests/`, `loadtest/` — test directories
- `plans/` — planning documents
- `docs/` — generated documentation
- `.env`, `.env.*` — environment secrets
- `.DS_Store` — macOS metadata
- `*.egg-info` — Python package metadata
- `.opencode/` — opencode config
- `opencode.json`, `opencode.jsonc` — opencode config files
- `AGENTS.md` — agent instructions

## Troubleshooting

| Problem | Fix |
|---|---|
| SSH connection refused | Verify `STAGING_HOST` and `STAGING_SSH_PORT`. Check VM is running. |
| Permission denied | Ensure SSH key is added to VM's `~/.ssh/authorized_keys`. |
| rsync fails on large files | Add `--partial` flag for resumable transfers. |
| Docker build fails | Check VM disk space: `ssh $STAGING_USER@$STAGING_HOST df -h` |
| Health check times out | Check container logs: `ssh $STAGING_USER@$STAGING_HOST docker compose -f docker-compose.prod.yml logs --tail=50 api` |
| Migration fails | Verify DB is accessible from the container. Check `.env` on VM has correct `DATABASE_URL`. |

## Safety Notes

- The script uses `rsync --delete` to remove files on the VM that no longer exist locally. Use `--dry-run` to preview changes.
- Docker containers are stopped gracefully before restart. If health check fails, old containers are NOT removed, allowing rollback.
- The `.env` file on the VM is never overwritten by rsync (it's excluded). Manage VM secrets separately.
