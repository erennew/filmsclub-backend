from pyrogram.filters import create
from Backend.config import Telegram

class CustomFilters:

    @staticmethod
    async def owner_filter(client, message):
        user = message.from_user or message.sender_chat
        uid = user.id
        return uid == Telegram.OWNER_ID

    owner = create(owner_filter)

    @staticmethod
    async def admin_filter(client, message):
        user = message.from_user or message.sender_chat
        uid = user.id
        return uid == Telegram.OWNER_ID or uid in Telegram.ADMIN_IDS

    admin = create(admin_filter)