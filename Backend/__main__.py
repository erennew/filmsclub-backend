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
from pyrogram import idle
from Backend import __version__, db
from Backend.logger import LOGGER
from Backend.fastapi import server
from Backend.helper.pyro import restart_notification
from Backend.pyrofork import StreamBot
from Backend.pyrofork.clients import initialize_clients
from Backend.config import Telegram
from Backend.pyrofork.plugins.start import start_background_tasks

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
