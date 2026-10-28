# EquityLens Deployment Guide

The EquityLens production pipeline runs in Docker. This ensures it behaves identically on macOS and Windows, automatically restarts on reboot or crash, and maintains correct timestamps regardless of the host's local timezone.

## Setup & Startup

1. Copy the environment template:
   ```bash
   cp .env.example .env
   ```
2. Edit `.env` and set `JWT_SECRET_KEY` to a random string.
3. Start the stack:
   ```bash
   docker compose up -d
   ```

## Verifying the Pipeline

The pipeline health endpoint reports exactly what data is present and when the last jobs ran.

```bash
# /health/pipeline needs a bearer token — see the API reference in README.md.
curl -s http://localhost:8000/api/v1/health
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/health/pipeline | jq
```

## Production (single EC2 host, plain `docker run`)

Production does not use compose: a `backend` container and a `caddy` reverse
proxy share a Docker network, with secrets in an env file on the host. Deploy:

```bash
cd /opt/EquityLens && sudo git fetch origin && sudo git reset --hard origin/main
sudo docker build -t equitylens-backend:new ./backend
sudo docker tag equitylens-backend:latest equitylens-backend:rollback
sudo docker rm -f backend && sudo docker tag equitylens-backend:new equitylens-backend:latest
sudo docker run -d --name backend --restart always --network equitylens \
  --env-file /opt/equitylens-backend.env \
  --memory 1500m --memory-swap 1500m \
  equitylens-backend:latest
```

**`--memory` with an equal `--memory-swap` is load-bearing.** Without a limit
the container may take the whole host plus its swap file, so a slow memory leak
does not crash it — it swap-thrashes. `--restart always` only acts when the
process *exits*, and Docker's `unhealthy` status triggers nothing. That is how,
in October 2026, the backend sat swap-thrashing and unresponsive for weeks — no
shortlist was published between 2026-10-01 and 2026-10-22 — with nothing
restarting it. With swap disabled for the container, the same leak ends in an
OOM kill and an automatic restart within about two minutes. 1500m leaves the
remainder of the 2 GB host for the OS and Caddy.

Other rules: `docker restart` does not re-read `--env-file` (always `rm` +
`run`); migrations run on start, so a migration that cannot apply is a restart
loop; startup takes about two minutes to report healthy; keep the `:rollback`
tag until the new image is confirmed.

## Daily Schedule (IST)

- **09:15** — Daily signals generated for the pre-market shortlist. If the
  previous session's data is incomplete the loop retries, but only until the
  15:30 close; a day still unpublished then is left as a gap, never published
  after the session it is dated for.
- **09:15 - 15:40** — Live prices updated every minute (WebSocket/REST).
- **20:00** — Incremental price ingestion fetches any new bars since the last run.

## Weekly / Monthly Schedule

- **Saturday 20:00** — Full universe rebuild (liquidity screen + new listings).
- **1st of Month 21:00** — Fundamentals refresh (P/E, P/B, ROE, etc.).

## Backups and Migration

To move the system from your Mac to your Windows PC:

1. **Mac (Backup):**
   ```bash
   ./scripts/backup.sh ./backups/
   ```
2. Move the `.sql.gz` file and the entire `EquityLens` folder to your Windows PC.
3. **Windows (Restore):**
   Ensure Docker Desktop is running, then:
   ```powershell
   cd EquityLens
   docker compose up -d
   ./scripts/restore.sh backups/equitylens_YYYYMMDD_HHMMSS.sql.gz
   ```

*Note: The Redis cache is deliberately not backed up. It will rebuild automatically.*
