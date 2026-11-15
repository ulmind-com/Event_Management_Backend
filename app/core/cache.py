"""
High-Performance In-Memory TTL Cache with SingleFlight Thundering-Herd Protection
================================================================================
Designed for high-concurrency event platforms (4,000–5,000 concurrent attendees).
Ensures database read queries are deduplicated and served directly from memory in < 1ms.
"""

import time
import copy
import asyncio
import logging
from typing import Any, Callable, Dict, Optional, Tuple, Coroutine

logger = logging.getLogger("eventora.cache")


class FastCache:
    """
    Thread-safe, non-blocking in-memory TTL cache with SingleFlight lock deduplication.
    """
    _store: Dict[str, Tuple[float, Any]] = {}
    _in_flight: Dict[str, asyncio.Future] = {}
    _lock = asyncio.Lock()

    @classmethod
    def get(cls, key: str) -> Optional[Any]:
        """Retrieve item if present and not expired."""
        entry = cls._store.get(key)
        if not entry:
            return None
        expires_at, value = entry
        if time.time() > expires_at:
            cls._store.pop(key, None)
            return None
        # Return deepcopy to ensure mutations outside don't corrupt cached state
        return copy.deepcopy(value)

    @classmethod
    def set(cls, key: str, value: Any, ttl: int = 30) -> None:
        """Store item with TTL in seconds."""
        cls._store[key] = (time.time() + ttl, copy.deepcopy(value))

    @classmethod
    def delete(cls, prefix: str) -> int:
        """Delete all keys starting with prefix (e.g. 'homepage', 'events', 'competitions')."""
        to_del = [k for k in cls._store if k.startswith(prefix)]
        for k in to_del:
            cls._store.pop(k, None)
        return len(to_del)

    @classmethod
    def clear(cls) -> None:
        """Clear all cached entries."""
        cls._store.clear()

    @classmethod
    async def cached_or_fetch(
        cls,
        key: str,
        ttl: int,
        fetcher: Callable[[], Coroutine[Any, Any, Any]]
    ) -> Any:
        """
        SingleFlight pattern: If key is in cache, return immediately (< 0.5ms).
        If 1,000 concurrent requests arrive when key is missing, only 1 task runs fetcher(),
        and all 1,000 tasks receive the identical result simultaneously without hitting MongoDB.
        """
        # 1. Fast path: check cache
        cached = cls.get(key)
        if cached is not None:
            return cached

        # 2. Check if a fetch is already in flight
        async with cls._lock:
            cached = cls.get(key)
            if cached is not None:
                return cached

            if key in cls._in_flight:
                fut = cls._in_flight[key]
            else:
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                cls._in_flight[key] = fut
                # Launch single worker task
                asyncio.create_task(cls._execute_fetch(key, ttl, fetcher, fut))

        # Wait for the single in-flight future
        res = await asyncio.shield(fut)
        return copy.deepcopy(res)

    @classmethod
    async def _execute_fetch(
        cls,
        key: str,
        ttl: int,
        fetcher: Callable[[], Coroutine[Any, Any, Any]],
        fut: asyncio.Future
    ) -> None:
        try:
            val = await fetcher()
            cls.set(key, val, ttl=ttl)
            if not fut.done():
                fut.set_result(val)
        except Exception as e:
            if not fut.done():
                fut.set_exception(e)
            logger.warning(f"Cache fetch failed for {key}: {e}")
        finally:
            async with cls._lock:
                cls._in_flight.pop(key, None)
