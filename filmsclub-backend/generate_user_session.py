"""
Generate a user-account session string for channel history scans.

Telegram forbids bots from calling messages.GetHistory, so indexing an
existing channel requires a real account that is a member of that channel.
Run:

    python generate_user_session.py

Then store the output as the USER_SESSION_STRING config var:

    heroku config:set USER_SESSION_STRING="..." -a your-app-name

The account must be a member (or admin) of every channel you want to index.
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


async def main() -> None:
    if not API_ID or not API_HASH:
        sys.exit("Missing API_ID / API_HASH in config.env or .env")

    async with Client(
        name="user_session_gen",
        api_id=int(API_ID),
        api_hash=API_HASH,
        in_memory=True,
    ) as app:
        me = await app.get_me()
        session_string = await app.export_session_string()

    username = f"@{me.username}" if me.username else str(me.id)
    print(f"\nLogged in as {me.first_name} ({username})")
    print("\nUSER_SESSION_STRING generated:\n")
    print(session_string)
    print("\nStore it with:")
    print('  heroku config:set USER_SESSION_STRING="<value>" -a your-app-name')
    print("\nKeep it secret: it grants full access to the account.")


if __name__ == "__main__":
    asyncio.run(main())
