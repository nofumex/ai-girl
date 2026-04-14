"""
Human-like delays, optional typos, and message splitting.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Sequence

from config import Config

logger = logging.getLogger(__name__)

_REACTION_EMOJIS: Sequence[str] = ("👍", "❤", "😂", "🔥", "🙏")

def clean_telegram_garbage(text: str) -> str:
    """Remove mangled Telegram emoji codes from text."""
    # Remove common garbled patterns
    text = text.replace("р'", "")  # р'
    text = text.replace("р`", "")  # р`
    # Remove any standalone orphan quote-like chars after removed text
    text = text.replace("¬", "")   # leftover ¬ after above removed
    return text

# Light keyboard-neighbor typos (both Latin and Cyrillic-ish layout hints)
_SWAP_PAIRS: list[tuple[str, str]] = [
    ("а", "я"),
    ("о", "а"),
    ("е", "и"),
    ("и", "й"),
    ("ы", "и"),
    ("t", "y"),
    ("a", "s"),
    ("o", "p"),
]


def maybe_apply_typos(text: str) -> str:
    if random.random() > Config.TYPO_MESSAGE_PROBABILITY:
        return text
    chars = list(text)
    if len(chars) < 4:
        return text
    edits = random.randint(1, 2)
    for _ in range(edits):
        i = random.randint(0, len(chars) - 2)
        a, b = chars[i], chars[i + 1]
        if random.random() < 0.55:
            chars[i], chars[i + 1] = b, a
        else:
            for old, new in _SWAP_PAIRS:
                if chars[i].lower() == old and random.random() < 0.5:
                    chars[i] = new if chars[i].islower() else new.upper()
                    break
    return "".join(chars)


async def pre_read_delay() -> None:
    lo, hi = Config.READ_DELAY_MIN, Config.READ_DELAY_MAX
    if hi < lo:
        lo, hi = hi, lo
    await asyncio.sleep(random.uniform(lo, hi))


async def typing_duration_for_text(text: str) -> float:
    lo, hi = Config.TYPING_PER_CHAR_MIN, Config.TYPING_PER_CHAR_MAX
    if hi < lo:
        lo, hi = hi, lo
    per = random.uniform(lo, hi)
    return max(0.6, min(25.0, len(text) * per))


async def maybe_extra_pause() -> None:
    if random.random() > Config.EXTRA_PAUSE_PROBABILITY:
        return
    lo, hi = Config.EXTRA_PAUSE_MIN, Config.EXTRA_PAUSE_MAX
    if hi < lo:
        lo, hi = hi, lo
    await asyncio.sleep(random.uniform(lo, hi))


async def inter_bot_burst_pause() -> None:
    """Short pause between consecutive outgoing messages in the same turn."""
    lo, hi = Config.BOT_BURST_PAUSE_MIN, Config.BOT_BURST_PAUSE_MAX
    if hi < lo:
        lo, hi = hi, lo
    await asyncio.sleep(random.uniform(lo, hi))


def split_bot_bubbles(text: str) -> list[str]:
    """
    Turn model output into 1-2 Telegram bubbles with no inner line breaks.
    Any explicit newline from the model becomes a new bubble candidate.
    """
    text = (text or "").strip()
    if not text:
        return []

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    line_parts = [re.sub(r"\s+", " ", part).strip() for part in normalized.split("\n") if part.strip()]
    if not line_parts:
        return [re.sub(r"\s+", " ", text).strip()]

    if len(line_parts) == 1:
        single = line_parts[0]
        if len(single) > 420:
            return _split_long_text_max_two(single)
        return [single]

    return _merge_bubbles_max_two(line_parts)


def _merge_bubbles_max_two(parts: list[str]) -> list[str]:
    if len(parts) <= 2:
        return parts

    first = parts[0]
    if len(first) < 30 and len(parts) >= 3:
        merged_first = f"{first} {parts[1]}".strip()
        merged_rest = " ".join(parts[2:]).strip()
        return [merged_first, merged_rest] if merged_rest else [merged_first]

    rest = " ".join(parts[1:]).strip()
    return [first, rest] if rest else [first]


def _split_long_text_max_two(text: str) -> list[str]:
    """Fallback: break an overly long single paragraph into up to 2 pieces."""
    chunks = split_into_chunks(text)
    return chunks[:2] if chunks else []


def split_into_chunks(text: str) -> list[str]:
    """
    Sometimes split one reply into 1-2 short Telegram messages.
    """
    text = text.strip()
    if len(text) < 80 or random.random() > Config.SPLIT_MESSAGE_PROBABILITY:
        return [text]
    parts = re.split(r"(?<=[\.\!\?…])\s+", text, maxsplit=1)
    if len(parts) == 2 and len(parts[0].strip()) >= 8 and len(parts[1].strip()) >= 8:
        return [parts[0].strip(), parts[1].strip()]
    mid = len(text) // 2
    spacer = text.rfind(" ", 0, mid)
    if spacer > 10:
        return [text[:spacer].strip(), text[spacer:].strip()]
    return [text]


async def maybe_react_to_message(event, probability: float | None = None) -> None:
    prob = probability if probability is not None else Config.REACTION_PROBABILITY
    if prob <= 0 or random.random() > prob:
        return
    emoji = random.choice(_REACTION_EMOJIS)
    try:
        msg = event.message
        if hasattr(msg, "react"):
            await msg.react(emoji)
            return
        from telethon.tl.functions.messages import SendReactionRequest
        from telethon.tl.types import ReactionEmoji

        await event.client(
            SendReactionRequest(
                peer=await event.get_input_chat(),
                msg_id=event.id,
                reaction=[ReactionEmoji(emoticon=emoji)],
            )
        )
    except Exception as exc:
        logger.debug("Reaction skipped: %s", exc)
