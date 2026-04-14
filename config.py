"""
Central configuration loaded from environment variables.
Do not commit real API keys; use a local .env file only.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(part.strip() for part in raw.split(",") if part.strip())


class Config:
    ROOT_DIR: Path = ROOT_DIR
    DATA_DIR: Path = DATA_DIR

    API_ID: int = _int("API_ID", 0)
    API_HASH: str | None = os.getenv("API_HASH")
    SESSION_NAME: str = os.getenv("SESSION_NAME", "ai_girl_session")
    SESSION_STRING: str | None = os.getenv("SESSION_STRING")
    OWNER_ID: int = _int("OWNER_ID", 0)

    GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")
    GEMINI_API_KEY_2: str | None = os.getenv("GEMINI_API_KEY_2")
    GEMINI_API_KEY_3: str | None = os.getenv("GEMINI_API_KEY_3")
    GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
    GROQ_API_KEY_2: str | None = os.getenv("GROQ_API_KEY_2")
    GROQ_API_KEY_3: str | None = os.getenv("GROQ_API_KEY_3")
    CEREBRAS_API_KEY: str | None = os.getenv("CEREBRAS_API_KEY")
    SILICONFLOW_API_KEY: str | None = os.getenv("SILICONFLOW_API_KEY")

    # Gemini models with high throughput - use flash-lite for better rate limits
    MODEL_GEMINI: str = os.getenv("MODEL_GEMINI", "gemini/gemini-2.5-flash-lite")
    MODEL_GEMINI_2: str = os.getenv("MODEL_GEMINI_2", "gemini/gemini-2.5-flash-lite")
    MODEL_GEMINI_3: str = os.getenv("MODEL_GEMINI_3", "gemini/gemini-2.5-flash-lite")
    # Groq models with high RPM/RPD - llama-3.3-70b-versatile has good throughput
    MODEL_GROQ: str = os.getenv("MODEL_GROQ", "groq/llama-3.3-70b-versatile")
    MODEL_GROQ_2: str = os.getenv("MODEL_GROQ_2", "groq/llama-3.3-70b-versatile")
    MODEL_GROQ_3: str = os.getenv("MODEL_GROQ_3", "groq/llama-3.3-70b-versatile")
    MODEL_CEREBRAS_FALLBACKS: tuple[str, ...] = _csv(
        "MODEL_CEREBRAS_FALLBACKS",
        "cerebras/qwen-3-235b-a22b-instruct-2507,cerebras/llama3.1-8b,cerebras/llama3.1-8b",
    )
    MODEL_SILICONFLOW: str = os.getenv("MODEL_SILICONFLOW", "openai/zai-org/GLM-5.1")
    MODEL_SILICONFLOW_2: str = os.getenv("MODEL_SILICONFLOW_2", "openai/MiniMaxAI/MiniMax-M2.5")
    MODEL_SILICONFLOW_3: str = os.getenv("MODEL_SILICONFLOW_3", "openai/deepseek-ai/DeepSeek-V3.2")
    # Vision models (for image analysis only)
    MODEL_SILICONFLOW_VISION: str = os.getenv("MODEL_SILICONFLOW_VISION", "Qwen/Qwen2.5-VL-72B-Instruct")
    MODEL_SILICONFLOW_VISION_2: str = os.getenv("MODEL_SILICONFLOW_VISION_2", "Qwen/Qwen2.5-VL-32B-Instruct")
    SILICONFLOW_API_BASE: str = os.getenv("SILICONFLOW_API_BASE", "https://api.siliconflow.com/v1")

    AUTO_START_ENABLED: bool = _bool("AUTO_START_ENABLED", True)
    REPLY_IN_GROUPS: bool = _bool("REPLY_IN_GROUPS", True)
    IGNORE_BOTS: bool = _bool("IGNORE_BOTS", True)
    HISTORY_MAX_MESSAGES: int = _int("HISTORY_MAX_MESSAGES", 12)
    HISTORY_MIN_MESSAGES: int = _int("HISTORY_MIN_MESSAGES", 8)
    COOLDOWN_SECONDS_AI: int = _int("COOLDOWN_SECONDS_AI", 2)
    COOLDOWN_SECONDS_STATIC: int = _int("COOLDOWN_SECONDS_STATIC", 300)
    COOLDOWN_SECONDS_STATIC_VIP: int = _int("COOLDOWN_SECONDS_STATIC_VIP", 60)

    DEBOUNCE_SECONDS_MIN: float = _float("DEBOUNCE_SECONDS_MIN", 2.5)
    DEBOUNCE_SECONDS_MAX: float = _float("DEBOUNCE_SECONDS_MAX", 5.0)

    BOT_BURST_PAUSE_MIN: float = _float("BOT_BURST_PAUSE_MIN", 0.8)
    BOT_BURST_PAUSE_MAX: float = _float("BOT_BURST_PAUSE_MAX", 1.5)

    READ_DELAY_MIN: float = _float("READ_DELAY_MIN", 1.2)
    READ_DELAY_MAX: float = _float("READ_DELAY_MAX", 3.8)
    TYPING_PER_CHAR_MIN: float = _float("TYPING_PER_CHAR_MIN", 0.08)
    TYPING_PER_CHAR_MAX: float = _float("TYPING_PER_CHAR_MAX", 0.13)
    EXTRA_PAUSE_PROBABILITY: float = _float("EXTRA_PAUSE_PROBABILITY", 0.22)
    EXTRA_PAUSE_MIN: float = _float("EXTRA_PAUSE_MIN", 0.4)
    EXTRA_PAUSE_MAX: float = _float("EXTRA_PAUSE_MAX", 1.6)
    SPLIT_MESSAGE_PROBABILITY: float = _float("SPLIT_MESSAGE_PROBABILITY", 0.28)

    TYPO_MESSAGE_PROBABILITY: float = _float("TYPO_MESSAGE_PROBABILITY", 0.12)
    REACTION_PROBABILITY: float = _float("REACTION_PROBABILITY", 0.18)

    GHOST_MODE_DEFAULT: bool = _bool("GHOST_MODE_DEFAULT", False)
    ENABLE_OWNER_ASK: bool = _bool("ENABLE_OWNER_ASK", True)

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

    LLM_TIMEOUT_SECONDS: float = _float("LLM_TIMEOUT_SECONDS", 18.0)
    LLM_RETRYABLE_ERROR_COOLDOWN_SECONDS: float = _float("LLM_RETRYABLE_ERROR_COOLDOWN_SECONDS", 45.0)
    LLM_RATE_LIMIT_COOLDOWN_SECONDS: float = _float("LLM_RATE_LIMIT_COOLDOWN_SECONDS", 300.0)
    LLM_HARD_ERROR_COOLDOWN_SECONDS: float = _float("LLM_HARD_ERROR_COOLDOWN_SECONDS", 1800.0)

    API_RESTART_BASE_DELAY: float = _float("API_RESTART_BASE_DELAY", 5.0)
    API_RESTART_MAX_DELAY: float = _float("API_RESTART_MAX_DELAY", 120.0)

    @classmethod
    def validate_telegram(cls) -> list[str]:
        errors: list[str] = []
        if not cls.API_ID:
            errors.append("API_ID is missing or invalid")
        if not cls.API_HASH:
            errors.append("API_HASH is missing")
        if not cls.SESSION_STRING and not cls.SESSION_NAME:
            errors.append("SESSION_NAME or SESSION_STRING is required")
        return errors

    @classmethod
    def validate_llm(cls) -> list[str]:
        errors: list[str] = []
        if not any(
            [
                cls.GEMINI_API_KEY,
                cls.GEMINI_API_KEY_2,
                cls.GEMINI_API_KEY_3,
                cls.GROQ_API_KEY,
                cls.GROQ_API_KEY_2,
                cls.GROQ_API_KEY_3,
                cls.CEREBRAS_API_KEY,
                cls.SILICONFLOW_API_KEY,
            ]
        ):
            errors.append("At least one LLM API key should be set for fallback chain")
        return errors
