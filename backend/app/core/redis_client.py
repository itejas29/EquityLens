"""The Redis connection, with the timeouts a remote cache needs.

Redis is NOT local here — it is Upstash, reached over the public internet from
EC2. redis-py's defaults assume a socket on the same machine: socket_timeout
and socket_connect_timeout both default to None, which means *wait forever*.
A blackholed connection (dropped packets, a provider incident, a NAT table
eviction) does not raise; it hangs the calling thread until something else
kills it.

That matters more than it sounds. Every price read goes through this client,
and FastAPI runs `def` endpoints in a bounded worker threadpool. Enough hung
reads and the pool is exhausted — at which point the API stops answering
requests that never touch Redis at all, including the health check that would
have reported the problem.

So: bounded timeouts, a bounded pool, and periodic liveness checks on idle
connections. Callers in core/cache.py turn the resulting errors into cache
misses rather than 500s.
"""

import redis

from app.core.config import settings

# 2s to open a socket, 3s for a command. The slowest thing this cache is ever
# asked for is a ~500-symbol JSON blob; anything past 3s is a broken path, not
# a slow one, and the DB fallback beats waiting.
SOCKET_CONNECT_TIMEOUT_SECONDS = 2.0
SOCKET_TIMEOUT_SECONDS = 3.0

# The default pool is effectively unbounded (2**31). Bounded here so a burst
# cannot open more sockets than the provider will allow — Upstash's free tier
# caps concurrent connections, and hitting that cap fails every command rather
# than queueing. Sized above the real concurrency: FastAPI's worker threadpool
# plus the scheduler's 3 executor threads.
MAX_CONNECTIONS = 50

# Recheck a connection that has sat idle for 30s. Upstash drops idle
# connections server-side; without this the first command after an idle period
# fails on a socket the client still believes is open.
HEALTH_CHECK_INTERVAL_SECONDS = 30

redis_client = redis.Redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=SOCKET_CONNECT_TIMEOUT_SECONDS,
    socket_timeout=SOCKET_TIMEOUT_SECONDS,
    # Retry once on a timeout. A single retransmit covers the common transient;
    # more than one just multiplies the worst-case latency by the retry count.
    retry_on_timeout=True,
    health_check_interval=HEALTH_CHECK_INTERVAL_SECONDS,
    max_connections=MAX_CONNECTIONS,
)
