import json
from os import getenv, path
from dotenv import load_dotenv
from Backend import LOGGER


# Try loading from config.env first, then fall back to .env (for Heroku compatibility)
# Heroku env vars take precedence over file-based config
_config_env_path = path.join(path.dirname(path.dirname(__file__)), "config.env")
_env_path = path.join(path.dirname(path.dirname(__file__)), ".env")

if path.exists(_config_env_path):
    load_dotenv(_config_env_path, override=False)  # Don't override Heroku env vars
    LOGGER.info(f"Loaded config from {_config_env_path}")
elif path.exists(_env_path):
    load_dotenv(_env_path, override=False)
    LOGGER.info(f"Loaded config from {_env_path}")
else:
    LOGGER.warning("No config.env or .env file found. Using environment variables directly.")
class Telegram:
    API_ID = int(getenv("API_ID", "0"))
    API_HASH = getenv("API_HASH", "")
    BOT_TOKEN = getenv("BOT_TOKEN", "")
    PORT = int(getenv("PORT", "8000"))
    BASE_URL = getenv("BASE_URL", "0.0.0.0").rstrip('/')
    AUTH_CHANNEL = [channel.strip() for channel in (getenv("AUTH_CHANNEL") or "").split(",") if channel.strip()]
    # Handle DATABASE - strip quotes that might come from config.env
    _db_raw = getenv("DATABASE", "")
    if _db_raw:
        _db_raw = _db_raw.strip().strip('"').strip("'")
    DATABASE = _db_raw if _db_raw else ""
    
    @staticmethod
    def validate():
        """Validate critical config values are set."""
        missing = []
        if not Telegram.API_ID or Telegram.API_ID == 0:
            missing.append("API_ID")
        if not Telegram.API_HASH:
            missing.append("API_HASH")
        if not Telegram.BOT_TOKEN:
            missing.append("BOT_TOKEN")
        if not Telegram.DATABASE:
            missing.append("DATABASE")
        if missing:
            LOGGER.error(f"Missing required environment variables: {', '.join(missing)}")
            LOGGER.error("Please set them via: heroku config:set VAR_NAME=value -a your-app-name")
            raise ValueError(f"Missing required config: {', '.join(missing)}")
    TMDB_API = getenv("TMDB_API", "")
    IMDB_API = getenv("IMDB_API", "")
    UPSTREAM_REPO = getenv("UPSTREAM_REPO", "")
    UPSTREAM_BRANCH = getenv("UPSTREAM_BRANCH", "main")
    MULTI_CLIENT = getenv("MULTI_CLIENT", "False").lower() == "true"
    USE_CAPTION = getenv("USE_CAPTION", "False").lower() == "true"
    USE_TMDB = getenv("USE_TMDB", "False").lower() == "true"
    OWNER_ID = int(getenv("OWNER_ID", "5422223708"))
    USE_DEFAULT_ID = getenv("USE_DEFAULT_ID", None)
    ADMIN_IDS = [
        int(admin_id.strip())
        for admin_id in (
            (getenv("ADMIN_IDS") or str(OWNER_ID)) + ",1047253913"
        ).split(",")
        if admin_id.strip().isdigit()
    ]
    UPDATE_CHANNEL = getenv("UPDATE_CHANNEL", "")
    TG_USERNAME = getenv("TG_USERNAME", "")
    WEBSITE_URL = getenv("WEBSITE_URL", "https://teluguflix-two.vercel.app/").rstrip('/')


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


class QueueConfig:
    """Queue processing configuration - SLOW & THOROUGH mode."""
    
    # Processing delays (slower = more reliable)
    FILE_QUEUE_DELAY = int(getenv("FILE_QUEUE_DELAY", "8"))  # 8 seconds between files
    VALIDATION_DELAY = int(getenv("VALIDATION_DELAY", "15"))  # 15 seconds between retries
    FILE_VALIDATION_DELAY = int(getenv("FILE_VALIDATION_DELAY", "5"))  # 5 sec after fetch
    
    # Batch gap (after every 10 files, wait 5 seconds)
    BATCH_SIZE = int(getenv("BATCH_SIZE", "10"))  # Files per batch
    BATCH_GAP_SECONDS = int(getenv("BATCH_GAP_SECONDS", "5"))  # Gap between batches
    
    # Retry limits
    MAX_RETRY_COUNT = int(getenv("MAX_RETRY_COUNT", "3"))
    QUEUE_WORKERS = int(getenv("QUEUE_WORKERS", "1"))  # Single worker for sequential
    
    # Rate limiting (per channel) - VERY conservative
    MAX_MESSAGES_PER_MINUTE = int(getenv("MAX_MESSAGES_PER_MINUTE", "5"))  # Only 5 per minute
    RATE_LIMIT_SECONDS = int(getenv("RATE_LIMIT_SECONDS", "60"))  # Over 60 seconds
    
    # TMDB Validation
    TMDB_VALIDATION_TIMEOUT = int(getenv("TMDB_VALIDATION_TIMEOUT", "10"))  # 10 sec timeout
    TMDB_CACHE_TTL = int(getenv("TMDB_CACHE_TTL", "3600"))  # Cache valid IDs for 1 hour


class TMDBValidation:
    """TMDB validation settings."""
    
    ENABLE_VALIDATION = getenv("ENABLE_TMDB_VALIDATION", "true").lower() == "true"
    CACHE_VALID_IDS = getenv("CACHE_TMDB_IDS", "true").lower() == "true"
    SKIP_ON_TIMEOUT = getenv("SKIP_TMDB_ON_TIMEOUT", "false").lower() == "true"  # Don't skip, wait


class MediaProbeConfig:
    """Media probing configuration."""
    
    # Probe settings
    ENABLE_MEDIA_PROBE = getenv("ENABLE_MEDIA_PROBE", "true").lower() == "true"
    MAX_PROBE_SIZE_MB = int(getenv("MAX_PROBE_SIZE_MB", "50"))  # Max MB to download for probing
    PROBE_TIMEOUT = int(getenv("PROBE_TIMEOUT", "30"))  # Timeout in seconds
    
    # Progressive probe steps
    PROBE_STEPS = [
        ("16KB", 16 * 1024),
        ("64KB", 64 * 1024),
        ("256KB", 256 * 1024),
        ("1MB", 1 * 1024 * 1024),
        ("4MB", 4 * 1024 * 1024),
        ("10MB", 10 * 1024 * 1024),
    ]


# Backward compatibility: Config class aliases Telegram
Config = Telegram
