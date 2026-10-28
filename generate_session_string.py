"""
Generate a Wzgram session string for Heroku deployment.

Credentials are read from the environment (or config.env / .env), never
hardcoded. Run:

    python generate_session_string.py

Then store the output as the SESSION_STRING config var:

    heroku config:set SESSION_STRING="..." -a your-app-name
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv("config.env")
load_dotenv(".env")

try:
    from wzgram import Client
except ImportError:
    sys.exit("wzgram is not installed. Run: pip install -r requirements.txt")


API_ID = os.getenv("API_ID", "").strip()
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()


async def main() -> None:
    missing = [
        name
        for name, value in (
            ("API_ID", API_ID),
            ("API_HASH", API_HASH),
            ("BOT_TOKEN", BOT_TOKEN),
        )
        if not value
    ]
    if missing:
        sys.exit(f"Missing environment variables: {', '.join(missing)}")

    async with Client(
        name="session_gen",
        api_id=int(API_ID),
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True,
    ) as app:
        session_string = await app.export_session_string()

    print("\nSESSION_STRING generated:\n")
    print(session_string)
    print("\nStore it with:")
    print('  heroku config:set SESSION_STRING="<value>" -a your-app-name')


if __name__ == "__main__":
    asyncio.run(main())
