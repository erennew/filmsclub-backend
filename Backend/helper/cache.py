"""
Multi-layer caching implementations for FilmsClub.

Provides:
- LRUCache: Least Recently Used cache for hot data
- TTLCache: Time-To-Live cache for time-sensitive data
- cached: Decorator for easy function result caching
"""

import time
import asyncio
import hashlib
import json
from functools import wraps
from typing import Optional, Any, Callable, Dict
from collections import OrderedDict


class LRUCache:
    """
    Simple LRU (Least Recently Used) cache implementation.
    
    Automatically evicts oldest items when max size is reached.
    Thread-safe for async operations.
    """
    
    def __init__(self, maxsize: int = 1000):
        self.maxsize = maxsize
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._lock = asyncio.Lock()
    
    async def get(self, key: str) -> Optional[Any]:
        """Get item from cache. Returns None if not found."""
        async with self._lock:
            if key in self._cache:
                # Move to end (most recently used)
                self._cache.move_to_end(key)
                return self._cache[key]
            return None
    
    async def set(self, key: str, value: Any):
        """Set item in cache."""
        async with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = value
            if len(self._cache) > self.maxsize:
                # Remove oldest (first item)
                self._cache.popitem(last=False)
    
    async def delete(self, key: str) -> bool:
        """Delete item from cache. Returns True if existed."""
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False
    
    async def clear(self):
        """Clear all items from cache."""
        async with self._lock:
            self._cache.clear()
    
    async def keys(self) -> list:
        """Get all cache keys."""
        async with self._lock:
            return list(self._cache.keys())
    
    async def size(self) -> int:
        """Get current cache size."""
        async with self._lock:
            return len(self._cache)


class TTLCache:
    """
    Cache with automatic expiration based on time-to-live (TTL).
    
    Items expire after specified seconds and are automatically
    removed on next access attempt.
    """
    
    def __init__(self, default_ttl: int = 300):
        """
        Args:
            default_ttl: Default time-to-live in seconds
        """
        self.default_ttl = default_ttl
        self._cache: Dict[str, tuple] = {}  # key -> (value, expiry_time)
        self._lock = asyncio.Lock()
    
    async def get(self, key: str) -> Optional[Any]:
        """Get item if not expired."""
        async with self._lock:
            if key in self._cache:
                value, expiry = self._cache[key]
                if time.time() < expiry:
                    return value
                # Expired - clean up
                del self._cache[key]
            return None
    
    async def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """
        Set item with TTL.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds (uses default if not specified)
        """
        ttl = ttl or self.default_ttl
        expiry = time.time() + ttl
        async with self._lock:
            self._cache[key] = (value, expiry)
    
    async def delete(self, key: str) -> bool:
        """Delete item from cache."""
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False
    
    async def clear(self):
        """Clear all items."""
        async with self._lock:
            self._cache.clear()
    
    async def expire(self, key: str, ttl: int) -> bool:
        """Update expiration time for existing key."""
        async with self._lock:
            if key in self._cache:
                value, _ = self._cache[key]
                self._cache[key] = (value, time.time() + ttl)
                return True
            return False


def generate_cache_key(*args, **kwargs) -> str:
    """Generate a consistent cache key from function arguments."""
    # Create a deterministic string representation
    key_parts = [str(arg) for arg in args]
    key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
    key_string = "|".join(key_parts)
    
    # Use MD5 for fast hashing (not security-critical)
    return hashlib.md5(key_string.encode()).hexdigest()


def cached(
    cache_instance,
    key_func: Optional[Callable] = None,
    ttl: Optional[int] = None,
    prefix: str = ""
):
    """
    Decorator for caching function results.
    
    Args:
        cache_instance: LRUCache or TTLCache instance
        key_func: Optional custom key generation function
        ttl: TTL for TTLCache (ignored for LRUCache)
        prefix: Key prefix for namespacing
    
    Example:
        @cached(file_metadata_cache, ttl=300)
        async def get_file_properties(chat_id, message_id):
            # Expensive operation
            return result
    """
    def decorator(func: Callable):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Generate cache key
            if key_func:
                cache_key = f"{prefix}:{key_func(*args, **kwargs)}"
            else:
                cache_key = f"{prefix}:{func.__name__}:{generate_cache_key(*args, **kwargs)}"
            
            # Try cache first
            result = await cache_instance.get(cache_key)
            if result is not None:
                return result
            
            # Execute function
            result = await func(*args, **kwargs)
            
            # Cache result
            if isinstance(cache_instance, TTLCache):
                await cache_instance.set(cache_key, result, ttl)
            else:
                await cache_instance.set(cache_key, result)
            
            return result
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            # For sync functions, generate key and check cache
            if key_func:
                cache_key = f"{prefix}:{key_func(*args, **kwargs)}"
            else:
                cache_key = f"{prefix}:{func.__name__}:{generate_cache_key(*args, **kwargs)}"
            
            # Note: For sync functions, we need to use the cache's
            # internal dict directly since we can't use async methods
            # This is a simplified version - prefer async for caching
            return func(*args, **kwargs)
        
        # Return appropriate wrapper
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator


# Global cache instances for use across the application

# File metadata cache - stores Telegram file properties
# High hit rate since same files are accessed repeatedly
file_metadata_cache = LRUCache(maxsize=1000)

# Hot movies/TV shows cache - stores frequently accessed media documents
# Reduces database queries for popular content
hot_movies_cache = LRUCache(maxsize=500)

# ffprobe results cache - stores audio/subtitle track detection results
# Very expensive operation (5+ seconds), cache for 24 hours
ffprobe_cache = TTLCache(default_ttl=86400)

# Bot workload cache - stores current workload distribution
# Short TTL since workloads change frequently (5 seconds)
workload_cache = TTLCache(default_ttl=5)

# Search results cache - stores recent search queries
# Medium TTL since content changes (2 minutes)
search_cache = TTLCache(default_ttl=120)

# Trending data cache - stores trending movies/TV
# Short TTL for real-time updates (1 minute)
trending_cache = TTLCache(default_ttl=60)

# API response cache - general API responses
# Use Redis for distributed caching, but this is local fallback
api_cache = TTLCache(default_ttl=60)


# Cache statistics tracking (optional - for monitoring)
class CacheStats:
    """Simple cache statistics tracker."""
    
    def __init__(self):
        self.hits = 0
        self.misses = 0
        self._lock = asyncio.Lock()
    
    async def record_hit(self):
        async with self._lock:
            self.hits += 1
    
    async def record_miss(self):
        async with self._lock:
            self.misses += 1
    
    async def get_stats(self) -> Dict[str, int]:
        async with self._lock:
            total = self.hits + self.misses
            hit_rate = (self.hits / total * 100) if total > 0 else 0
            return {
                "hits": self.hits,
                "misses": self.misses,
                "total": total,
                "hit_rate_percent": round(hit_rate, 2)
            }
    
    async def reset(self):
        async with self._lock:
            self.hits = 0
            self.misses = 0


class VideoChunkCache:
    """
    Smart chunk cache for video streaming.
    
    Caches only high-value chunks to manage memory efficiently:
    - First chunks for instant video start
    - Recently accessed chunks for smooth seeking
    - Popular chunks across all videos (LRU eviction)
    
    Memory budget: ~200-600MB configurable (not full videos)
    """
    
    def __init__(
        self,
        hot_start_slots: int = 100,      # Number of videos to cache start chunks
        hot_start_size: int = 1_048_576,  # 1MB per video start
        lru_cache_size: int = 524_288_000,  # 500MB for hot chunks
        chunk_size: int = 65536  # 64KB chunks
    ):
        self.hot_start_slots = hot_start_slots
        self.hot_start_size = hot_start_size
        self.chunk_size = chunk_size
        
        # Hot start cache: file_id -> bytearray (first 1MB)
        self._hot_start_cache: Dict[str, bytearray] = {}
        
        # LRU cache for general chunks: (file_id, offset) -> bytes
        # Calculate max items based on chunk size
        max_lru_items = lru_cache_size // chunk_size
        self._lru_cache = LRUCache(maxsize=max_lru_items)
        
        # Active stream buffers: stream_id -> TTLCache of chunks
        self._active_streams: Dict[str, TTLCache] = {}
        
        self._lock = asyncio.Lock()
    
    async def get_chunk(self, file_id: str, offset: int, length: int) -> Optional[bytes]:
        """
        Get cached chunk if available.
        
        Args:
            file_id: Media file identifier
            offset: Byte offset in file
            length: Requested chunk length
            
        Returns:
            Cached chunk data or None if not cached
        """
        async with self._lock:
            # Check hot start cache (first 1MB)
            if offset < self.hot_start_size:
                hot_data = self._hot_start_cache.get(file_id)
                if hot_data:
                    start = offset
                    end = min(offset + length, len(hot_data))
                    return bytes(hot_data[start:end])
            
            # Check LRU cache for general chunks
            cache_key = f"{file_id}:{offset}"
            return await self._lru_cache.get(cache_key)
    
    async def cache_chunk(self, file_id: str, offset: int, data: bytes, is_start: bool = False):
        """
        Cache a chunk with intelligent eviction.
        
        Args:
            file_id: Media file identifier
            offset: Byte offset in file
            data: Chunk bytes to cache
            is_start: Whether this is a start chunk (higher priority)
        """
        async with self._lock:
            # Hot start chunks get priority caching
            if is_start or offset < self.hot_start_size:
                # Check if we need to evict
                if file_id not in self._hot_start_cache:
                    if len(self._hot_start_cache) >= self.hot_start_slots:
                        # Evict oldest (FIFO)
                        oldest_key = next(iter(self._hot_start_cache))
                        del self._hot_start_cache[oldest_key]
                
                # Initialize or extend hot start buffer
                if file_id not in self._hot_start_cache:
                    self._hot_start_cache[file_id] = bytearray()
                
                # Calculate where this chunk fits
                chunk_end = offset + len(data)
                current_len = len(self._hot_start_cache[file_id])
                
                if offset <= current_len:
                    # Extend the buffer
                    self._hot_start_cache[file_id].extend(data)
                    
                    # Trim to max size
                    if len(self._hot_start_cache[file_id]) > self.hot_start_size:
                        self._hot_start_cache[file_id] = self._hot_start_cache[file_id][:self.hot_start_size]
            
            # Also add to LRU cache for general access
            cache_key = f"{file_id}:{offset}"
            await self._lru_cache.set(cache_key, data)
    
    async def create_stream_buffer(self, stream_id: str, ttl: int = 300):
        """
        Create buffer for an active stream.
        
        Args:
            stream_id: Unique stream identifier
            ttl: Time-to-live in seconds for stream chunks
        """
        async with self._lock:
            self._active_streams[stream_id] = TTLCache(default_ttl=ttl)
    
    async def add_to_stream_buffer(self, stream_id: str, offset: int, data: bytes):
        """
        Add chunk to active stream buffer for rewinding.
        
        Args:
            stream_id: Stream identifier
            offset: Chunk offset
            data: Chunk bytes
        """
        async with self._lock:
            if stream_id in self._active_streams:
                await self._active_streams[stream_id].set(str(offset), data)
    
    async def get_from_stream_buffer(self, stream_id: str, offset: int) -> Optional[bytes]:
        """
        Get chunk from active stream buffer.
        
        Args:
            stream_id: Stream identifier
            offset: Chunk offset
            
        Returns:
            Chunk data if in stream buffer, None otherwise
        """
        async with self._lock:
            if stream_id in self._active_streams:
                return await self._active_streams[stream_id].get(str(offset))
            return None
    
    async def close_stream_buffer(self, stream_id: str):
        """
        Clean up stream buffer when streaming ends.
        
        Args:
            stream_id: Stream identifier to close
        """
        async with self._lock:
            if stream_id in self._active_streams:
                del self._active_streams[stream_id]
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        async with self._lock:
            hot_start_memory = sum(len(v) for v in self._hot_start_cache.values())
            return {
                "hot_start": {
                    "slots_used": len(self._hot_start_cache),
                    "slots_total": self.hot_start_slots,
                    "memory_mb": round(hot_start_memory / 1024 / 1024, 2)
                },
                "lru_cache": {
                    "items": await self._lru_cache.size(),
                    "max_items": self._lru_cache.maxsize
                },
                "active_streams": len(self._active_streams)
            }
    
    async def clear(self):
        """Clear all video chunk caches."""
        async with self._lock:
            self._hot_start_cache.clear()
            await self._lru_cache.clear()
            self._active_streams.clear()


# Global cache instances for use across the application

# File metadata cache - stores Telegram file properties
file_metadata_cache = LRUCache(maxsize=1000)

# Hot movies/TV shows cache - stores frequently accessed media documents
hot_movies_cache = LRUCache(maxsize=500)

# ffprobe results cache - stores audio/subtitle track detection results
ffprobe_cache = TTLCache(default_ttl=86400)

# Bot workload cache - stores current workload distribution
workload_cache = TTLCache(default_ttl=5)

# Search results cache - stores recent search queries
search_cache = TTLCache(default_ttl=120)

# Trending data cache - stores trending movies/TV
trending_cache = TTLCache(default_ttl=60)

# API response cache - general API responses
api_cache = TTLCache(default_ttl=60)

# Video chunk cache - smart chunk storage for streaming
video_chunk_cache = VideoChunkCache(
    hot_start_slots=100,
    hot_start_size=1_048_576,  # 1MB
    lru_cache_size=524_288_000,  # 500MB
    chunk_size=65536
)

# Global stats instance
cache_stats = CacheStats()


async def get_all_cache_stats() -> Dict[str, Any]:
    """Get statistics for all cache instances."""
    return {
        "file_metadata": {
            "size": await file_metadata_cache.size(),
            "maxsize": file_metadata_cache.maxsize
        },
        "hot_movies": {
            "size": await hot_movies_cache.size(),
            "maxsize": hot_movies_cache.maxsize
        },
        "video_chunks": await video_chunk_cache.get_stats(),
        "overall_stats": await cache_stats.get_stats()
    }


async def clear_all_caches():
    """Clear all in-memory caches. Useful for testing or admin operations."""
    await file_metadata_cache.clear()
    await hot_movies_cache.clear()
    await ffprobe_cache.clear()
    await workload_cache.clear()
    await video_chunk_cache.clear()
    await search_cache.clear()
    await trending_cache.clear()
    await api_cache.clear()
