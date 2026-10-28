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
    @staticmethod
    def _clean_uri(raw: str) -> str:
        return (raw or "").strip().strip('"').strip("'")

    _db_raw = _clean_uri(getenv("DATABASE", ""))
    DATABASE = _db_raw if _db_raw else ""

    # ── Multi-cluster storage (sharding across several free Atlas clusters) ──
    # DATABASE = cluster 1 (also the ops cluster: queue, views, failed_files...)
    # DATABASE_2..DATABASE_5 = additional media-only clusters.
    DATABASE_URIS: list = []
    if DATABASE:
        DATABASE_URIS.append(DATABASE)
    for _i in range(2, 6):
        _uri = _clean_uri(getenv(f"DATABASE_{_i}", ""))
        if _uri:
            DATABASE_URIS.append(_uri)
    DATABASE_CLUSTER_COUNT = len(DATABASE_URIS)

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
        if not Telegram.ADMIN_WEB_PASSWORD and not Telegram.ADMIN_WEB_PASSWORD_HASH:
            missing.append("ADMIN_WEB_PASSWORD (or ADMIN_WEB_PASSWORD_HASH)")
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
            (getenv("ADMIN_IDS") or str(OWNER_ID))
        ).split(",")
        if admin_id.strip().isdigit()
    ]
    ADMIN_WEB_USERNAME = getenv("ADMIN_WEB_USERNAME", "admin")
    ADMIN_WEB_PASSWORD = getenv("ADMIN_WEB_PASSWORD", "")
    ADMIN_WEB_PASSWORD_HASH = getenv("ADMIN_WEB_PASSWORD_HASH", "").strip()
    ADMIN_SESSION_SECRET = getenv("ADMIN_SESSION_SECRET", "").strip()
    ADMIN_SESSION_TTL = int(getenv("ADMIN_SESSION_TTL", "86400"))
    INTERNAL_API_KEY = getenv("INTERNAL_API_KEY", "").strip()
    UPDATE_CHANNEL = getenv("UPDATE_CHANNEL", "")
    TG_USERNAME = getenv("TG_USERNAME", "")
    WEBSITE_URL = getenv("WEBSITE_URL", "https://cloudxmovies.vercel.app/").rstrip('/')
    CORS_ORIGINS = [
        origin.strip()
        for origin in (
            getenv("CORS_ORIGINS")
            or "https://cloudxmovies.vercel.app,http://localhost:3000,http://localhost:5173"
        ).split(",")
        if origin.strip()
    ]


class Cache:
    """Caching configuration for multi-layer caching strategy."""
    
    # Redis configuration
    REDIS_URL = getenv("REDIS_URL", "redis://localhost:6379/0")
    ENABLE_REDIS = getenv("ENABLE_REDIS", "false").lower() == "true"
    
    # Cache TTL settings (in seconds)
    TRENDING_CACHE_TTL = int(getenv("TRENDING_CACHE_TTL", "160"))  # ~2.7 minutes
    SEARCH_CACHE_TTL = int(getenv("SEARCH_CACHE_TTL", "3000"))  # 50 minutes
    MEDIA_INFO_CACHE_TTL = int(getenv("MEDIA_INFO_CACHE_TTL", "864000"))  # 10 days
    MOVIE_DETAIL_CACHE_TTL = int(getenv("MOVIE_DETAIL_CACHE_TTL", "30000"))  # ~8.3 hours
    TV_DETAIL_CACHE_TTL = int(getenv("TV_DETAIL_CACHE_TTL", "30000"))  # ~8.3 hours
    
    # In-memory cache sizes
    FILE_METADATA_CACHE_SIZE = int(getenv("FILE_METADATA_CACHE_SIZE", "5000"))
    HOT_MOVIES_CACHE_SIZE = int(getenv("HOT_MOVIES_CACHE_SIZE", "500"))
    
    # Video chunk cache configuration.
    # Hot-start budget = slots * size, so the defaults stay at ~100 MB,
    # small enough for a 512 MB dyno.
    ENABLE_VIDEO_CACHE = getenv("ENABLE_VIDEO_CACHE", "true").lower() == "true"
    VIDEO_CACHE_HOT_START_SLOTS = int(getenv("VIDEO_CACHE_HOT_START_SLOTS", "100"))
    VIDEO_CACHE_HOT_START_SIZE_MB = int(getenv("VIDEO_CACHE_HOT_START_SIZE_MB", "1"))
    VIDEO_CACHE_LRU_SIZE_MB = int(getenv("VIDEO_CACHE_LRU_SIZE_MB", "256"))
    VIDEO_CACHE_CHUNK_SIZE = int(getenv("VIDEO_CACHE_CHUNK_SIZE", "1048576"))

    # Remuxed (audio/subtitle switched) file cache on disk.
    # Heroku dynos have ephemeral, small disks; 256 MB keeps the cache within
    # a 512 MB dyno while still covering a few in-flight sessions.
    REMUX_CACHE_SIZE_MB = int(getenv("REMUX_CACHE_SIZE_MB", "256"))
    REMUX_CACHE_TTL_HOURS = int(getenv("REMUX_CACHE_TTL_HOURS", "24"))
    REMUX_CACHE_DIR = getenv(
        "REMUX_CACHE_DIR",
        path.join(path.dirname(path.dirname(__file__)), ".cache", "remux"),
    )
    
    # Browser/HTTP cache headers (in seconds)
    BROWSER_CACHE_MAX_AGE = int(getenv("BROWSER_CACHE_MAX_AGE", "60"))
    
    # CDN/Edge cache TTL (for CloudFlare/CDN)
    CDN_CACHE_TTL = int(getenv("CDN_CACHE_TTL", "300"))  # 5 minutes for dynamic content
    CDN_STATIC_TTL = int(getenv("CDN_STATIC_TTL", "2592000"))  # 30 days for static assets


class QueueConfig:
    """Queue processing configuration - SLOW & THOROUGH mode with MongoDB persistence."""
    
    # Processing delays (slower = more reliable)
    FILE_QUEUE_DELAY = int(getenv("FILE_QUEUE_DELAY", "1"))  # 1 second between files
    VALIDATION_DELAY = int(getenv("VALIDATION_DELAY", "1"))  # 1 second between retries
    FILE_VALIDATION_DELAY = int(getenv("FILE_VALIDATION_DELAY", "1"))  # 1 second after fetch
    
    # Batch gap (after every N files, wait BATCH_GAP_SECONDS)
    BATCH_SIZE = int(getenv("BATCH_SIZE", "50"))  # Files per batch (small = less RAM/DB pressure)
    BATCH_GAP_SECONDS = int(getenv("BATCH_GAP_SECONDS", "2"))  # Gap between batches
    
    # Retry limits
    MAX_RETRY_COUNT = int(getenv("MAX_RETRY_COUNT", "3"))
    QUEUE_WORKERS = int(getenv("QUEUE_WORKERS", "2"))  # Parallel in-memory workers
    # Items claimed by a worker move to "processing"; a claim older than this
    # (e.g. the process crashed mid-file) is returned to "pending".
    QUEUE_CLAIM_TIMEOUT = int(getenv("QUEUE_CLAIM_TIMEOUT", "7200"))

    # Rate limiting (per channel)
    MAX_MESSAGES_PER_MINUTE = int(getenv("MAX_MESSAGES_PER_MINUTE", "300"))  # 300 per minute
    RATE_LIMIT_SECONDS = int(getenv("RATE_LIMIT_SECONDS", "60"))  # Per 60 seconds

    # TMDB Validation
    TMDB_VALIDATION_TIMEOUT = int(getenv("TMDB_VALIDATION_TIMEOUT", "7"))  # 7 second timeout
    TMDB_CACHE_TTL = int(getenv("TMDB_CACHE_TTL", "86400"))  # Cache valid IDs for 24 hours
    
    # MongoDB Queue Persistence
    ENABLE_MONGODB_QUEUE = getenv("ENABLE_MONGODB_QUEUE", "true").lower() == "true"
    QUEUE_TTL_DAYS = int(getenv("QUEUE_TTL_DAYS", "2"))  # Retain finished (failed) queue items for 2 days
    PROCESSED_TTL_DAYS = int(getenv("PROCESSED_TTL_DAYS", "3"))  # Keep processed records for 3 days
    RESUME_ON_START = getenv("RESUME_ON_START", "true").lower() == "true"  # Resume queue on restart
    # How often workers re-read the paused flag / pending channel list from Mongo.
    QUEUE_STATE_POLL_SECONDS = int(getenv("QUEUE_STATE_POLL_SECONDS", "5"))
    QUEUE_CHANNEL_REFRESH_SECONDS = int(getenv("QUEUE_CHANNEL_REFRESH_SECONDS", "30"))

    # Fair scheduling: live uploads are processed before rescan backfill.
    PRIORITY_LIVE = int(getenv("QUEUE_PRIORITY_LIVE", "0"))
    PRIORITY_RESCAN = int(getenv("QUEUE_PRIORITY_RESCAN", "1"))

    # Ingestion policy
    ENABLE_PACK_DETECTION = getenv("ENABLE_PACK_DETECTION", "true").lower() == "true"
    MAX_QUALITIES_PER_TITLE = int(getenv("MAX_QUALITIES_PER_TITLE", "6"))

    # FloodWait handling
    FLOODWAIT_MAX_RETRIES = int(getenv("FLOODWAIT_MAX_RETRIES", "5"))
    FLOODWAIT_BASE_DELAY = int(getenv("FLOODWAIT_BASE_DELAY", "30"))  # Base delay in seconds


class IndexConfig:
    """Full-channel indexing (link trigger) — deliberately slow and resumable.

    A channel can hold lakhs of messages, so the scanner pages history in
    bounded batches, sleeps between them, and pauses whenever the file queue
    is already deep. Progress is persisted per batch, so a restart resumes
    from the last scanned message instead of starting over.
    """

    # History paging
    SCAN_BATCH_SIZE = int(getenv("INDEX_SCAN_BATCH_SIZE", "200"))  # messages per get_chat_history call
    SCAN_BATCH_DELAY = float(getenv("INDEX_SCAN_BATCH_DELAY", "2.0"))  # seconds between batches

    # Per-file pacing while parsing/queueing (probe work dominates when enabled)
    FILE_DELAY = float(getenv("INDEX_FILE_DELAY", "0.5"))
    ENABLE_PROBE = getenv("INDEX_ENABLE_PROBE", "true").lower() == "true"

    # Backpressure: hold the scan while the processing queue is this deep
    MAX_QUEUE_BACKLOG = int(getenv("INDEX_MAX_QUEUE_BACKLOG", "2000"))
    BACKLOG_WAIT_SECONDS = int(getenv("INDEX_BACKLOG_WAIT_SECONDS", "30"))

    # FloodWait / failure handling
    FLOODWAIT_MAX_RETRIES = int(getenv("INDEX_FLOODWAIT_MAX_RETRIES", "5"))
    MAX_CONSECUTIVE_BATCH_FAILURES = int(getenv("INDEX_MAX_BATCH_FAILURES", "5"))

    # Progress message edits are throttled to stay well under bot edit limits
    PROGRESS_EDIT_INTERVAL = int(getenv("INDEX_PROGRESS_EDIT_INTERVAL", "15"))

    # Stop queueing before the cluster quota is exhausted; otherwise every
    # remaining file fails insert and piles up terminal queue records.
    STORAGE_STOP_PERCENT = int(getenv("INDEX_STORAGE_STOP_PERCENT", "92"))
    STORAGE_CHECK_SECONDS = int(getenv("INDEX_STORAGE_CHECK_SECONDS", "300"))

    RESUME_ON_START = getenv("INDEX_RESUME_ON_START", "true").lower() == "true"


class StorageConfig:
    """Storage hygiene config — keeps quota-driven DBs (Atlas free tier) healthy."""

    # views collection TTL (days). Trending only needs recent data.
    VIEWS_TTL_DAYS = int(getenv("VIEWS_TTL_DAYS", "90"))
    # How often (seconds) to refresh per-cluster free-space stats for shard picking.
    SHARD_STATS_REFRESH_SECONDS = int(getenv("SHARD_STATS_REFRESH_SECONDS", "3600"))
    # Per-cluster storage quota (MB) used by the /storage report.
    CLUSTER_QUOTA_MB = int(getenv("CLUSTER_QUOTA_MB", "512"))
    STORAGE_ALERT_PERCENT = int(getenv("STORAGE_ALERT_PERCENT", "70"))


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
    # Default (quick) probe stops after this many progressive steps (~1 MB).
    # A deep probe runs only when a same-quality comparison is otherwise tied.
    QUICK_STEPS = int(getenv("MEDIA_PROBE_QUICK_STEPS", "4"))
    DEEP_PROBE_ON_TIE = getenv("MEDIA_PROBE_DEEP_ON_TIE", "true").lower() == "true"
    
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
