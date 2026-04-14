"""
Per-chat message history stored as JSON for LLM context.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import Config

logger = logging.getLogger(__name__)

_HISTORY_PATH = Config.DATA_DIR / "chat_history.json"
_DAILY_PATH = Config.DATA_DIR / "daily_dialogs.json"
_lock = threading.Lock()


class HistoryStore:
    def __init__(self, path: Path = _HISTORY_PATH) -> None:
        self.path = path
        self._chats: dict[str, deque[dict[str, Any]]] = {}
        self._daily_path = _DAILY_PATH
        self._daily: dict[str, list[dict[str, Any]]] = {}
        self._load()
        self._load_daily()
        self._bootstrap_daily_from_recent()

    def _max_len(self) -> int:
        n = Config.HISTORY_MAX_MESSAGES
        low = Config.HISTORY_MIN_MESSAGES
        if low > n:
            low, n = n, low
        return max(n, low)

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return
            max_len = self._max_len()
            for chat_id, items in raw.items():
                if not isinstance(items, list):
                    continue
                dq: deque[dict[str, Any]] = deque(maxlen=max_len)
                for it in items[-max_len:]:
                    if isinstance(it, dict) and "role" in it and "content" in it:
                        dq.append(it)
                self._chats[str(chat_id)] = dq
        except Exception as exc:
            logger.error("Failed to load chat_history.json: %s", exc)

    def _save_unlocked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serializable = {k: list(v) for k, v in self._chats.items()}
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2, ensure_ascii=False)

    def _load_daily(self) -> None:
        if not self._daily_path.exists():
            return
        try:
            with self._daily_path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return
            for chat_id, items in raw.items():
                if isinstance(items, list):
                    self._daily[str(chat_id)] = [it for it in items if isinstance(it, dict)]
            self._prune_daily_unlocked()
        except Exception as exc:
            logger.error("Failed to load daily_dialogs.json: %s", exc)

    def _save_daily_unlocked(self) -> None:
        self._daily_path.parent.mkdir(parents=True, exist_ok=True)
        with self._daily_path.open("w", encoding="utf-8") as f:
            json.dump(self._daily, f, indent=2, ensure_ascii=False)

    def _bootstrap_daily_from_recent(self) -> None:
        if self._daily or not self._chats:
            return
        seeded = False
        for chat_id, items in self._chats.items():
            copied = [dict(it) for it in items if isinstance(it, dict)]
            if copied:
                self._daily[chat_id] = copied
                seeded = True
        if seeded:
            self._prune_daily_unlocked()
            self._save_daily_unlocked()

    def _prune_daily_unlocked(self, keep_days: int = 7) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, keep_days))
        keep: dict[str, list[dict[str, Any]]] = {}
        for chat_id, items in self._daily.items():
            filtered: list[dict[str, Any]] = []
            for it in items:
                ts = it.get("ts")
                try:
                    dt = datetime.fromisoformat(str(ts))
                except Exception:
                    continue
                if dt >= cutoff:
                    filtered.append(it)
            if filtered:
                keep[chat_id] = filtered
        self._daily = keep

    def append(
        self,
        chat_id: int | str,
        role: str,
        content: str,
        *,
        sender_name: str | None = None,
        sender_id: int | None = None,
    ) -> None:
        if not content.strip():
            return
        ts = datetime.now(timezone.utc).isoformat()
        entry = {
            "role": role,
            "content": content.strip(),
            "ts": ts,
        }
        if sender_name:
            entry["sender"] = sender_name
        if sender_id is not None:
            entry["sender_id"] = int(sender_id)
        key = str(chat_id)
        with _lock:
            max_len = self._max_len()
            if key not in self._chats or self._chats[key].maxlen != max_len:
                old = list(self._chats.get(key, []))
                self._chats[key] = deque(old[-max_len:], maxlen=max_len)
            self._chats[key].append(entry)
            self._daily.setdefault(key, []).append(dict(entry))
            self._prune_daily_unlocked()
            self._save_unlocked()
            self._save_daily_unlocked()

    def get_recent(self, chat_id: int | str) -> list[dict[str, Any]]:
        key = str(chat_id)
        with _lock:
            return list(self._chats.get(key, deque()))

    def get_today_dialogs(
        self,
        *,
        now: datetime | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        base = now or datetime.now(timezone.utc)
        today = base.date()
        base_tz = base.tzinfo or timezone.utc
        with _lock:
            out: dict[str, list[dict[str, Any]]] = {}
            for chat_id, items in self._daily.items():
                filtered: list[dict[str, Any]] = []
                for it in items:
                    ts = it.get("ts")
                    try:
                        dt = datetime.fromisoformat(str(ts))
                    except Exception:
                        continue
                    if dt.astimezone(base_tz).date() == today:
                        filtered.append(dict(it))
                if filtered:
                    out[chat_id] = filtered
            return out

    def clear_chat(self, chat_id: int | str) -> None:
        key = str(chat_id)
        with _lock:
            self._chats.pop(key, None)
            self._daily.pop(key, None)
            self._save_unlocked()
            self._save_daily_unlocked()


history = HistoryStore()
