from os import getenv

from Backend.config import Telegram
import uvicorn
from Backend.fastapi.main import app

# Fetch host and port from the Telegram configuration
Port = Telegram.PORT

# Access logs would record bearer-style stream tokens; opt in explicitly.
config = uvicorn.Config(
    app=app,
    host='0.0.0.0',
    port=Port,
    server_header=False,
    proxy_headers=False,
    access_log=getenv("UVICORN_ACCESS_LOG", "false").lower() == "true",
)
server = uvicorn.Server(config)
