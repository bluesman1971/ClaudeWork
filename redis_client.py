"""
redis_client.py — Shared Redis connection for Trip Master

Provides a single lazily-initialised Redis client used by:
  - app.py    (_session_store, _cache)
  - auth.py   (login rate limiter, per-user AI rate limiter)

Graceful degradation
--------------------
If REDIS_URL is not set, or if the Redis server is unreachable,
get_redis() returns None.  Every caller checks for None and falls
back to its own in-memory dict so the app continues to function
correctly in local development without a Redis instance.

Usage
-----
    from redis_client import get_redis

    r = get_redis()
    if r is not None:
        r.setex('mykey', 3600, 'value')
    else:
        # fall back to in-memory
        _local_store['mykey'] = 'value'
"""

import os
import logging

logger = logging.getLogger(__name__)

_redis_client = None          # module-level singleton
_redis_checked = False        # only attempt connection once per process


def get_redis():
    """
    Return a connected Redis client, or None if Redis is unavailable.

    The connection is established once per process and reused.
    Thread-safe: the redis-py client is thread-safe by default.
    """
    global _redis_client, _redis_checked

    if _redis_checked:
        return _redis_client

    _redis_checked = True
    url = os.getenv('REDIS_URL', '').strip()

    if not url:
        logger.info(
            "REDIS_URL not set — using in-memory fallbacks for session store, "
            "cache, and rate limiters. Set REDIS_URL for multi-worker safety."
        )
        return None

    try:
        import redis
        client = redis.Redis.from_url(
            url,
            decode_responses=True,   # always return str, never bytes
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        client.ping()                # fail fast if unreachable
        logger.info("Redis connected: %s", _redact_url(url))
        _redis_client = client
    except Exception as exc:
        logger.warning(
            "Redis unavailable (%s) — falling back to in-memory stores. "
            "Rate limiting and session continuity will be per-worker only.",
            exc,
        )
        _redis_client = None

    return _redis_client


def _redact_url(url: str) -> str:
    """Return the Redis URL with the password replaced by ***."""
    try:
        from urllib.parse import urlparse, urlunparse
        p = urlparse(url)
        if p.password:
            netloc = f"{p.username}:***@{p.hostname}" + (f":{p.port}" if p.port else "")
            return urlunparse(p._replace(netloc=netloc))
    except Exception:
        pass
    return url
