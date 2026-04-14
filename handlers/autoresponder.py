"""
Incoming DM / group-mention auto replies with debounced batching and plain sends (no reply_to).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, List, Mapping, Sequence, cast

from telethon import TelegramClient, events

from config import Config
from core.debounce_buffer import BufferedIncoming, init_debounce_buffer
from core.history_store import history
from core.llm import MessageContent, complete_chat
from core.state_store import state
from utils.humanize import (
    clean_telegram_garbage,
    inter_bot_burst_pause,
    maybe_apply_typos,
    maybe_react_to_message,
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
    format_batch_message_times_msk,
    format_line_now_msk,
    heuristic_activity_and_place,
    now_msk,
)
from utils.telegram_helpers import get_reply_chain_text, is_self_mentioned

logger = logging.getLogger(__name__)


async def _display_name(sender) -> str | None:
    if not sender:
        return None
    if getattr(sender, "first_name", None):
        name = sender.first_name
        if getattr(sender, "last_name", None):
            name = f"{name} {sender.last_name}"
        return name
    if getattr(sender, "title", None):
        return sender.title
    return None


async def wait_cooldown(chat_id: int, sender_id: int, status: str) -> None:
    """Sleep until per-peer cooldown allows another auto reply, then refresh timestamp."""
    key = f"{chat_id}:{sender_id}"
    cooldowns_mono = dict(state.get("cooldowns_mono") or {})
    now_mono = time.monotonic()
    end_mono = float(cooldowns_mono.get(key, 0))
    limit = float(Config.COOLDOWN_SECONDS_AI)
    if status == "static":
        vips = list(state.get("vip_list") or [])
        limit = (
            float(Config.COOLDOWN_SECONDS_STATIC_VIP)
            if sender_id in vips
            else float(Config.COOLDOWN_SECONDS_STATIC)
        )
    if now_mono < end_mono:
        wait_time = end_mono - now_mono
        # Cap wait time at 30 seconds to prevent excessive delays
        wait_time = min(wait_time, 30.0)
        await asyncio.sleep(wait_time + 0.05)
    cooldowns_mono[key] = time.monotonic() + limit
    state.set("cooldowns_mono", cooldowns_mono)


async def _resolve_batch_reply(
    client,
    *,
    chat_id: int,
    is_private: bool,
    user_block: str,
    sender_name: str | None,
    chat_title: str | None,
    last_event,
    batch: List[BufferedIncoming],
    image_data: bytes | None = None,
) -> str | None:
    status = str(state.get("system_status", "off"))
    if status == "static":
        return str(state.get("static_message") or "…")

    ctx = str(state.get("context_mode", "default"))
    human = bool(state.get("human_mode", True))

    hist = history.get_recent(chat_id)
    n = history_window_size()
    max_items = min(n, len(hist)) if hist else None
    hist_for_user = format_history_for_prompt(hist, max_items=max_items)
    analyze_cap = min(28, len(hist)) if hist else None
    hist_for_analysis = format_history_for_prompt(hist, max_items=analyze_cap)

    situational = await build_situational_system_appendix(
        history_text=hist_for_analysis,
        peer_display_name=sender_name,
        batch=batch,
    )
    system = build_system_prompt(
        context_mode=ctx,
        human_mode=human,
        situational_appendix=situational,
    )

    reply_chain = None
    if last_event and getattr(last_event, "is_reply", False):
        r = await last_event.get_reply_message()
        if r:
            reply_chain = await get_reply_chain_text(client, r)

    schedule_header = "\n".join(
        [
            format_line_now_msk(),
            format_batch_message_times_msk(batch),
            f"Эвристика занятости сейчас (по часу в Москве): {heuristic_activity_and_place(now_msk())}",
        ]
    )

    user_payload = build_autoresponder_user_prompt(
        schedule_header=schedule_header,
        chat_history_lines=hist_for_user,
        user_block_text=user_block,
        chat_title=chat_title,
        is_private=is_private,
        peer_name=sender_name,
        reply_chain=reply_chain,
    )

    messages: list[dict[str, MessageContent]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_payload},
    ]
    logger.info("Calling complete_chat for chat %s", chat_id)
    result = await complete_chat(cast(Sequence[dict[str, MessageContent]], messages), image_data=image_data)
    logger.info("complete_chat result: %s", result[:100] if result else None)
    return result


async def _send_batched_humanlike(
    client: TelegramClient,
    *,
    chat_id: int,
    text: str,
    max_incoming_msg_id: int,
    min_incoming_msg_id: int,
    reaction_event,
) -> list[str]:
    """Read ack, typing, then 1-2 plain sends, reply to last message in batch."""
    text = clean_telegram_garbage(text)
    text = maybe_apply_typos(text)
    bubbles = split_bot_bubbles(text)
    if not bubbles:
        return []

    await pre_read_delay()
    try:
        await client.send_read_acknowledge(chat_id, max_id=max_incoming_msg_id)
    except Exception as exc:
        logger.debug("Read ack failed: %s", exc)

    for idx, chunk in enumerate(bubbles):
        if idx:
            await inter_bot_burst_pause()
        dur = await typing_duration_for_text(chunk)
        try:
            async with client.action(chat_id, "typing"):
                await asyncio.sleep(dur)
        except Exception as exc:
            logger.debug("Typing action failed: %s", exc)
            await asyncio.sleep(dur)
        reply_to = min_incoming_msg_id if idx == 0 else None
        await client.send_message(chat_id, chunk, reply_to=reply_to)

    state.update_metric("total_replies")
    if reaction_event is not None:
        await maybe_react_to_message(reaction_event)
    return bubbles


def register(client: TelegramClient) -> None:
    init_debounce_buffer(
        delay_min=Config.DEBOUNCE_SECONDS_MIN,
        delay_max=Config.DEBOUNCE_SECONDS_MAX,
    )
    from core.debounce_buffer import get_debounce_buffer

    coordinator = get_debounce_buffer()

    async def _flush_batch(key, batch: List[BufferedIncoming]) -> None:
        chat_id, sender_id = key
        logger.info("Flushing batch for key %s, batch size %d", key, len(batch))
        if not batch:
            return

        status = str(state.get("system_status", "off"))
        if status == "off" or bool(state.get("ghost_mode")):
            logger.info("Skipping due to status %s or ghost %s", status, state.get("ghost_mode"))
            return

        last = batch[-1].event
        is_private = last.is_private

        await wait_cooldown(chat_id, sender_id, status)

        lines = [b.text.strip() for b in batch if b.text.strip()]
        if not lines:
            return
        user_block = "\n".join(lines)

        sender = await last.get_sender()
        sender_name = await _display_name(sender)

        chat_title = None
        if not is_private:
            try:
                chat = await last.get_chat()
                chat_title = getattr(chat, "title", None)
            except Exception:
                chat_title = None

        max_mid = max(b.message_id for b in batch)
        min_mid = min(b.message_id for b in batch)
        
        # Collect image data from batch items (use last image if multiple)
        image_data = None
        for b in reversed(batch):
            if b.image_data:
                image_data = b.image_data
                break

        try:
            reply = await _resolve_batch_reply(
                client,
                chat_id=chat_id,
                is_private=is_private,
                user_block=user_block,
                sender_name=sender_name,
                chat_title=chat_title,
                last_event=last,
                batch=batch,
                image_data=image_data,
            )
        except Exception as exc:
            logger.exception("Batch reply generation error: %s", exc)
            state.update_metric("llm_errors")
            reply = None

        if not reply:
            reply = str(state.get("static_message") or "сейчас не могу ответить, потом напишу")

        try:
            sent_bubbles = await _send_batched_humanlike(
                client,
                chat_id=chat_id,
                text=reply,
                max_incoming_msg_id=max_mid,
                min_incoming_msg_id=min_mid,
                reaction_event=last,
            )
            logger.info("Generated reply: %s", reply[:100] if reply else None)
            logger.info("Sent %d bubbles", len(sent_bubbles))
            for bubble in sent_bubbles:
                history.append(chat_id, "assistant", bubble)
        except Exception as exc:
            logger.exception("Batch send failed: %s", exc)
            state.update_metric("llm_errors")

    @client.on(events.NewMessage(incoming=True))
    async def _on_incoming(event):
        logger.info("Incoming message from %s in chat %s: %s", event.sender_id, event.chat_id, (event.raw_text or "").strip()[:50])
        if event.out:
            return

        sender = await event.get_sender()
        if Config.IGNORE_BOTS and sender and getattr(sender, "bot", False):
            logger.info("Sender %s is bot, skipping", event.sender_id)
            return

        if sender is None:
            logger.warning("Sender is None for message from %s", event.sender_id)

        if sender and getattr(sender, "is_self", False):
            logger.info("Message is from self, skipping")
            return

        status = str(state.get("system_status", "off"))
        if status == "off":
            logger.info("Status is off, skipping")
            return

        if bool(state.get("ghost_mode")):
            logger.info("Ghost mode on, skipping")
            return

        if event.sender_id in list(state.get("blacklist") or []):
            logger.info("Sender %s in blacklist, skipping", event.sender_id)
            return

        # Check for voice messages (voice = voice message, video_note = circle/кружок)
        voice_msg = event.message.voice
        video_note = event.message.video_note
        has_media = voice_msg or video_note
        
        # Check for photos
        photo_msg = event.message.photo
        
        if not event.is_private:
            if not Config.REPLY_IN_GROUPS:
                return
            me = await event.client.get_me()
            un = me.username if me else None
            if not is_self_mentioned(event, un):
                return

        user_text = (event.raw_text or "").strip()
        
        # Handle media: voice (голосовое), video_note (кружок), photo (фото)
        media_type = None
        if video_note:
            user_text = "кружок"
            media_type = "circle"
        elif voice_msg:
            user_text = "голосовое"
            media_type = "voice"
        
        # Handle photos - download and send to LLM for analysis
        image_data = None
        if photo_msg:
            user_text = "пользователь отправил фото. как тебе?"
            media_type = "photo"
            logger.info("Photo detected for message %s, downloading...", event.id)
            try:
                image_data = await event.message.download_media(bytes)
                logger.info("Photo downloaded: %d bytes", len(image_data) if image_data else 0)
            except Exception as exc:
                logger.warning("Failed to download photo: %s", exc)
        
        if not user_text and not has_media:
            return

        # Skip messages starting with ! or . to avoid autoresponder interfering with commands.
        if user_text.startswith(("!", ".")):
            return

        sender_name = await _display_name(sender)
        history.append(
            event.chat_id,
            "user",
            user_text,
            sender_name=sender_name,
            sender_id=int(event.sender_id),
        )

        item = BufferedIncoming(text=user_text, message_id=event.id, event=event, image_data=image_data)
        await coordinator.push(
            event.chat_id,
            int(event.sender_id),
            item,
            _flush_batch,
        )
