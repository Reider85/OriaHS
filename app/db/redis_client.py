"""Async Redis client factory (embedding cache + Streams queue).

Only ``redis.asyncio`` is supported — never the sync client. The pool is
built lazily from ``app.config.RedisConfig`` so importing the module does not
open connections.
"""

from functools import lru_cache

import redis.asyncio as aioredis

from app.config import settings


@lru_cache
def get_redis_client() -> aioredis.Redis:
    """Return a process-wide async Redis client with a shared connection pool."""
    config = settings.redis
    pool = aioredis.ConnectionPool.from_url(
        config.dsn,
        max_connections=config.pool_size,
        decode_responses=False,
    )
    return aioredis.Redis(connection_pool=pool)


redis_client = get_redis_client()
