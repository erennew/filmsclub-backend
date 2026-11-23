import socket

# ── TMDB DNS bypass (Bypasses Indian ISP DNS blocks via official CloudFront IPs) ──
_orig_getaddrinfo = socket.getaddrinfo
_TMDB_HOST_MAP = {
    "api.themoviedb.org": ["3.175.86.67", "3.175.86.50", "3.175.86.103", "3.175.86.37"],
    "themoviedb.org": ["18.155.33.30", "18.155.33.48", "18.155.33.116", "18.155.33.119"],
    "api.tmdb.org": ["3.175.86.16", "3.175.86.56", "3.175.86.78", "3.175.86.91"],
    "image.tmdb.org": ["84.17.38.232"],
}
def _tmdb_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if isinstance(host, str) and host in _TMDB_HOST_MAP:
        res = []
        for ip in _TMDB_HOST_MAP[host]:
            try:
                res.extend(_orig_getaddrinfo(ip, port, family, type, proto, flags))
            except Exception:
                pass
        if res:
            return res
    return _orig_getaddrinfo(host, port, family, type, proto, flags)
socket.getaddrinfo = _tmdb_getaddrinfo

from Backend.logger import LOGGER 
from Backend.helper.database import Database
from time import time
from datetime import datetime
import pytz


__version__ = "2.0.4"
StartTime = time()
timezone = pytz.timezone("Asia/Kolkata")
now = datetime.now(timezone)
USE_DEFAULT_ID: str = None  # or Optional[str]

db = Database()  
