"""
Handler for DaiVinchi (Dating Bot) auto-likes.
Auto-responds to @leomatchbot with likes.
"""

from __future__ import annotations

import asyncio
import logging
import re

from telethon import TelegramClient, events

from config import Config
from core.state_store import state

logger = logging.getLogger(__name__)

# Regex: any message starting with "Ты понравилась"
PATTERN_LIKES = re.compile(r"^Ты понравилась", re.IGNORECASE | re.MULTILINE)


async def _auto_like_daivinchi(client: TelegramClient, event) -> bool:
    """Process message from daivinchi bot and auto-like."""
    text = (event.raw_text or "").strip()
    chat_id = event.chat_id
    
    # Check if it's from leomatchbot
    sender = await event.get_sender()
    if not sender:
        return False
    
    username = getattr(sender, "username", None) or ""
    first_name = getattr(sender, "first_name", "") or ""
    
    # Check if it's the dating bot
    is_dating_bot = (
        username.lower() == "leomatchbot" or 
        "leomatch" in username.lower() or
        "matchbot" in username.lower()
    )
    
    # Check for "Ты понравилась..." pattern
    if not (is_dating_bot and PATTERN_LIKES.search(text)):
        return False
    
    logger.info("DaVinchi message: %s", text[:80])
    
    # Send "1 👍" to show next profile
    await asyncio.sleep(0.5)
    await client.send_message(chat_id, "1 👍")
    logger.info("Sent '1 👍' to leomatchbot")
    await client.send_read_acknowledge(chat_id, max_id=event.id)
    return True


def register(client: TelegramClient) -> None:
    logger.info("Registering DaVinchi auto-like handler")

    @client.on(events.NewMessage(incoming=True))
    async def _on_daivinchi(event):
        if event.out:
            return
        if not Config.AUTO_START_ENABLED:
            return
        try:
            await _auto_like_daivinchi(client, event)
        except Exception as exc:
            logger.warning("DaVinchi error: %s", exc)
