"""
Periodic task to check for unread messages in all dialogs and respond to them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Sequence, cast

from telethon import TelegramClient
from telethon.tl import types

from config import Config
from core.debounce_buffer import BufferedIncoming, init_debounce_buffer
from core.history_store import history
from core.llm import MessageContent, complete_chat
from core.state_store import state
from handlers.autoresponder import _display_name, _resolve_batch_reply, _send_batched_humanlike, wait_cooldown
from utils.humanize import (
    clean_telegram_garbage,
    maybe_apply_typos,
    pre_read_delay,
    split_bot_bubbles,
    typing_duration_for_text,
)
from utils.prompts import (
    build_autoresponder_user_prompt,
    build_system_prompt,
    format_history_for_prompt,
    history_window_size,
)
from utils.situational_context import (
    build_situational_system_appendix,
    format_line_now_msk,
    heuristic_activity_and_place,
    now_msk,
)

logger = logging.getLogger(__name__)

_UNREAD_CHECK_INTERVAL = 60


async def _check_and_respond_unread(client: TelegramClient) -> None:
    """Check all dialogs for messages without bot response and respond to them."""
    status = str(state.get("system_status", "off"))
    if status == "off" or bool(state.get("ghost_mode")):
        logger.debug("Skipping unread check: status=%s, ghost=%s", status, state.get("ghost_mode"))
        return

    logger.info("Running unread check...")

    me = None
    my_id = None
    try:
        me = await client.get_me()
        my_id = me.id if me else None
    except Exception:
        pass

    try:
        dialogs = await client.get_dialogs()
    except Exception as exc:
        logger.warning("Failed to get dialogs: %s", exc)
        return

    processed_chats = set()

    for dialog in dialogs:
        try:
            chat = dialog.chat
            peer_id = chat.id if chat else None
            if not peer_id:
                continue

            if peer_id in processed_chats:
                continue
            processed_chats.add(peer_id)

            # Get the last message in the chat
            last_msg = None
            try:
                async for msg in client.iter_messages(peer_id, limit=1):
                    last_msg = msg
            except Exception:
                continue

            if not last_msg:
                continue

            # Check if last message is from user (not from bot)
            if getattr(last_msg, "out", False):
                # Last message is from bot - no need to respond
                continue

            is_muted = getattr(dialog, "is_muted", None)
            if is_muted and callable(is_muted) and is_muted():
                logger.debug("Skipping muted dialog %s", peer_id)
                continue

            sender = await client.get_entity(peer_id)
            if Config.IGNORE_BOTS and getattr(sender, "bot", False):
                logger.debug("Skipping bot dialog %s", peer_id)
                continue

            sender_name = await _display_name(sender)

            cooldowns_mono = dict(state.get("cooldowns_mono") or {})
            key = f"{peer_id}:{peer_id}"
            now_mono = time.monotonic()
            end_mono = float(cooldowns_mono.get(key, 0))
            if now_mono < end_mono:
                logger.debug("Skipping chat %s: cooldown active", peer_id)
                continue

            messages = []
            try:
                async for msg in client.iter_messages(peer_id, limit=10):
                    if my_id and msg.from_id and msg.from_id == my_id:
                        continue
                    if getattr(msg, "out", False):
                        continue
                    if msg.action and hasattr(msg.action, "muted_members"):
                        continue
                    messages.append(msg)
            except Exception as exc:
                logger.debug("Failed to get messages for %s: %s", peer_id, exc)
                continue

            if not messages:
                continue

            logger.info("Found %d new messages in chat %s", len(messages), peer_id)

            user_texts = []
            for msg in messages:
                text = (msg.text or "").strip()
                media_type = None
                if msg.voice:
                    text = "голосовое"
                    media_type = "voice"
                elif msg.video_note:
                    text = "кружок"
                    media_type = "circle"
                elif msg.photo:
                    text = "фото"
                if text:
                    user_texts.append(text)

            if not user_texts:
                continue

            await wait_cooldown(peer_id, peer_id, status)

            user_block = "\n".join(user_texts)

            hist = history.get_recent(peer_id)
            n = history_window_size()
            max_items = min(n, len(hist)) if hist else None
            hist_for_user = format_history_for_prompt(hist, max_items=max_items)
            analyze_cap = min(28, len(hist)) if hist else None
            hist_for_analysis = format_history_for_prompt(hist, max_items=analyze_cap)

            ctx = str(state.get("context_mode", "default"))
            human = bool(state.get("human_mode", True))

            situational = await build_situational_system_appendix(
                history_text=hist_for_analysis,
                peer_display_name=sender_name,
                batch=[],
            )
            system = build_system_prompt(
                context_mode=ctx,
                human_mode=human,
                situational_appendix=situational,
            )

            schedule_header = format_line_now_msk()

            user_payload = build_autoresponder_user_prompt(
                schedule_header=schedule_header,
                chat_history_lines=hist_for_user,
                user_block_text=user_block,
                chat_title=getattr(chat, "title", None),
                is_private=True,
                peer_name=sender_name,
                reply_chain=None,
            )

            messages_for_llm: list[dict[str, MessageContent]] = [
                {"role": "system", "content": system},
                {"role": "user", "content": user_payload},
            ]

            result = await complete_chat(
                cast(Sequence[dict[str, MessageContent]], messages_for_llm),
            )
            logger.info("Unread check: generated reply: %s", result[:100] if result else None)

            if not result:
                result = "сейчас не могу ответить, потом напишу"

            max_msg_id = max(m.id for m in messages)
            min_msg_id = min(m.id for m in messages)

            text = clean_telegram_garbage(result)
            text = maybe_apply_typos(text)
            bubbles = split_bot_bubbles(text)
            if not bubbles:
                continue

            for idx, chunk in enumerate(bubbles):
                if idx:
                    await asyncio.sleep(0.5)
                dur = await typing_duration_for_text(chunk)
                try:
                    async with client.action(peer_id, "typing"):
                        await asyncio.sleep(dur)
                except Exception:
                    await asyncio.sleep(dur)
                reply_to = min_msg_id if idx == 0 else None
                await client.send_message(peer_id, chunk, reply_to=reply_to)

            for bubble in bubbles:
                history.append(peer_id, "assistant", bubble)

            try:
                await client.send_read_acknowledge(peer_id, max_id=max_msg_id)
            except Exception as exc:
                logger.debug("Read ack failed: %s", exc)

            logger.info("Replied to unread messages in chat %s", peer_id)

        except Exception as exc:
            logger.debug("Error processing dialog: %s", exc)
            continue


async def _unread_monitor_loop(client: TelegramClient) -> None:
    """Loop that checks for unread messages every minute."""
    while True:
        await asyncio.sleep(_UNREAD_CHECK_INTERVAL)
        try:
            await _check_and_respond_unread(client)
        except Exception as exc:
            logger.warning("Unread check failed: %s", exc)


def register(client: TelegramClient) -> None:
    logger.info("Registering unread message monitor")
    asyncio.create_task(_unread_monitor_loop(client))