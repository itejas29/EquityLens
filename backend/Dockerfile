FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Non-root. Nothing here needs privilege — uvicorn binds 8000, not a
# privileged port — and a container that trades, holds database credentials
# and reaches the public internet should not run as uid 0 because nobody said
# otherwise. A fixed uid rather than whatever useradd picks, so a bind-mounted
# volume's ownership is predictable.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

COPY --chown=appuser:appuser . .

# WORKDIR created /app as root before appuser existed, and COPY --chown only
# covers the files it copies, not the directory holding them. So /app stayed
# root-owned 755 and the non-root process could not create anything in it —
# which broke two real things: experiment_lock writes /app/.experiment.lock, and
# app/ml/train.py writes its joblib artifacts under /app/app/ml/artifacts.
# Found when the Phase 21 sweep failed to take its lock.
RUN chown appuser:appuser /app

USER appuser

EXPOSE 8000

# /api/v1/health, not /health/pipeline: it checks DB and Redis reachability
# without counting the universe, and it is one of the two endpoints that stay
# unauthenticated precisely so a probe can use them. This is what `docker ps`
# reads to show health — the EC2 deployment runs `docker run` directly rather
# than compose, so without this there is no health signal on the box at all.
HEALTHCHECK --interval=60s --timeout=10s --start-period=45s --retries=3 \
    CMD curl -fsS http://localhost:${PORT:-8000}/api/v1/health || exit 1

# $PORT is injected by Render (and most PaaS hosts) and varies per deploy;
# docker-compose never sets it, so 8000 stays the local default.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
