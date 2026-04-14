"""
Runtime state persisted to JSON (auto mode, mood, ghost, VIP, blacklist, metrics).
English comments only as requested.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from config import Config

logger = logging.getLogger(__name__)

_STATE_PATH = Config.DATA_DIR / "state.json"
_lock = threading.Lock()


def _default_state() -> dict[str, Any]:
    return {
        "system_status": "ai" if Config.AUTO_START_ENABLED else "off",
        "context_mode": "default",
        "static_message": "hey im kinda busy rn, text u later 💬",
        "ghost_mode": Config.GHOST_MODE_DEFAULT,
        "vip_list": [],
        "blacklist": [],
        "cooldowns": {},
    "cooldowns_mono": {},
        "human_mode": True,
        "metrics": {"total_replies": 0, "llm_errors": 0},
    }


class StateStore:
    def __init__(self, path: Path = _STATE_PATH) -> None:
        self.path = path
        self._data = _default_state()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._save()
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                base = _default_state()
                base.update(loaded)
                # Clear invalid monotonic cooldowns (e.g., after restart)
                import time
                now_mono = time.monotonic()
                cooldowns_mono = base.get("cooldowns_mono", {})
                base["cooldowns_mono"] = {
                    k: v for k, v in cooldowns_mono.items()
                    if isinstance(v, (int, float)) and 0 <= v <= now_mono + 3600
                }
                self._data = base
        except Exception as exc:
            logger.error("Failed to load state.json: %s", exc)
            self._data = _default_state()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.error("Failed to save state.json: %s", exc)

    def get(self, key: str, default: Any = None) -> Any:
        with _lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with _lock:
            self._data[key] = value
            self._save()

    def update_metric(self, name: str, delta: int = 1) -> None:
        with _lock:
            metrics = self._data.setdefault("metrics", {})
            if name in metrics:
                metrics[name] = int(metrics[name]) + delta
            else:
                metrics[name] = delta
            self._save()


state = StateStore()
