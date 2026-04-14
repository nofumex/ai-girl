"""
Moscow time, heuristic activity, and parallel LLM analyzers for system-prompt enrichment.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from core.llm import complete_chat

logger = logging.getLogger(__name__)

try:
    MSK = ZoneInfo("Europe/Moscow")
except Exception:
    # Windows/minimal Python installs may lack tzdata; Moscow has no DST (UTC+3).
    MSK = timezone(timedelta(hours=3))
    logger.warning("ZoneInfo Europe/Moscow unavailable; using fixed UTC+3 fallback. Install 'tzdata' for IANA zones.")


def now_msk() -> datetime:
    return datetime.now(tz=MSK)


def _to_msk(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK)


def format_line_now_msk() -> str:
    return f"Сейчас по Москве: {now_msk().strftime('%Y-%m-%d %H:%M:%S')} (МСК)"


def format_batch_message_times_msk(batch: Sequence[Any]) -> str:
    """batch items must have .event.date (Telethon)."""
    parts: list[str] = []
    for i, item in enumerate(batch, start=1):
        ev = getattr(item, "event", None)
        if ev is None:
            continue
        raw = getattr(ev, "date", None)
        if raw is None:
            continue
        msk = _to_msk(raw)
        parts.append(f"сообщение {i}: {msk.strftime('%Y-%m-%d %H:%M:%S')} МСК")
    if not parts:
        return "Время последних сообщений собеседника (МСК): неизвестно (нет дат в событиях)."
    return "Время последних сообщений собеседника (МСК):\n" + "\n".join(parts)


def format_event_time_msk(event) -> str:
    raw = getattr(event, "date", None)
    if raw is None:
        return "Время сообщения (МСК): неизвестно."
    msk = _to_msk(raw)
    return f"Время сообщения (МСК): {msk.strftime('%Y-%m-%d %H:%M:%S')}"


def heuristic_activity_and_place(dt_msk: datetime) -> str:
    """
    Rough schedule for Anya: study, side job, home, walk — not ground truth, hint for analyzers.
    """
    h = dt_msk.hour
    wd = dt_msk.weekday()  # 0 = Monday
    is_weekend = wd >= 5

    if 0 <= h < 6:
        return "ночь: обычно дома в однушке телефон сериал или не спит"
    if 6 <= h < 10:
        return "раннее утро: встаёт кофе овсяное собирается в универ или за ноутом дистант"
    if 10 <= h < 14:
        if is_weekend:
            return "день выходной: может договориться с Машей шопинг или лежит дома"
        return "день будни: дистант или таргет в вк/тг заказы если есть"
    if 14 <= h < 18:
        if is_weekend:
            return "после обеда выходной: прогулка район парк или дома отдых"
        return "после обеда будни: подработка реклама или пары записи дедлайны"
    if 18 <= h < 22:
        return "вечер: метро гулять Патриаршие/Чистые пруды или дома устала после дня"
    return "поздний вечер: дома ужин сериал готовится ко сну"


ANALYZER_MOOD_SYSTEM = """Ты служебный аналитик (не Аня). По истории переписки кратко опиши настроение и тон девушки к этому человеку сейчас.
Учти подсказку «чем обычно занята по времени суток» — на работе/учёбе чаще скучнее и устала, на прогулке с подругой веселее, дома нейтральнее.
3–6 коротких фраз на русском, без списков с цифрами, без советов как писать ответ."""


ANALYZER_MATERIAL_SYSTEM = """Ты служебный аналитик. По истории определи: просила ли девушка у этого человека деньги подарок оплату такси маникюр и т п.
Если да — насколько давно и насколько прямо. Насколько сейчас уместна очень редкая невзначайная просьба (правило примерно раз в 7–10 дней общения).
3–6 коротких фраз на русском, без списков с цифрами."""


ANALYZER_EXTRA_SYSTEM = """Ты служебный аналитик. По истории выдели важное: повторяющиеся темы уровень доверия насколько человек вовлечён
есть ли риск что он проверяет на бота что лучше не говорить сейчас общая динамика (сближение отдаление нейтрально).
3–6 коротких фраз на русском без списков с цифрами."""


async def _one_analyzer(system: str, user_body: str) -> str:
    text = await complete_chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user_body},
        ],
        temperature=0.35,
        max_tokens=400,
        log_success=False,
        cheap_only=True,
    )
    return (text or "").strip() or "(нет данных)"


async def run_parallel_context_analyzers(
    *,
    history_text: str,
    activity_hint: str,
    peer_display_name: str | None,
) -> tuple[str, str, str]:
    peer = peer_display_name or "неизвестно"
    base_user = (
        f"Собеседник: {peer}\n\n"
        f"Подсказка занятости по времени суток (эвристика, не факт):\n{activity_hint}\n\n"
        f"История (собеседник / я):\n{history_text}"
    )
    # Run sequentially - same provider for all 3 analyzers
    mood = await _one_analyzer(ANALYZER_MOOD_SYSTEM, base_user)
    material = await _one_analyzer(ANALYZER_MATERIAL_SYSTEM, base_user)
    extra = await _one_analyzer(ANALYZER_EXTRA_SYSTEM, base_user)
    logger.debug("Context analyzers done (mood/material/extra).")
    return mood, material, extra


async def build_situational_system_appendix(
    *,
    history_text: str,
    peer_display_name: str | None,
    batch: Sequence[Any] | None = None,
    anchor_event: Any | None = None,
) -> str:
    """
    Full block appended under the main persona system prompt.
    Use `batch` for autoresponder bursts; `anchor_event` for owner !ask; or neither for "now" only.
    """
    dt = now_msk()
    activity = heuristic_activity_and_place(dt)
    if batch:
        times_block = format_batch_message_times_msk(batch)
    elif anchor_event is not None:
        times_block = format_event_time_msk(anchor_event)
    else:
        times_block = format_line_now_msk()

    mood, material, extra = await run_parallel_context_analyzers(
        history_text=history_text,
        activity_hint=activity,
        peer_display_name=peer_display_name,
    )

    rapport_line = (
        "Напоминание для роли: держи дистанцию как в переписке с живым человеком; "
        "если аналитика говорит о напряжении — не перегибай с флиртом и просьбами."
    )

    parts = [
        format_line_now_msk(),
        times_block,
        f"Чем сейчас по типичному расписанию могла бы заниматься (эвристика по часу в Москве): {activity}",
        f"Настроение и тон к этому человеку (анализ модели):\n{mood}",
        f"Просьбы о деньгах/подарках и уместность сейчас (анализ модели):\n{material}",
        f"Дополнительный контекст переписки (анализ модели):\n{extra}",
        rapport_line,
    ]
    return "\n\n".join(parts)
