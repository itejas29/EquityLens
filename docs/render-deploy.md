# Free deployment on Render (+ Neon + Upstash)

Keeps EquityLens running when your Mac is off. This is the ₹0/month path:
Render's free web service tier, Neon for Postgres, Upstash for Redis. The
tradeoff for free is a sleep/wake cycle on the backend — see the "Why this
shape" section before you commit to it.

## Why this shape

- **Render free web services sleep after 15 minutes with no inbound HTTP
  traffic**, and take about a minute to wake on the next request. That would
  silently reintroduce the exact bug this deployment exists to fix (system
  quiet -> pipeline runs missed) unless something keeps pinging it. Step 6
  sets up that ping.
- **Render's own free tier has no managed Postgres or Redis** (discontinued),
  which is why Neon and Upstash are separate signups.
- **Neon's free compute also scales to zero after 5 minutes idle.** During
  market hours the scheduler hits the DB constantly (every 10-60s) so it stays
  warm; outside market hours, the first query after a gap — the 09:15 signal
  job or the 20:00 incremental — pays a one-time wake latency, typically well
  under a second. Not a functional risk, just worth knowing about.
- **Upstash's free tier is 500K commands/month.** The two price-refresh loops
  together land in the low hundred-thousands a month at market-hours usage —
  comfortable headroom, not a hard limit you're likely to hit.
- Render is single-instance on the free plan, which this app requires anyway:
  the WebSocket connection manager and viewed-symbol tracking
  (`app/core/ws_manager.py`) are in-process state, not shared across replicas.
  Never scale this service to 2+ instances.

## 1. Push to GitHub

Render deploys from the connected repo (`itejas29/EquityLens`). Commit and
push the working tree — including `render.yaml`, the Dockerfile/config fixes,
and the two-tier price refresh work — before starting step 4.

## 2. Neon — free Postgres

1. [neon.tech](https://neon.tech) -> sign up -> New Project.
2. Copy the connection string it gives you (starts `postgres://` or
   `postgresql://` — either works, `app/core/config.py` normalizes it).
3. Keep this tab open; you'll paste it into Render in step 4.

## 3. Upstash — free Redis

1. [upstash.com](https://upstash.com) -> sign up -> Create Database.
2. Pick a region close to Render's (Oregon/US if you don't have a strong
   reason otherwise — matching regions cuts latency between services).
3. Copy the **TLS** connection string (`rediss://...`, not `redis://`).

## 4. Render — create the Blueprint

1. [dashboard.render.com](https://dashboard.render.com) -> New -> Blueprint.
2. Connect the `itejas29/EquityLens` repo. Render reads `render.yaml` at the
   root and proposes two services: `equitylens-backend` (Docker web service)
   and `equitylens-frontend` (static site).
3. When prompted for env vars, fill in:
   - `DATABASE_URL` -> the Neon connection string from step 2
   - `REDIS_URL` -> the Upstash `rediss://` string from step 3
   - Leave `CORS_ORIGINS`, `VITE_API_BASE_URL`, `VITE_WS_BASE_URL` blank —
     none of the three URLs they need exist yet.
4. Apply. `JWT_SECRET_KEY` is generated automatically (`generateValue: true`
   in the blueprint) — nothing to do there.
5. Wait for `equitylens-backend` to finish deploying, note its URL, e.g.
   `https://equitylens-backend.onrender.com`.

## 5. Wire the two services to each other

The frontend's API URL and the backend's CORS allowlist each need the other
service's URL, which is why this is a second pass rather than step 4.

1. **Frontend** (`equitylens-frontend` -> Environment):
   - `VITE_API_BASE_URL` = `https://equitylens-backend.onrender.com/api/v1`
   - `VITE_WS_BASE_URL` = `wss://equitylens-backend.onrender.com/api/v1`
     (`wss://`, not `ws://` — Render terminates TLS in front of the service)
   - Save; this triggers a rebuild since Vite bakes these in at build time.
2. Note the frontend's URL once it deploys, e.g.
   `https://equitylens-frontend.onrender.com`.
3. **Backend** (`equitylens-backend` -> Environment):
   - `CORS_ORIGINS` = `https://equitylens-frontend.onrender.com`
   - Save; the backend redeploys and will now accept the frontend's origin.

## 6. Keep the backend awake

Without this, the backend sleeps 15 minutes after the last request and the
09:15/20:00 jobs get skipped exactly like they did when the Mac was off.

1. [cron-job.org](https://cron-job.org) -> free account -> Create cronjob.
2. URL: `https://equitylens-backend.onrender.com/api/v1/health`
3. Interval: every 10 minutes (comfortably under Render's 15-minute window).
4. Save. `/api/v1/health` is a cheap DB+Redis ping — no heavy queries, safe
   to hit this often.

This keeps the process awake but does **not** guarantee zero gaps — a slow
ping or a Render restart can still land inside a scheduled job's window. It
is a large improvement over the Mac sleeping, not a formal uptime guarantee.

## 7. Verify

1. Open the frontend URL, log in.
2. Check `https://equitylens-backend.onrender.com/api/v1/health/pipeline` —
   should report current data, not stale.
3. Leave a tab open across a 09:15 or 20:00 IST boundary once and confirm the
   corresponding log line / DB row appears (same checks used throughout this
   project's development sessions).
