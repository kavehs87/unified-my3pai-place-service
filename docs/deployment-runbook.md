# Production deployment runbook (immutable images → GHCR)

## Model

Commits to `main` are tested in CI, built once, and pushed to GHCR as an
immutable tag (`ghcr.io/kavehs87/unified-my3pai-place-service:sha-<commit>`).
Production never builds: it pulls the exact image CI produced, so what was
tested is what runs. The image tag is the commit SHA — rollback means deploying
an older tag.

```
push to main ──▶ CI: lint + tests ──▶ build & push image ──▶ (manual, approved)
                                                    │
                                                    ▼
                          deploy workflow ──▶ VM: pull → migrate → restart api
                                                 → health gate → record active tag
```

## Secrets (GitHub environment `production`)

All values live in the `production` environment; nothing sensitive is committed.
The repository is public — never put hosts, ports, keys, or passwords in files.

| Secret | Meaning |
|---|---|
| `DEPLOY_HOST` | production hostname/IP |
| `DEPLOY_PORT` | SSH port |
| `DEPLOY_USER` | SSH user |
| `DEPLOY_SSH_KEY` | SSH private key allowed to log in to the VM |

Runtime secrets (DB/Redis passwords, API key, admin credentials) and
runtime-only feature flags (e.g. `MCP_ENABLED`, `MCP_ALLOWED_HOSTS`) stay only
in `/root/ups/.env` on the VM (mode `600`), which the deploy never overwrites.
Do not move these into `docker-compose.prod.yml` or the workflows: the compose
file is replaced from the repo on every deploy, the `.env` is not.

GHCR images are pulled anonymously (package visibility: public — the image
contains source code only, no secrets). To switch to a private package, add
`GHCR_USER`/`GHCR_TOKEN` (read:packages) environment secrets; the deploy script
logs in when both are present.

## Deploying

1. Pick the tag: `sha-<full commit SHA>` (from the CI run that built it).
2. GitHub → Actions → **Deploy production** → *Run workflow* with that tag.
3. The `production` environment gate requires approval.
4. The job uploads `scripts/deploy-remote.sh` + `docker-compose.prod.yml`,
   copies the compose file to `docker-compose.yml`, and runs the deploy:
   - best-effort `pg_dump` snapshot into `/root/ups/pre_deploy_backups/` (last 5 kept)
   - `docker compose pull api`
   - `docker compose run --rm api alembic upgrade head` (single migration step)
   - `docker compose up -d --no-deps api` (db/redis untouched)
   - polls `/health` for up to 120 s, then writes `/root/ups/.active_tag`
   - on failure: restarts the previous `.active_tag` and exits non-zero

CLI equivalent:

```bash
gh workflow run deploy.yml -f image_tag=sha-<commit>
gh run watch
```

## Rollback

Redeploy the previous tag (from `/root/ups/.active_tag` on the VM) with the same
workflow. Because migrations are additive, the previous image runs against the
new schema.

## Migration discipline

- Migrations must be **expand/contract**: add columns/tables first, backfill,
  switch reads/writes, drop old structures in a later release.
- Exactly one migration step per deploy (the one-shot container); do not rely on
  container start-up ordering.
- Take/verify a backup before schema-changing releases (`db-backup.sh` from a
  workstation also works).

## What is intentionally NOT automated yet

- **TLS/reverse proxy**: only `8000` (API) is published; db/redis publish
  nothing after the next full `docker compose up -d`. Put Caddy/Traefik on
  `80/443` in front and stop publishing `8000` (already removed from db/redis
  in `docker-compose.prod.yml`; applies when the full stack is next recreated).
- **Offsite backups**: `pre_deploy_backups/` is local to the VM. Add scheduled
  `pg_dump`/`pgBackRest` to object storage plus periodic restore drills.
- **Image signing/SBOM**: Trivy scan currently reports without failing; add
  cosign keyless signing and tighten the scan gate when ready.

## Ops notes

- `scripts/deploy.sh --prod` (rsync + build on server) is superseded; do not use
  it for production anymore.
- `docker-compose.prod.yml` requires `IMAGE_TAG`; compose fails fast without it.
- Health: `GET /health` (DB + Redis, 1.5 s per component).
