"""Register Telethon event handlers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from handlers import autoresponder, owner, unread_monitor

if TYPE_CHECKING:
    from telethon import TelegramClient

logger = logging.getLogger(__name__)


def register_all(client: TelegramClient) -> None:
    owner.register(client)
    autoresponder.register(client)
    unread_monitor.register(client)
    logger.info("Handlers registered (owner + autoresponder + unread monitor)")
