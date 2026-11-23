from wzgram import Client
from Backend.config import Telegram
from os import getenv
from typing import Optional


plugins = {"root": "Backend/wzgram_pkg/plugins"}

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

_user_session_string = getenv("USER_SESSION_STRING")

UserBot = None
if _user_session_string:
    UserBot = Client(
        name="userbot",
        api_id=Telegram.API_ID,
        api_hash=Telegram.API_HASH,
        session_string=_user_session_string,
        workdir="Backend",
        in_memory=True,
        no_updates=True,
        sleep_threshold=100,
        workers=40,
    )


def user_session_error() -> Optional[str]:
    """Why USER_SESSION_STRING cannot read channel history, if it cannot."""
    if UserBot is None:
        return "USER_SESSION_STRING is required for channel history scans"

    # A common mistake is copying SESSION_STRING (generated from the bot token)
    # into USER_SESSION_STRING. Surface that explicitly instead of letting the
    # generic is_bot check produce a confusing message.
    if (
        _session_string
        and _user_session_string
        and _session_string.strip() == _user_session_string.strip()
    ):
        return (
            "USER_SESSION_STRING is set to the same session string as "
            "SESSION_STRING, which is the bot session. Generate a personal "
            "user session with 'python generate_user_session.py' (sign in with "
            "a real phone number), set USER_SESSION_STRING to that new value, "
            "leave SESSION_STRING unchanged, and restart the backend."
        )

    me = getattr(UserBot, "me", None)
    if me is not None and getattr(me, "is_bot", False):
        return (
            "USER_SESSION_STRING belongs to a bot account, not a user account. "
            "Telegram's Bot API cannot read channel history (BOT_METHOD_INVALID), "
            "so scans will always fail. Generate a session by signing in with a "
            "personal Telegram account that is a member/admin of the channels, "
            "set USER_SESSION_STRING to that session, and restart the backend."
        )

    # A rejected/revoked session still creates a Client object, but startup
    # only logs the failure. Without this check link resolution would succeed
    # via the bot and the scan would then die with a cryptic error.
    if not getattr(UserBot, "is_connected", True):
        return (
            "USER_SESSION_STRING is configured but its Telegram session is not "
            "connected (Telegram rejected it, e.g. AUTH_KEY_UNREGISTERED). "
            "Generate a fresh user session from an account that is a member of "
            "the channels, update USER_SESSION_STRING, and restart the backend."
        )
    return None


def get_index_client():
    """Return the connected user client required for Telegram history reads."""
    error = user_session_error()
    if error:
        raise RuntimeError(error)
    return UserBot