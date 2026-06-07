"""
Generate Pyrogram session string for Heroku deployment.

This prevents FloodWait errors by creating a persistent session
that can be stored as a Heroku config var instead of a file.

Usage:
    python generate_session_string.py

Then copy the output and set it as Heroku config var:
    heroku config:set SESSION_STRING="your_session_string_here" -a your-app-name
"""

import asyncio
from pyrogram import Client

# Replace with your actual credentials
API_ID = 25259066
API_HASH = "caad2cdad2fe06057f2bf8f8a8e58950"
BOT_TOKEN = "8228736863:AAFuwwJ3AZn0SCRz7F14PprLAcigN_3uyOk"

async def main():
    print("Generating session string for bot...")
    
    async with Client(
        name="session_gen",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True  # Don't create session file
    ) as app:
        session_string = await app.export_session_string()
        print("\n" + "="*60)
        print("SESSION STRING GENERATED!")
        print("="*60)
        print(f"\n{session_string}\n")
        print("="*60)
        print("\nSet this as Heroku config var:")
        print(f'heroku config:set SESSION_STRING="{session_string}" -a your-app-name')
        print("\nThis will prevent FloodWait errors on Heroku.")

if __name__ == "__main__":
    asyncio.run(main())
