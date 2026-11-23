import sys
import os

# ── Fix Windows console encoding (emoji/unicode in log messages) ──────────────
# Must be done BEFORE any other imports that might trigger the logger
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass  # Python < 3.7 fallback

# ── Port conflict pre-check ───────────────────────────────────────────────────
import socket
def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0

# ── TMDB DNS bypass (Bypasses Indian ISP DNS blocks via official CloudFront IPs) ──
_orig_getaddrinfo = socket.getaddrinfo
_TMDB_HOST_MAP = {
    "api.themoviedb.org": "3.175.86.67",
    "themoviedb.org": "18.155.33.30",
    "api.tmdb.org": "3.175.86.16",
    "image.tmdb.org": "84.17.38.232",
    "api.jikan.moe": "135.181.39.91",
}
def _tmdb_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if isinstance(host, str) and host in _TMDB_HOST_MAP:
        return _orig_getaddrinfo(_TMDB_HOST_MAP[host], port, family, type, proto, flags)
    return _orig_getaddrinfo(host, port, family, type, proto, flags)
socket.getaddrinfo = _tmdb_getaddrinfo

from Backend.config import Telegram as _TelCfg
_port = getattr(_TelCfg, "PORT", 8000)
if _port_in_use(_port):
    print(f"\n[ERROR] Port {_port} is already in use!")
    print(f"        Run this to free it (PowerShell):")
    print(f"        (netstat -ano | Select-String ':{_port}' | Where-Object {{ $_ -match 'LISTENING' }} | ForEach-Object {{ ($_ -split '\\s+')[-1] }}) | ForEach-Object {{ taskkill /F /PID $_ }}")
    print(f"\n  Or just run:  .\\start.ps1  (handles this automatically)\n")
    sys.exit(1)

from asyncio import get_event_loop, sleep as asleep
from traceback import format_exc
from wzgram import idle
from Backend import __version__, db
from Backend.logger import LOGGER
from Backend.fastapi import server
from Backend.helper.pyro import restart_notification
from Backend.wzgram_pkg import StreamBot, UserBot
from Backend.wzgram_pkg.clients import initialize_clients
from Backend.config import Telegram
from Backend.wzgram_pkg.plugins.start import start_background_tasks

loop = get_event_loop()

async def start_services():
    try:
        # Validate critical env vars before doing anything else
        Telegram.validate()
        
        LOGGER.info(f"Initializing Project-Stream v-{__version__}")
        await asleep(1.2)
        
        await db.connect()
        await asleep(1.2)
        
        # Start web server first - API endpoints work without Telegram bot
        await asleep(2)
        LOGGER.info('Initializing Project-S Web Server...')
        await restart_notification()
        loop.create_task(server.serve())

        # Try to start Telegram bot with FloodWait handling
        try:
            await StreamBot.start()
            StreamBot.username = StreamBot.me.username
            LOGGER.info(f"Bot Client : [@{StreamBot.username}]")

            await asleep(1.2)
            LOGGER.info("Initializing Multi Clients...")
            await initialize_clients()

            if UserBot is not None:
                try:
                    await UserBot.start()
                    LOGGER.info(f"User Client : [@{UserBot.me.username or UserBot.me.id}] (channel indexing enabled)")
                except Exception as e:
                    LOGGER.warning(f"User client failed to start (channel indexing disabled): {e}")

            # Start enhanced queue system background tasks
            await start_background_tasks()
        except Exception as e:
            LOGGER.warning(f"Telegram bot failed to start (API endpoints will still work): {e}")
            LOGGER.warning("Continuing with web server only...")

        LOGGER.info("Project-S Started Successfully!")
        await idle()
    except Exception:
        LOGGER.error("Error during startup:\n" + format_exc())

async def stop_services():
    try:
        LOGGER.info("Stopping services...")
        if UserBot is not None:
            try:
                await UserBot.stop()
            except Exception:
                pass
        await StreamBot.stop()
        await db.disconnect()
        LOGGER.info("Services stopped successfully.")
    except Exception:
        LOGGER.error("Error during shutdown:\n" + format_exc())

if __name__ == '__main__':
    try:
        loop.run_until_complete(start_services())
    except KeyboardInterrupt:
        LOGGER.info('Service Stopping...')
    except Exception:
        LOGGER.error(format_exc())
    finally:
        loop.run_until_complete(stop_services())
        loop.stop()
