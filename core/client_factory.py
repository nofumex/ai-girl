"""
Build Telethon client from SESSION_NAME (file) or SESSION_STRING.
"""

from __future__ import annotations

import logging

from telethon import TelegramClient
from telethon.sessions import StringSession

from config import Config

logger = logging.getLogger(__name__)


def create_client() -> TelegramClient:
    if Config.SESSION_STRING:
        session = StringSession(Config.SESSION_STRING.strip())
        logger.info("Using StringSession from SESSION_STRING")
    else:
        session = Config.SESSION_NAME
        logger.info("Using file session name: %s.session", session)

    return TelegramClient(session, Config.API_ID, Config.API_HASH)
