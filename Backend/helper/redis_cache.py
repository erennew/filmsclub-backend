"""
Redis-based distributed caching for FilmsClub API.

Provides Redis client wrapper with automatic connection management,
JSON serialization, and pattern-based invalidation.

Use this for multi-server deployments where in-memory caches
are not shared between instances.
"""

import json
import hashlib
from typing import Optional, Any, List
from functools import wraps
from Backend.logger import LOGGER

# Try to import redis - graceful fallback if not installed
try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    LOGGER.warning("Redis not installed. Redis caching will be disabled.")
    LOGGER.info("Install with: pip install redis")

# Import config for Redis settings
from Backend.config import Cache as CacheConfig


class RedisCache:
    """
    Async Redis cache client with JSON serialization.
    
    Features:
    - Automatic connection management
    - JSON serialization/deserialization
    - Pattern-based cache invalidation
    - TTL support
    - Graceful fallback when Redis unavailable
    """
    
    def __init__(self, redis_url: str = None):
        """
        Initialize Redis cache.
        
        Args:
            redis_url: Redis connection URL. If None, uses CacheConfig.REDIS_URL.
                      Format: redis://host:port/db or rediss://default:pass@host:port
                      Example: redis://localhost:6379/0
        """
        self.redis_url = redis_url or CacheConfig.REDIS_URL
        self._client = None
        self._connected = False
        self._available = REDIS_AVAILABLE and CacheConfig.ENABLE_REDIS
    
    async def connect(self) -> bool:
        """
        Connect to Redis server.
        
        Returns:
            True if connection successful, False otherwise
        """
        if not self._available:
            return False
        
        if self._connected:
            return True
        
        try:
            # redis.asyncio.from_url() auto-detects SSL/TLS from rediss:// scheme
            # For Upstash Redis, use: rediss://default:PASSWORD@host:port
            self._client = await redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                health_check_interval=30
            )
            # Test connection
            await self._client.ping()
            self._connected = True
            LOGGER.info(f"Redis connected to {self.redis_url}")
            return True
        except Exception as e:
            LOGGER.error(f"Redis connection failed: {e}")
            self._connected = False
            return False
    
    async def disconnect(self):
        """Close Redis connection."""
        if self._client and self._connected:
            await self._client.close()
            self._connected = False
            LOGGER.info("Redis disconnected")
    
    async def get(self, key: str) -> Optional[Any]:
        """
        Get value from Redis cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found/expired
        """
        if not await self.connect():
            return None
        
        try:
            data = await self._client.get(key)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            LOGGER.error(f"Redis GET error for key {key}: {e}")
            return None
    
    async def set(
        self,
        key: str,
        value: Any,
        ttl: int = 60,
        nx: bool = False
    ) -> bool:
        """
        Set value in Redis cache.
        
        Args:
            key: Cache key
            value: Value to cache (will be JSON serialized)
            ttl: Time-to-live in seconds
            nx: Only set if key doesn't exist (NX flag)
            
        Returns:
            True if successful, False otherwise
        """
        if not await self.connect():
            return False
        
        try:
            data = json.dumps(value, default=str)  # Handle non-serializable objects
            
            if nx:
                # Only set if not exists
                result = await self._client.setnx(key, data)
                if result:
                    await self._client.expire(key, ttl)
                return bool(result)
            else:
                await self._client.setex(key, ttl, data)
                return True
        except Exception as e:
            LOGGER.error(f"Redis SET error for key {key}: {e}")
            return False
    
    async def delete(self, key: str) -> bool:
        """
        Delete key from Redis.
        
        Args:
            key: Cache key to delete
            
        Returns:
            True if key existed and was deleted
        """
        if not await self.connect():
            return False
        
        try:
            result = await self._client.delete(key)
            return result > 0
        except Exception as e:
            LOGGER.error(f"Redis DELETE error for key {key}: {e}")
            return False
    
    async def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        if not await self.connect():
            return False
        
        try:
            result = await self._client.exists(key)
            return result > 0
        except Exception as e:
            LOGGER.error(f"Redis EXISTS error for key {key}: {e}")
            return False
    
    async def expire(self, key: str, ttl: int) -> bool:
        """Update TTL for existing key."""
        if not await self.connect():
            return False
        
        try:
            result = await self._client.expire(key, ttl)
            return result > 0
        except Exception as e:
            LOGGER.error(f"Redis EXPIRE error for key {key}: {e}")
            return False
    
    async def ttl(self, key: str) -> int:
        """Get remaining TTL for key in seconds. -1 if no TTL, -2 if not found."""
        if not await self.connect():
            return -2
        
        try:
            return await self._client.ttl(key)
        except Exception as e:
            LOGGER.error(f"Redis TTL error for key {key}: {e}")
            return -2
    
    async def keys(self, pattern: str) -> List[str]:
        """
        Get all keys matching pattern.
        
        WARNING: Use sparingly on production - O(N) operation.
        Prefer using SCAN for large datasets.
        """
        if not await self.connect():
            return []
        
        try:
            return await self._client.keys(pattern)
        except Exception as e:
            LOGGER.error(f"Redis KEYS error for pattern {pattern}: {e}")
            return []
    
    async def delete_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching pattern.
        
        Useful for cache invalidation (e.g., clear all trending data).
        
        Args:
            pattern: Redis key pattern (e.g., "api:trending:*")
            
        Returns:
            Number of keys deleted
        """
        if not await self.connect():
            return 0
        
        try:
            # Use SCAN to avoid blocking on large datasets
            deleted = 0
            cursor = 0
            
            while True:
                cursor, keys = await self._client.scan(cursor, match=pattern, count=100)
                if keys:
                    deleted += await self._client.delete(*keys)
                
                if cursor == 0:
                    break
            
            if deleted > 0:
                LOGGER.info(f"Redis deleted {deleted} keys matching pattern: {pattern}")
            return deleted
        except Exception as e:
            LOGGER.error(f"Redis delete_pattern error for {pattern}: {e}")
            return 0
    
    async def flushdb(self) -> bool:
        """Clear all keys in current database. DANGEROUS!"""
        if not await self.connect():
            return False
        
        try:
            await self._client.flushdb()
            LOGGER.warning("Redis FLUSHDB executed - all keys deleted!")
            return True
        except Exception as e:
            LOGGER.error(f"Redis FLUSHDB error: {e}")
            return False
    
    async def info(self) -> dict:
        """Get Redis server info."""
        if not await self.connect():
            return {}
        
        try:
            info = await self._client.info()
            return dict(info)
        except Exception as e:
            LOGGER.error(f"Redis INFO error: {e}")
            return {}
    
    async def health_check(self) -> dict:
        """Perform health check and return status."""
        try:
            if not self._available:
                return {
                    "status": "unavailable",
                    "available": False,
                    "connected": False,
                    "error": "Redis not installed"
                }
            
            connected = await self.connect()
            if not connected:
                return {
                    "status": "disconnected",
                    "available": True,
                    "connected": False,
                    "error": "Connection failed"
                }
            
            # Try ping
            await self._client.ping()
            
            info = await self.info()
            return {
                "status": "healthy",
                "available": True,
                "connected": True,
                "version": info.get("redis_version", "unknown"),
                "used_memory": info.get("used_memory_human", "unknown"),
                "connected_clients": info.get("connected_clients", 0)
            }
        except Exception as e:
            return {
                "status": "error",
                "available": self._available,
                "connected": False,
                "error": str(e)
            }


# Global Redis cache instance
redis_cache = RedisCache()


def generate_cache_key(*args, **kwargs) -> str:
    """Generate a deterministic cache key from function arguments."""
    key_parts = [str(arg) for arg in args]
    key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
    key_string = "|".join(key_parts)
    return hashlib.md5(key_string.encode()).hexdigest()


def cache_response(
    ttl: int = 60,
    key_prefix: str = "api",
    key_func: Optional[callable] = None,
    skip_headers: Optional[List[str]] = None
):
    """
    Decorator for caching FastAPI endpoint responses in Redis.
    
    Args:
        ttl: Time-to-live in seconds
        key_prefix: Key namespace prefix
        key_func: Optional custom key generation function
        skip_headers: Headers to ignore when generating cache key
        
    Example:
        @app.get("/api/trending")
        @cache_response(ttl=60, key_prefix="trending")
        async def get_trending():
            return await fetch_trending()
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Generate cache key
            if key_func:
                cache_key = f"{key_prefix}:{key_func(*args, **kwargs)}"
            else:
                # Use function name and arguments
                func_args = [str(a) for a in args if not hasattr(a, '__class__') or a.__class__.__name__ in ['int', 'str', 'float', 'bool']]
                func_kwargs = {k: str(v) for k, v in kwargs.items() if k not in (skip_headers or [])}
                key_hash = generate_cache_key(*func_args, **func_kwargs)
                cache_key = f"{key_prefix}:{func.__name__}:{key_hash}"
            
            # Try to get from cache
            cached = await redis_cache.get(cache_key)
            if cached is not None:
                LOGGER.debug(f"Redis cache hit: {cache_key}")
                return cached
            
            # Execute function
            result = await func(*args, **kwargs)
            
            # Cache result (only if it's a dict/list - not streaming responses)
            if isinstance(result, (dict, list)):
                await redis_cache.set(cache_key, result, ttl=ttl)
                LOGGER.debug(f"Redis cache set: {cache_key} (TTL: {ttl}s)")
            
            return result
        
        # Attach cache management methods to function
        wrapper._cache_key_prefix = key_prefix
        wrapper._cache_invalidate = lambda pattern=None: redis_cache.delete_pattern(
            pattern or f"{key_prefix}:{func.__name__}:*"
        )
        
        return wrapper
    return decorator


async def invalidate_cache_pattern(pattern: str) -> int:
    """
    Invalidate all cache entries matching pattern.
    
    Args:
        pattern: Redis key pattern (e.g., "api:trending:*")
        
    Returns:
        Number of entries invalidated
    """
    return await redis_cache.delete_pattern(pattern)


async def invalidate_endpoint_cache(endpoint_func):
    """
    Invalidate cache for a specific endpoint.
    
    Args:
        endpoint_func: The decorated endpoint function
        
    Example:
        await invalidate_endpoint_cache(get_trending_today_endpoint)
    """
    if hasattr(endpoint_func, '_cache_invalidate'):
        return await endpoint_func._cache_invalidate()
    
    # Fallback: try to infer pattern
    if hasattr(endpoint_func, '_cache_key_prefix'):
        pattern = f"{endpoint_func._cache_key_prefix}:{endpoint_func.__name__}:*"
        return await redis_cache.delete_pattern(pattern)
    
    LOGGER.warning(f"Cannot invalidate cache for {endpoint_func.__name__} - no cache metadata")
    return 0
