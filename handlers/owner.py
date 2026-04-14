"""
Owner-only commands from OWNER_ID: .auto, .mode, .ghost, .human, .ping, .help, !ask, !help, !today, !clear
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telethon import events

from config import Config
from core.history_store import history
from core.llm import complete_chat
from core.state_store import state
from persona import TextConstants
from utils.prompts import build_system_prompt, format_history_for_prompt
from utils.situational_context import (
    build_situational_system_appendix,
    format_event_time_msk,
    format_line_now_msk,
    heuristic_activity_and_place,
    now_msk,
)
from utils.telegram_helpers import get_reply_chain_text, owner_check

if TYPE_CHECKING:
    from telethon import TelegramClient

logger = logging.getLogger(__name__)

ASK_AUTO = (
    "Read the thread and write ONE natural reply as me. "
    "Match the language of the chat. No meta commentary."
)
TODAY_DEFAULT_QUERY = "как дела за сегодня"


async def _entity_display_name(client, chat_id: int, items: list[dict]) -> str:
    try:
        entity = await client.get_entity(chat_id)
        first_name = getattr(entity, "first_name", None)
        last_name = getattr(entity, "last_name", None)
        title = getattr(entity, "title", None)
        username = getattr(entity, "username", None)
        if first_name:
            return f"{first_name} {last_name}".strip() if last_name else str(first_name)
        if title:
            return str(title)
        if username:
            return f"@{username}"
    except Exception as exc:
        logger.debug("Failed to resolve entity %s: %s", chat_id, exc)

    for item in reversed(items):
        sender = str(item.get("sender") or "").strip()
        if sender:
            return sender
    return str(chat_id)


def _format_dialog_excerpt(items: list[dict], limit: int = 18) -> str:
    excerpt = items[-limit:]
    lines: list[str] = []
    for item in excerpt:
        role = "аня" if item.get("role") == "assistant" else "собеседник"
        content = str(item.get("content") or "").strip().replace("\n", " ")
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _fallback_today_report(summary_rows: list[dict], owner_query: str, today_label: str) -> str:
    lines = [
        f"ОТЧЕТ_ЗА_СЕГОДНЯ: {today_label}",
        f"ВОПРОС_ВЛАДЕЛЬЦА: {owner_query}",
        f"УНИКАЛЬНЫХ_СОБЕСЕДНИКОВ: {len(summary_rows)}",
        "СОБЕСЕДНИКИ:",
    ]
    if not summary_rows:
        lines.append("нет данных")
    else:
        for idx, row in enumerate(summary_rows, start=1):
            lines.append(
                f"{idx} | id={row['chat_id']} | имя={row['name']} | сообщений_всего={row['total']} | "
                f"сообщений_собеседника={row['incoming']} | сообщений_ани={row['outgoing']} | "
                "контекст=недостаточно данных без LLM анализа | шанс_подарка=1%"
            )
    lines.append("ИНТЕРЕСНЫЙ_ФАКТ: сегодня отчёт собран без LLM-анализа")
    lines.append("ОБЩИЙ_ВЫВОД: активность за сегодня собрана, но качественный разбор недоступен")
    return "\n".join(lines)


async def _build_today_report(client, owner_query: str) -> str:
    now = now_msk()
    dialogs = history.get_today_dialogs(now=now)
    owner_chat_id = str(Config.OWNER_ID) if Config.OWNER_ID else None

    summary_rows: list[dict] = []
    transcript_blocks: list[str] = []

    for chat_id, items in dialogs.items():
        if owner_chat_id and chat_id == owner_chat_id:
            continue
        incoming = [item for item in items if item.get("role") == "user"]
        if not incoming:
            continue

        chat_id_int = int(chat_id)
        outgoing = [item for item in items if item.get("role") == "assistant"]
        name = await _entity_display_name(client, chat_id_int, items)
        row = {
            "chat_id": chat_id_int,
            "name": name,
            "total": len(items),
            "incoming": len(incoming),
            "outgoing": len(outgoing),
        }
        summary_rows.append(row)
        transcript_blocks.append(
            "\n".join(
                [
                    f"CHAT_ID: {chat_id_int}",
                    f"ИМЯ: {name}",
                    f"СООБЩЕНИЙ_ВСЕГО: {row['total']}",
                    f"СООБЩЕНИЙ_СОБЕСЕДНИКА: {row['incoming']}",
                    f"СООБЩЕНИЙ_АНИ: {row['outgoing']}",
                    "ФРАГМЕНТ_ДИАЛОГА:",
                    _format_dialog_excerpt(items),
                ]
            )
        )

    today_label = now.strftime("%Y-%m-%d")
    if not summary_rows:
        return _fallback_today_report([], owner_query, today_label)

    system = (
        "Ты аналитик личных переписок Ани. "
        "Ответь строго на русском и строго по заданному формату. "
        "Не добавляй markdown, пояснений, вступлений или списков вне шаблона. "
        "Не меняй числовые значения, которые уже даны во входе. "
        "Для каждого собеседника кратко опиши контекст общения. "
        "Поле шанс_подарка должно быть целым числом от 1 до 100 с символом %. "
        "Оценивай шанс материального подарка осторожно и только по контексту."
    )

    raw_stats = "\n".join(
        f"- id={row['chat_id']} | имя={row['name']} | сообщений_всего={row['total']} | "
        f"сообщений_собеседника={row['incoming']} | сообщений_ани={row['outgoing']}"
        for row in summary_rows
    )
    transcripts = "\n\n-----\n\n".join(transcript_blocks)
    user_payload = "\n\n".join(
        [
            f"ДАТА_ОТЧЕТА: {today_label}",
            f"ВОПРОС_ВЛАДЕЛЬЦА: {owner_query}",
            f"УНИКАЛЬНЫХ_СОБЕСЕДНИКОВ: {len(summary_rows)}",
            "ЧИСЛОВЫЕ_ДАННЫЕ:",
            raw_stats,
            "ДИАЛОГИ_ЗА_СЕГОДНЯ:",
            transcripts,
            (
                "Верни ответ строго в таком виде:\n"
                f"ОТЧЕТ_ЗА_СЕГОДНЯ: {today_label}\n"
                f"ВОПРОС_ВЛАДЕЛЬЦА: {owner_query}\n"
                f"УНИКАЛЬНЫХ_СОБЕСЕДНИКОВ: {len(summary_rows)}\n"
                "СОБЕСЕДНИКИ:\n"
                "1 | id=... | имя=... | сообщений_всего=... | сообщений_собеседника=... | сообщений_ани=... | контекст=... | шанс_подарка=...%\n"
                "2 | id=... | имя=... | сообщений_всего=... | сообщений_собеседника=... | сообщений_ани=... | контекст=... | шанс_подарка=...%\n"
                "ИНТЕРЕСНЫЙ_ФАКТ: ...\n"
                "ОБЩИЙ_ВЫВОД: ..."
            ),
        ]
    )

    response = await complete_chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user_payload},
        ],
        temperature=0.2,
        max_tokens=1400,
        log_success=False,
        cheap_only=True,
    )
    return response or _fallback_today_report(summary_rows, owner_query, today_label)


def register(client: TelegramClient) -> None:
    owner_filter = None
    if Config.OWNER_ID:
        owner_filter = Config.OWNER_ID

    @client.on(events.NewMessage(incoming=True, from_users=owner_filter, pattern=r"^\.ping$"))
    async def ping(event):
        logger.info("Ping command received from %s", event.sender_id)
        if not owner_check(event):
            logger.info("Ping: not owner")
            return
        await event.edit(TextConstants.PING)

    @client.on(events.NewMessage(incoming=True, from_users=owner_filter, pattern=r"^\.help$"))
    async def help_dot(event):
        if not owner_check(event):
            return
        await event.edit(TextConstants.HELP_DOT, parse_mode="md")

    @client.on(events.NewMessage(incoming=True, from_users=owner_filter, pattern=r"^\.auto(?:\s+(.*))?$"))
    async def auto_cmd(event):
        if not owner_check(event):
            return
        arg = (event.pattern_match.group(1) or "").strip().lower()
        if not arg:
            await event.edit(TextConstants.AUTO_USAGE, parse_mode="md")
            return
        if arg == "off":
            state.set("system_status", "off")
            await event.edit("🔴 Auto replies off.", parse_mode="md")
        elif arg == "ai":
            state.set("system_status", "ai")
            await event.edit("🧠 Auto replies AI.", parse_mode="md")
        elif arg == "static":
            state.set("system_status", "static")
            await event.edit("🟢 Auto replies static.", parse_mode="md")
        else:
            state.set("system_status", "static")
            state.set("static_message", event.pattern_match.group(1).strip())
            await event.edit("📝 Static text updated.", parse_mode="md")

    @client.on(events.NewMessage(incoming=True, from_users=owner_filter, pattern=r"^\.mode(?:\s+(.*))?$"))
    async def mode_cmd(event):
        if not owner_check(event):
            return
        arg = (event.pattern_match.group(1) or "").strip().lower()
        modes = ("sleep", "work", "gaming", "default")
        if arg in modes:
            state.set("context_mode", arg)
            await event.edit(f"🎭 Mode: **{arg}**", parse_mode="md")
        else:
            await event.edit(f"Modes: `{', '.join(modes)}`")

    @client.on(events.NewMessage(incoming=True, from_users=owner_filter, pattern=r"^\.ghost(?:\s+(.*))?$"))
    async def ghost_cmd(event):
        if not owner_check(event):
            return
        arg = (event.pattern_match.group(1) or "").strip().lower()
        if arg in ("on", "1", "true", "yes"):
            state.set("ghost_mode", True)
            await event.edit("👻 Ghost on (`!ask` still works).", parse_mode="md")
        elif arg in ("off", "0", "false", "no"):
            state.set("ghost_mode", False)
            await event.edit("👻 Ghost off.", parse_mode="md")
        else:
            cur = "on" if state.get("ghost_mode") else "off"
            await event.edit(f"Ghost is **{cur}**. Use `.ghost on` / `.ghost off`", parse_mode="md")

    @client.on(events.NewMessage(incoming=True, from_users=owner_filter, pattern=r"^\.human(?:\s+(.*))?$"))
    async def human_toggle(event):
        if not owner_check(event):
            return
        arg = (event.pattern_match.group(1) or "").strip().lower()
        if arg in ("on", "1", "true", "yes"):
            state.set("human_mode", True)
            await event.edit("✨ Human style on.", parse_mode="md")
        elif arg in ("off", "0", "false", "no"):
            state.set("human_mode", False)
            await event.edit("✨ Human style off.", parse_mode="md")
        else:
            hm = state.get("human_mode", True)
            await event.edit(f"Human style: **{'on' if hm else 'off'}**", parse_mode="md")

    @client.on(events.NewMessage(incoming=True, from_users=owner_filter, pattern=r"^\.vip(?:\s+(add|rem|list))?$"))
    async def vip_cmd(event):
        if not owner_check(event):
            return
        sub = event.pattern_match.group(1)
        vips = list(state.get("vip_list") or [])
        if not sub:
            await event.edit("Usage: `.vip add` / `.vip rem` / `.vip list` (reply for add/rem)")
            return
        if sub == "list":
            await event.edit(f"VIP count: {len(vips)}")
            return
        reply = await event.get_reply_message()
        if not reply or not reply.sender_id:
            await event.edit("Reply to a user.")
            return
        uid = int(reply.sender_id)
        if sub == "add":
            if uid not in vips:
                vips.append(uid)
                state.set("vip_list", vips)
            await event.edit("VIP added.")
        elif sub == "rem":
            if uid in vips:
                vips.remove(uid)
                state.set("vip_list", vips)
            await event.edit("VIP removed.")

    @client.on(events.NewMessage(incoming=True, from_users=owner_filter, pattern=r"^!help$"))
    async def help_bang(event):
        if not owner_check(event) or not Config.ENABLE_OWNER_ASK:
            return
        await event.edit(TextConstants.HELP_BANG, parse_mode="md")

    @client.on(events.NewMessage(incoming=True, from_users=owner_filter, pattern=r"^!ask(?:\s+(.*))?$"))
    async def ask_cmd(event):
        if not owner_check(event) or not Config.ENABLE_OWNER_ASK:
            return
        query = event.pattern_match.group(1)
        reply_to_id = event.reply_to_msg_id

        user_text = (query or "").strip()
        if not user_text:
            user_text = ASK_AUTO

        anchor = event
        reply_msg = None
        if event.is_reply:
            reply_msg = await event.get_reply_message()
            if reply_msg:
                anchor = reply_msg

        if user_text == ASK_AUTO and not reply_msg:
            await event.delete()
            return

        chat_id = event.chat_id
        hist = history.get_recent(chat_id)
        analyze_n = min(28, len(hist)) if hist else None
        hist_analyze = format_history_for_prompt(hist, max_items=analyze_n)

        situational = await build_situational_system_appendix(
            history_text=hist_analyze,
            peer_display_name=None,
            anchor_event=anchor,
        )
        system = build_system_prompt(
            context_mode=str(state.get("context_mode", "default")),
            human_mode=bool(state.get("human_mode", True)),
            situational_appendix=situational,
        )

        schedule_header = "\n".join(
            [
                format_line_now_msk(),
                format_event_time_msk(anchor),
                f"Эвристика занятости сейчас: {heuristic_activity_and_place(now_msk())}",
            ]
        )

        if event.is_reply and reply_msg:
            chain = await get_reply_chain_text(event.client, reply_msg)
            core_user = (
                f"{chain}\n\nИнструкция владельца (не цитируй дословно, ответь в ветке как Аня по правилам):\n{user_text}"
            )
        else:
            core_user = user_text

        user_payload = f"{schedule_header}\n\n{core_user}"

        await event.delete()

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_payload},
        ]
        response = await complete_chat(messages, cheap_only=True)
        if not response:
            response = "…"
        if reply_to_id:
            await event.client.send_message(event.chat_id, response, reply_to=reply_to_id)
        else:
            await event.client.send_message(event.chat_id, response)

    @client.on(events.NewMessage(incoming=True, from_users=owner_filter, pattern=r"^!today(?:\s+(.*))?$"))
    async def today_cmd(event):
        if not owner_check(event):
            return
        owner_query = (event.pattern_match.group(1) or "").strip() or TODAY_DEFAULT_QUERY
        await event.delete()
        report = await _build_today_report(event.client, owner_query)
        await event.client.send_message(event.chat_id, report)

    @client.on(events.NewMessage(incoming=True, from_users=owner_filter, pattern=r"^!clear(?:\s+(\d+))$"))
    async def clear_user_history_cmd(event):
        if not owner_check(event):
            return
        raw_user_id = (event.pattern_match.group(1) or "").strip()
        await event.delete()
        try:
            user_id = int(raw_user_id)
        except ValueError:
            await event.client.send_message(event.chat_id, "неверный user_id")
            return

        history.clear_chat(user_id)
        await event.client.send_message(
            event.chat_id,
            f"история для user_id {user_id} очищена",
        )

    @client.on(events.NewMessage(incoming=True, from_users=owner_filter, pattern=r"^\.bl(?:\s+(add|rem|list))?$"))
    async def blacklist_cmd(event):
        if not owner_check(event):
            return
        sub = event.pattern_match.group(1)
        blocked = list(state.get("blacklist") or [])
        if not sub:
            await event.edit("Usage: `.bl add` / `.bl rem` / `.bl list` (reply for add/rem)")
            return
        if sub == "list":
            await event.edit(f"Blocked: {len(blocked)}")
            return
        reply = await event.get_reply_message()
        if not reply or not reply.sender_id:
            await event.edit("Reply to a user.")
            return
        uid = int(reply.sender_id)
        if sub == "add":
            if uid not in blocked:
                blocked.append(uid)
                state.set("blacklist", blocked)
            await event.edit("Added to blocklist.")
        elif sub == "rem":
            if uid in blocked:
                blocked.remove(uid)
                state.set("blacklist", blocked)
            await event.edit("Removed from blocklist.")

    @client.on(events.NewMessage(incoming=True, from_users=owner_filter, pattern=r"^\.clearhistory$"))
    async def clear_history_cmd(event):
        if not owner_check(event):
            return
        history.clear_chat(event.chat_id)
        await event.edit("History cleared for this chat (local JSON).")
