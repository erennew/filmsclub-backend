import json
from os import getenv, path
from dotenv import load_dotenv
from Backend import LOGGER


load_dotenv(path.join(path.dirname(path.dirname(__file__)), "config.env"))
class Telegram:
    API_ID = int(getenv("API_ID", "0"))
    API_HASH = getenv("API_HASH", "")
    BOT_TOKEN = getenv("BOT_TOKEN", "")
    PORT = int(getenv("PORT", "8000"))
    BASE_URL = getenv("BASE_URL", "0.0.0.0").rstrip('/')
    AUTH_CHANNEL = [channel.strip() for channel in (getenv("AUTH_CHANNEL") or "").split(",") if channel.strip()]
    DATABASE = getenv("DATABASE", "").split(", ")
    TMDB_API = getenv("TMDB_API", "")
    IMDB_API = getenv("IMDB_API", "")
    UPSTREAM_REPO = getenv("UPSTREAM_REPO", "")
    UPSTREAM_BRANCH = getenv("UPSTREAM_BRANCH", "main")
    MULTI_CLIENT = getenv("MULTI_CLIENT", "False").lower() == "true"
    USE_CAPTION = getenv("USE_CAPTION", "False").lower() == "true"
    USE_TMDB = getenv("USE_TMDB", "False").lower() == "true"
    OWNER_ID = int(getenv("OWNER_ID", "5422223708"))
    USE_DEFAULT_ID = getenv("USE_DEFAULT_ID", None)


class Cache:
    """Caching configuration for multi-layer caching strategy."""
    
    # Redis configuration
    REDIS_URL = getenv("REDIS_URL", "redis://localhost:6379/0")
    ENABLE_REDIS = getenv("ENABLE_REDIS", "false").lower() == "true"
    
    # Cache TTL settings (in seconds)
    TRENDING_CACHE_TTL = int(getenv("TRENDING_CACHE_TTL", "60"))  # 1 minute
    SEARCH_CACHE_TTL = int(getenv("SEARCH_CACHE_TTL", "30"))  # 30 seconds
    MEDIA_INFO_CACHE_TTL = int(getenv("MEDIA_INFO_CACHE_TTL", "86400"))  # 24 hours
    MOVIE_DETAIL_CACHE_TTL = int(getenv("MOVIE_DETAIL_CACHE_TTL", "300"))  # 5 minutes
    TV_DETAIL_CACHE_TTL = int(getenv("TV_DETAIL_CACHE_TTL", "300"))  # 5 minutes
    
    # In-memory cache sizes
    FILE_METADATA_CACHE_SIZE = int(getenv("FILE_METADATA_CACHE_SIZE", "1000"))
    HOT_MOVIES_CACHE_SIZE = int(getenv("HOT_MOVIES_CACHE_SIZE", "500"))
    
    # Video chunk cache configuration
    ENABLE_VIDEO_CACHE = getenv("ENABLE_VIDEO_CACHE", "true").lower() == "true"
    VIDEO_CACHE_HOT_START_SLOTS = int(getenv("VIDEO_CACHE_HOT_START_SLOTS", "100"))
    VIDEO_CACHE_HOT_START_SIZE_MB = int(getenv("VIDEO_CACHE_HOT_START_SIZE_MB", "1"))
    VIDEO_CACHE_LRU_SIZE_MB = int(getenv("VIDEO_CACHE_LRU_SIZE_MB", "500"))
    VIDEO_CACHE_CHUNK_SIZE = int(getenv("VIDEO_CACHE_CHUNK_SIZE", "65536"))
    
    # Browser/HTTP cache headers (in seconds)
    BROWSER_CACHE_MAX_AGE = int(getenv("BROWSER_CACHE_MAX_AGE", "60"))
    
    # CDN/Edge cache TTL (for CloudFlare/CDN)
    CDN_CACHE_TTL = int(getenv("CDN_CACHE_TTL", "300"))  # 5 minutes for dynamic content
    CDN_STATIC_TTL = int(getenv("CDN_STATIC_TTL", "2592000"))  # 30 days for static assets
