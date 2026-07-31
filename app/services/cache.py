"""Cache service with Redis backend and in-memory fallback."""

from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

import redis.asyncio as aioredis

from app.config import settings
from app.utils.logger import logger


class CacheBackend(ABC):
    @abstractmethod
    async def get(self, key: str) -> Optional[Any]: ...
    @abstractmethod
    async def set(self, key: str, value: Any, ttl: int = 300) -> None: ...
    @abstractmethod
    async def delete(self, key: str) -> None: ...
    @abstractmethod
    async def exists(self, key: str) -> bool: ...
    @abstractmethod
    async def close(self) -> None: ...


class RedisCache(CacheBackend):
    def __init__(self, url: str) -> None:
        self._redis = aioredis.from_url(url, decode_responses=True)
        logger.info("redis_cache_initialised", url=url)

    async def get(self, key: str) -> Optional[Any]:
        raw = await self._redis.get(key)
        return json.loads(raw) if raw else None

    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        await self._redis.set(key, json.dumps(value, default=str), ex=ttl)

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)

    async def exists(self, key: str) -> bool:
        return bool(await self._redis.exists(key))

    async def close(self) -> None:
        await self._redis.aclose()


class MemoryCache(CacheBackend):
    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = asyncio.Lock()
        logger.warning("memory_cache_fallback", msg="Redis not available — using in-memory cache")

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None: return None
            value, expires = entry
            if expires < time.time():
                del self._store[key]
                return None
            return value

    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        async with self._lock:
            self._store[key] = (value, time.time() + ttl)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def exists(self, key: str) -> bool:
        return await self.get(key) is not None

    async def close(self) -> None:
        async with self._lock:
            self._store.clear()


_cache: Optional[CacheBackend] = None

async def get_cache() -> CacheBackend:
    global _cache
    if _cache is not None: return _cache

    if settings.REDIS_URL:
        try:
            _cache = RedisCache(settings.REDIS_URL)
            await _cache.exists("__ping__")
        except Exception as exc:
            logger.error("redis_connection_failed", error=str(exc))
            _cache = MemoryCache()
    else:
        _cache = MemoryCache()
    return _cache

async def close_cache() -> None:
    global _cache
    if _cache is not None:
        await _cache.close()
        _cache = None
