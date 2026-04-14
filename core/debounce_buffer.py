"""
Per-(chat_id, sender_id) debounced message batching for natural reply timing.
English comments only.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, DefaultDict, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BufferKey = Tuple[int, int]  # (chat_id, sender_id)


@dataclass
class BufferedIncoming:
    """One user line collected before the debounce window closes."""

    text: str
    message_id: int
    event: Any  # Telethon NewMessage.Event (avoid circular import)
    image_data: bytes | None = None


class DebounceBuffer:
    """
    Buffers consecutive texts from the same peer in the same chat.
    Cancels/reschedules an asyncio task on each push; flush runs after quiet period.
    """

    def __init__(
        self,
        *,
        delay_min: float,
        delay_max: float,
    ) -> None:
        self._delay_min = delay_min
        self._delay_max = delay_max
        self._lock = asyncio.Lock()
        self._buffers: DefaultDict[BufferKey, List[BufferedIncoming]] = defaultdict(list)
        self._tasks: Dict[BufferKey, asyncio.Task[None]] = {}
        self._seen_message_ids: DefaultDict[BufferKey, set[int]] = defaultdict(set)
        self._flushing: set[BufferKey] = set()

    def _key(self, chat_id: int, sender_id: int) -> BufferKey:
        return (chat_id, sender_id)

    async def push(
        self,
        chat_id: int,
        sender_id: int,
        item: BufferedIncoming,
        on_flush: Callable[[BufferKey, List[BufferedIncoming]], Awaitable[None]],
    ) -> None:
        key = self._key(chat_id, sender_id)
        async with self._lock:
            if item.message_id in self._seen_message_ids[key]:
                logger.debug("Skipping duplicate message_id %d for key %s", item.message_id, key)
                return
            self._seen_message_ids[key].add(item.message_id)
            self._buffers[key].append(item)
            logger.info("Buffered msg %d for key %s (total %d)", item.message_id, key, len(self._buffers[key]))
            
            if key in self._flushing:
                logger.info("Already flushing for key %s, just buffering", key)
                old = self._tasks.pop(key, None)
                if old is not None and not old.done():
                    old.cancel()
                self._tasks[key] = asyncio.create_task(
                    self._wait_and_flush(key, on_flush),
                    name=f"debounce-{key}",
                )
                return
            
            old = self._tasks.pop(key, None)
            if old is not None and not old.done():
                old.cancel()

            self._tasks[key] = asyncio.create_task(
                self._wait_and_flush(key, on_flush),
                name=f"debounce-{key}",
            )

    async def _wait_and_flush(
        self,
        key: BufferKey,
        on_flush: Callable[[BufferKey, List[BufferedIncoming]], Awaitable[None]],
    ) -> None:
        lo, hi = self._delay_min, self._delay_max
        if hi < lo:
            lo, hi = hi, lo
        delay = random.uniform(lo, hi)
        logger.info("Debounce wait %.1fs for key %s", delay, key)
        try:
            await asyncio.sleep(delay)
            async with self._lock:
                batch = self._buffers.pop(key, [])
                self._tasks.pop(key, None)
                self._flushing.add(key)
            logger.info("Flushing %d messages for key %s", len(batch), key)
            if batch:
                await on_flush(key, batch)
            async with self._lock:
                self._flushing.discard(key)
                self._seen_message_ids.pop(key, None)
        except asyncio.CancelledError:
            async with self._lock:
                self._flushing.discard(key)
            logger.debug("Debounce cancelled for key %s", key)
            return
        except Exception:
            async with self._lock:
                self._flushing.discard(key)
            logger.exception("Debounce flush failed for key=%s", key)


# Module-level coordinator (delay from Config at register time)
_coordinator: Optional[DebounceBuffer] = None


def get_debounce_buffer() -> DebounceBuffer:
    if _coordinator is None:
        raise RuntimeError("DebounceBuffer not initialized; call init_debounce_buffer first")
    return _coordinator


def init_debounce_buffer(*, delay_min: float, delay_max: float) -> DebounceBuffer:
    global _coordinator
    _coordinator = DebounceBuffer(delay_min=delay_min, delay_max=delay_max)
    return _coordinator
