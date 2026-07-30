import sys
from logging import getLogger, FileHandler, StreamHandler, INFO, ERROR, Formatter, basicConfig
import pytz
from datetime import datetime

# Define IST timezone
IST = pytz.timezone("Asia/Kolkata")

class ISTFormatter(Formatter):
    """Custom formatter to use IST timezone."""
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, IST)
        return dt.strftime(datefmt or "%d-%b-%y %I:%M:%S %p")

# Create formatter with IST timezone
formatter = ISTFormatter("[%(asctime)s] [%(levelname)s] - %(message)s", "%d-%b-%y %I:%M:%S %p")

# File handler — always safe
file_handler = FileHandler("log.txt", encoding="utf-8")
file_handler.setFormatter(formatter)

# Stream handler — force UTF-8 on Windows (cp1252 can't encode emojis)
try:
    # Reconfigure stdout to UTF-8 if possible (Python 3.7+)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

stream_handler = StreamHandler(stream=sys.stdout)
stream_handler.setFormatter(formatter)

# Configure basic logging
basicConfig(
    handlers=[file_handler, stream_handler],
    level=INFO
)

# Set log level for external libraries
getLogger("aiohttp").setLevel(ERROR)
getLogger("pyrogram").setLevel(ERROR)
getLogger("fastapi").setLevel(ERROR)

# Create main logger
LOGGER = getLogger(__name__)
LOGGER.setLevel(INFO)

# Example log message
LOGGER.info("Logger initialized with IST timezone.")
