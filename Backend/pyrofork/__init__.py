from pyrogram import Client
from Backend.config import Telegram
from os import getenv


plugins = {"root": "Backend/pyrofork/plugins"}

# Use session string for Heroku (avoids FloodWait from file loss)
_session_string = getenv("SESSION_STRING")

StreamBot = Client(
    name='bot',
    api_id=Telegram.API_ID,
    api_hash=Telegram.API_HASH,
    bot_token=Telegram.BOT_TOKEN,
    session_string=_session_string,
    workdir="Backend",
    plugins=plugins,
    sleep_threshold=100,
    workers=80,
    max_concurrent_transmissions=1000
)


multi_clients = {}
work_loads = {}