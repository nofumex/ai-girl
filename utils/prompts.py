"""Assemble system and user prompts from persona, mode, and history."""

from __future__ import annotations

import random
from typing import Any

from config import Config
from persona import HUMAN_STYLE_BLOCK, MODE_PROMPTS, PERSONA_BACK_PROMPT


def build_system_prompt(
    *,
    context_mode: str,
    human_mode: bool,
    situational_appendix: str | None = None,
) -> str:
    """System prompt for LLM (autoresponder and owner !ask)."""
    mode = MODE_PROMPTS.get(context_mode, MODE_PROMPTS["default"])
    parts = [PERSONA_BACK_PROMPT.strip(), mode]
    if human_mode:
        parts.append(HUMAN_STYLE_BLOCK.strip())
    parts.append(
        "Пиши только текст сообщений для Telegram. "
        "Обычно это 1-2 недлинных сообщения. "
        "Не используй переносы строк внутри одного сообщения. "
        "Если хочешь отправить 2 сообщения подряд, разделяй их пустой строкой. "
        "Не дроби одну мысль на много коротких строк. "
        "Следи за логикой переписки — не противоречь себе. "
        "Не пиши в скобках пояснения вроде «(отправляет...)», «скину», «могу скинуть» — это выдаёт что ты бот. "
        "Пиши как обычная девушка в переписке. "
        "Без нумерации и префиксов вроде «аня:» в тексте."
    )
    if situational_appendix and situational_appendix.strip():
        parts.append(
            "--- Контекст ситуации (учитывай при ответе) ---\n" + situational_appendix.strip()
        )
    return "\n\n".join(p for p in parts if p)


def format_history_for_prompt(items: list[dict[str, Any]], max_items: int | None = None) -> str:
    if max_items is not None and max_items <= 0:
        max_items = None
    if max_items is not None:
        items = items[-max_items:]
    lines: list[str] = []
    for it in items:
        role = it.get("role", "user")
        who = "собеседник" if role == "user" else "я"
        sender = it.get("sender")
        prefix = f"{who}" + (f" ({sender})" if sender else "")
        content = str(it.get("content", "")).strip()
        if content:
            lines.append(f"{prefix}: {content}")
    if not lines:
        return "(раньше в сохранённой истории ничего нет)"
    return "\n".join(lines)


def build_autoresponder_user_prompt(
    *,
    schedule_header: str,
    chat_history_lines: str,
    user_block_text: str,
    chat_title: str | None,
    is_private: bool,
    peer_name: str | None,
    reply_chain: str | None,
) -> str:
    """
    User message for the autoresponder: Moscow time + history + latest burst.
    """
    parts: list[str] = []
    if schedule_header.strip():
        parts.append("Время и ситуация (для ориентира в ответе):")
        parts.append(schedule_header.strip())

    parts.append("Ниже — недавняя история диалога (последние записи в памяти чата).")
    parts.append("История:")
    parts.append(chat_history_lines)

    meta: list[str] = [f"Чат: {'личка' if is_private else 'группа'}"]
    if chat_title:
        meta.append(f"Название: {chat_title}")
    if peer_name:
        meta.append(f"Имя собеседника (подсказка): {peer_name}")
    parts.append("\n".join(meta))

    if reply_chain and reply_chain.strip():
        parts.append("Контекст: пользователь ответил на сообщение (см. ниже). Учитывай это при ответе:")
        parts.append(reply_chain.strip())

    block = user_block_text.strip()
    message_count = len(block.split("\n"))
    if message_count > 1:
        instruction = (
            "Пользователь написал несколько сообщений подряд ({} сообщений). "
            "Отвечай на ВСЮ группу сообщений одним общим ответом (1-2 коротких сообщения). "
            "Не отвечай на каждое сообщение отдельно. "
            "Следи за логикой разговора — не противоречь тому что ты писала раньше в этом же чате.\n"
        ).format(message_count)
    else:
        instruction = (
            "Отвечай естественно, без цитирования. "
            "Не говори «ответ на твоё сообщение» и не пересказывай их текст как цитату. "
            "Следи за логикой разговора — не противоречь тому что ты писала раньше в этом же чате. "
        )
    parts.append(
        "Пользователь только что написал следующие сообщения подряд:\n\n"
        f"{block}\n\n"
        + instruction
    )
    return "\n\n".join(parts)


def history_window_size() -> int:
    """Random inclusive window for how many stored turns to inject (bounded by config)."""
    lo = max(1, Config.HISTORY_MIN_MESSAGES)
    hi = max(1, Config.HISTORY_MAX_MESSAGES)
    if lo > hi:
        lo, hi = hi, lo
    return random.randint(lo, hi)


def build_user_payload(
    *,
    chat_history_lines: str,
    reply_chain: str | None,
    latest_message: str,
    chat_title: str | None,
    is_private: bool,
    peer_name: str | None = None,
    schedule_header: str = "",
) -> str:
    """Payload for owner !ask and tools."""
    return build_autoresponder_user_prompt(
        schedule_header=schedule_header,
        chat_history_lines=chat_history_lines,
        user_block_text=latest_message,
        chat_title=chat_title,
        is_private=is_private,
        peer_name=peer_name,
        reply_chain=reply_chain,
    )