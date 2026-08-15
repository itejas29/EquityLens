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
curl -s http://localhost:8000/api/v1/health/pipeline | jq
```

## Daily Schedule (IST)

- **09:15** — Daily signals generated for the pre-market shortlist.
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
