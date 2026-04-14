"""Telegram-specific helpers: reply chains and mention detection."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import Config

if TYPE_CHECKING:
    from telethon import TelegramClient
    from telethon.events import NewMessage

logger = logging.getLogger(__name__)


async def get_reply_chain_text(client: TelegramClient, message) -> str:
    """
    Walk the reply chain upward and format chronologically (TeleAgent-style).
    """
    chain: list[str] = []
    current = message
    me = await client.get_me()
    my_id = me.id if me else 0

    while current:
        sender = await current.get_sender()
        name = "Unknown"
        if sender:
            if sender.id == my_id:
                name = "Me"
            elif getattr(sender, "first_name", None):
                name = sender.first_name
                if getattr(sender, "last_name", None):
                    name = f"{name} {sender.last_name}"
            elif getattr(sender, "title", None):
                name = sender.title

        text = getattr(current, "text", None) or getattr(current, "raw_text", None) or ""
        if not text.strip():
            text = "[media / no text]"
        time_str = current.date.strftime("%Y-%m-%d %H:%M:%S")
        chain.append(f"[{time_str}] {name}: {text}")
        current = await current.get_reply_message()

    return "\n".join(reversed(chain))


def is_self_mentioned(event: NewMessage.Event, my_username: str | None) -> bool:
    """True if this account is @mentioned in a group/supergroup."""
    msg = event.message
    if getattr(msg, "mentioned", False):
        return True
    raw = event.raw_text or ""
    if my_username and raw:
        un = my_username.lower().lstrip("@")
        if f"@{un}" in raw.lower():
            return True
    return False


def owner_check(event: NewMessage.Event) -> bool:
    if not Config.OWNER_ID:
        logger.warning("OWNER_ID is not set; owner-only command allowed for everyone")
        return True
    return event.sender_id == Config.OWNER_ID
