"""
AI-Girl Telegram userbot entrypoint.
English comments only; user-facing docs live in README.md (Russian).
"""

from __future__ import annotations

import asyncio
import logging
import time

from config import Config
from core.client_factory import create_client
from handlers import register_all
from utils.logging_setup import setup_logging

logger = logging.getLogger(__name__)


async def _run() -> None:
    t_errors = Config.validate_telegram()
    if t_errors:
        for msg in t_errors:
            logger.error("%s", msg)
        raise SystemExit(1)

    for w in Config.validate_llm():
        logger.warning("%s", w)

    client = create_client()
    register_all(client)

    await client.start()
    me = await client.get_me()
    logger.info("Logged in as id=%s @%s", me.id, me.username or "nousername")

    # Monitor connection and restart if disconnected
    async def monitor_connection():
        while True:
            await asyncio.sleep(60)  # Check every minute
            
            is_conn = client.is_connected()
            logger.info("Connection check: is_connected=%s", is_conn)
            
            if not is_conn:
                logger.warning("Client disconnected — triggering restart.")
                raise RuntimeError("Client disconnected")
            
            # Try to get me - this tests the connection more thoroughly
            try:
                me = await client.get_me()
                logger.info("Status: online as @%s (id=%s)", me.username or "nousername", me.id)
            except Exception as exc:
                logger.warning("get_me failed: %s — may be disconnected", exc)
                raise RuntimeError(f"Connection test failed: {exc}")

    monitor_task = asyncio.create_task(monitor_connection())

    try:
        await client.run_until_disconnected()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt — exiting.")
    finally:
        # Save session before exit (only if file session)
        try:
            if client.session and hasattr(client.session, 'save'):
                client.session.save()
                logger.info("Session saved")
        except Exception as e:
            logger.warning("Session save failed: %s", e)
        monitor_task.cancel()


def main() -> None:
    setup_logging()
    delay = float(Config.API_RESTART_BASE_DELAY)
    max_delay = float(Config.API_RESTART_MAX_DELAY)

    while True:
        try:
            asyncio.run(_run())
            logger.info("Client stopped normally.")
            break
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt — exiting.")
            break
        except SystemExit as exc:
            raise exc
        except Exception:
            logger.exception("Crash — restarting in %.1fs", delay)
            time.sleep(delay)
            delay = min(delay * 2.0, max_delay)


if __name__ == "__main__":
    main()
