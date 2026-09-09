"""Shared fixtures.

Two things every test here depends on and neither of which may reach a real
service: the database is SQLite in memory, and Redis is a fake. A test that
silently fell back to the configured DATABASE_URL/REDIS_URL would be running
against production, which is exactly the accident that
backend/test_momentum.py used to cause.
"""

import os
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Set before app.core.config is imported anywhere: Settings has no defaults for
# these three and constructing it without them raises at import time.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-not-used-outside-tests")

from app.core.database import Base  # noqa: E402

# Every model module, imported for its side effect of registering the table on
# Base.metadata. create_all() only builds tables it knows about, and it knows
# about a table only once its module has been imported — so without this, which
# tables exist depended on which test module happened to be collected first.
# The failure mode was "no such table: users" from a fixture, not from the code
# under test.
import app.models  # noqa: E402,F401


@pytest.fixture
def db_session():
    # In-memory SQLite, with one connection shared by every thread.
    #
    # StaticPool is load-bearing, not a tuning choice. SQLAlchemy's default for
    # sqlite://:memory: is SingletonThreadPool, which hands each thread its own
    # connection — and an in-memory database lives inside its connection, so a
    # second thread gets a second, EMPTY database. FastAPI runs `def` endpoints
    # in a worker thread, so every HTTP test failed with "no such table: users"
    # while the same models worked fine when called directly.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


# ------------------------------------------------------------- fake redis --

class FakeRedisError(Exception):
    """Stands in for redis.exceptions.RedisError in the failure fixtures."""


class FakePipeline:
    def __init__(self, store: "FakeRedis"):
        self._store = store
        self._ops: list = []

    def zadd(self, key, mapping):
        self._ops.append(("zadd", key, mapping))
        return self

    def zremrangebyscore(self, key, lo, hi):
        self._ops.append(("zrem", key, lo, hi))
        return self

    def zcard(self, key):
        self._ops.append(("zcard", key))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    def execute(self):
        self._store._maybe_fail()
        results = []
        for op in self._ops:
            kind, key = op[0], op[1]
            zset = self._store.zsets.setdefault(key, {})
            if kind == "zadd":
                zset.update(op[2])
                results.append(len(op[2]))
            elif kind == "zrem":
                lo, hi = op[2], op[3]
                for member in [m for m, score in zset.items() if lo <= score <= hi]:
                    del zset[member]
                results.append(0)
            elif kind == "zcard":
                results.append(len(zset))
            else:
                results.append(True)
        self._ops = []
        return results


class FakeRedis:
    """In-process Redis stand-in. `fail = True` makes every call raise."""

    def __init__(self):
        self.data: dict[str, str] = {}
        self.zsets: dict[str, dict] = {}
        self.fail = False

    def _maybe_fail(self):
        if self.fail:
            raise FakeRedisError("simulated redis outage")

    def get(self, key):
        self._maybe_fail()
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self._maybe_fail()
        self.data[key] = value
        return True

    def delete(self, key):
        self._maybe_fail()
        self.data.pop(key, None)
        return 1

    def ping(self):
        self._maybe_fail()
        return True

    def pipeline(self):
        return FakePipeline(self)


@pytest.fixture
def fake_redis(monkeypatch):
    """Replace the Redis client everywhere it was imported by name.

    Also swaps the RedisError the modules catch, so FakeRedisError is treated
    as a real Redis failure by the very `except` clauses under test.
    """
    from app.core import cache, rate_limit

    fake = FakeRedis()
    monkeypatch.setattr(cache, "redis_client", fake)
    monkeypatch.setattr(cache, "RedisError", FakeRedisError)
    monkeypatch.setattr(rate_limit, "redis_client", fake)
    monkeypatch.setattr(rate_limit, "RedisError", FakeRedisError)
    # The throttle is module state and would otherwise silence log assertions
    # in whichever test happened to run second.
    cache._last_logged.clear()
    return fake


# ---------------------------------------------------------------- test app --

@pytest.fixture
def client(db_session, fake_redis):
    """TestClient over the real app, with the real DB dependency overridden.

    Constructed WITHOUT `with`: entering the context manager runs the lifespan,
    which starts all eight scheduler loops. Tests must not do that.
    """
    from fastapi.testclient import TestClient

    from app.core.database import get_db
    from app.main import app

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(client, db_session):
    """A registered user's bearer token."""
    resp = client.post("/api/v1/auth/register", json={
        "name": "Audit", "email": "audit@example.com", "password": "correct-horse-battery",
    })
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@contextmanager
def redis_down(fake_redis):
    fake_redis.fail = True
    try:
        yield
    finally:
        fake_redis.fail = False
