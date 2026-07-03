---
name: deploy-staging
description: Upload project to any environment (test/staging/production) via rsync and run full deployment (docker build, DB migrate, service restart, health check). Use when the user says "deploy", "push to staging", "upload to VM", or "deploy to prod".
---

# Deploy to VM (Test / Staging / Production)

Uploads the project to a target VM using rsync, deploys the environment-specific Docker Compose file as `docker-compose.yml`, then runs the full deployment pipeline.

## Prerequisites

- SSH key-based access to the target VM (no password prompt)
- Docker + Docker Compose installed on the VM
- `rsync` available locally

## Configuration

Create environment config files in project root:

| File | Variables | Target |
|------|-----------|--------|
| `.env.test` | `TEST_HOST`, `TEST_USER`, `TEST_PATH`, `TEST_SSH_KEY` | Test VM |
| `.env.staging` | `STAGING_HOST`, `STAGING_USER`, `STAGING_PATH`, `STAGING_SSH_KEY` | Staging VM |
| `.env.production` | `PROD_HOST`, `PROD_USER`, `PROD_PATH`, `PROD_SSH_KEY` | Production VM |

## Environment-Specific Compose Files

Each environment gets its own compose file with appropriate resource limits. On deploy, the correct file is copied as `docker-compose.yml` on the target VM.

| Source File | Deployed As | Resources |
|-------------|-------------|-----------|
| `docker-compose.test.yml` | `docker-compose.yml` | 4 CPU / 4 GB RAM |
| `docker-compose.staging.yml` | `docker-compose.yml` | 4 CPU / 3 GB RAM |
| `docker-compose.prod.yml` | `docker-compose.yml` | 16 CPU / 8 GB RAM |

## Deployment Pipeline

The deploy script (`scripts/deploy.sh`) executes these steps in order:

1. **Validate** — Check SSH connectivity and required tools
2. **Sync** — rsync project files to VM (excluding `.git`, `.venv`, `__pycache__`, `.env*`, non-target compose files)
3. **Compose setup** — Copy environment-specific compose file as `docker-compose.yml`
4. **Docker Build** — SSH into VM and run `docker compose build`
5. **Migrate** — SSH into VM and run Alembic migrations inside the api container
6. **Restart** — SSH into VM and run `docker compose up -d`
7. **Health Check** — Poll `http://$HOST:8000/health` until 200 or timeout

## Usage

```bash
# Deploy to test VM
./scripts/deploy.sh --test

# Deploy to staging VM
./scripts/deploy.sh --staging

# Deploy to production VM
./scripts/deploy.sh --prod

# Dry run (preview rsync changes)
./scripts/deploy.sh --prod --dry-run

# Sync only (skip Docker steps)
./scripts/deploy.sh --prod --sync-only

# Skip migrations
./scripts/deploy.sh --prod --no-migrate
```

## rsync Exclusions

The following are excluded from upload:

- `.git/` — version control
- `.venv/` — local Python virtual environment
- `__pycache__/`, `*.pyc` — Python cache
- `.pytest_cache/`, `.ruff_cache/` — tool caches
- `loadtest/` — load test scripts
- `plans/` — planning documents
- `docs/` — generated documentation
- `backups/` — database backups
- `.env`, `.env.*` — environment secrets
- `docker-compose.yml` — local dev compose file
- Non-target compose files (e.g., `docker-compose.prod.yml` when deploying to staging)
- `.DS_Store` — macOS metadata
- `*.egg-info` — Python package metadata
- `.opencode/` — opencode config
- `opencode.json`, `opencode.jsonc` — opencode config files
- `AGENTS.md` — agent instructions

## Troubleshooting

| Problem | Fix |
|---|---|
| SSH connection refused | Verify host IP and SSH port. Check VM is running. |
| Permission denied | Ensure SSH key is added to VM's `~/.ssh/authorized_keys`. |
| rsync fails on large files | Add `--partial` flag for resumable transfers. |
| Docker build fails | Check VM disk space: `ssh user@host df -h` |
| Health check times out | Check container logs: `ssh user@host docker compose -f docker-compose.yml logs --tail=50 api` |
| Migration fails | Verify DB is accessible from the container. Check `.env` on VM has correct `DATABASE_URL`. |

## Safety Notes

- The script uses `rsync --delete` to remove files on the VM that no longer exist locally. Use `--dry-run` to preview changes.
- Docker containers are stopped gracefully before restart. If health check fails, old containers are NOT removed, allowing rollback.
- The `.env` file on the VM is never overwritten by rsync (it's excluded). Manage VM secrets separately.
- Each environment deploys its own compose file — deploying to one environment never affects another.
