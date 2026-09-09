"""The database engine, sized and bounded.

The engine used to be `create_engine(url, pool_pre_ping=True)` and nothing
else, which means SQLAlchemy's defaults: pool_size=5, max_overflow=10, and —
the part that matters — no connect timeout at all. Postgres is Neon, reached
over the internet from EC2, so "no connect timeout" means a request that hits
a blackholed TCP path occupies its worker thread until something else kills it.

The numbers below are derived from what actually contends for a connection,
not picked to look generous:

  * ONE uvicorn worker (see the Dockerfile CMD), so this pool is not
    multiplied across processes.
  * FastAPI runs `def` endpoints in AnyIO's worker threadpool, and each one
    holds a Session for the life of the request. That threadpool defaults to 40
    threads, i.e. 40 potential concurrent connections against a 15-connection
    pool — so the 16th request would queue for pool_timeout (30s by default)
    and then fail. main.py caps the threadpool to THREADPOOL_LIMIT to make the
    two agree.
  * The scheduler's blocking work runs in two small executors (2 + 1 threads,
    core/scheduler.py), so background demand is 3, not 8 — the eight loops are
    asyncio tasks that spend nearly all their time idle.

POOL_SIZE + MAX_OVERFLOW is therefore set just above THREADPOOL_LIMIT + 3, so a
connection is available whenever a thread is; POOL_TIMEOUT then stops being a
queue and starts being an alarm.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

# Ceiling on FastAPI's worker threads for `def` endpoints. Applied in main.py's
# lifespan; defined here because it is one half of the pool arithmetic above.
THREADPOOL_LIMIT = 24

POOL_SIZE = 10
MAX_OVERFLOW = 20  # 30 total, against 24 request threads + 3 scheduler threads

# Fail fast instead of hanging. With the sizing above a wait here should be
# impossible, so 10s is not a budget — it is how long the system takes to tell
# you the sizing assumption has been broken.
POOL_TIMEOUT_SECONDS = 10

# Neon suspends idle compute and drops the connections with it. pool_pre_ping
# already catches that at checkout; recycling on top of it means long-idle
# sockets are retired rather than proven dead one round trip at a time.
POOL_RECYCLE_SECONDS = 1800

CONNECT_TIMEOUT_SECONDS = 10

# A backstop against a single statement pinning a connection forever, not a
# tuning knob. Every query this app issues is an indexed select over a
# ~500-stock universe; the heavy work (pandas, scoring, backtests) happens in
# Python after the rows are fetched. Five minutes is far outside anything
# legitimate here and still bounds the pathological case.
STATEMENT_TIMEOUT_MS = 300_000

_engine_kwargs: dict = {
    "pool_pre_ping": True,
    "pool_size": POOL_SIZE,
    "max_overflow": MAX_OVERFLOW,
    "pool_timeout": POOL_TIMEOUT_SECONDS,
    "pool_recycle": POOL_RECYCLE_SECONDS,
}

# psycopg2-only options. Guarded on the driver rather than applied
# unconditionally, so pointing DATABASE_URL at something else (SQLite in a
# test, say) fails on the URL rather than on an unrecognised connect argument.
if settings.database_url.startswith("postgresql"):
    _engine_kwargs["connect_args"] = {
        "connect_timeout": CONNECT_TIMEOUT_SECONDS,
        "options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
        # Shows up in pg_stat_activity and Neon's dashboard, so a connection
        # leak can be attributed to this app rather than guessed at.
        "application_name": "equitylens-api",
    }

engine = create_engine(settings.database_url, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
