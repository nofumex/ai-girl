"""
LiteLLM-based completion with strict provider fallback order:
gemini -> gemini2 -> gemini3 -> groq -> groq2 -> groq3 -> cerebras -> siliconflow
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import time
from typing import Any, Sequence, Union

MessageContent = str | list[dict[str, Any]]

import litellm

from config import Config

logger = logging.getLogger(__name__)

litellm.suppress_debug_info = True

_logged_provider_chain = False
_provider_skip_until: dict[str, float] = {}
_provider_inflight: set[str] = set()
_provider_state_lock = asyncio.Lock()


def _strip_provider_prefix(model: str, prefix: str) -> str:
    wanted = f"{prefix}/"
    return model[len(wanted):] if model.startswith(wanted) else model


def _base_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if Config.LLM_PROXY:
        kwargs["proxy"] = Config.LLM_PROXY
        os.environ["HTTPS_PROXY"] = Config.LLM_PROXY
        os.environ["HTTP_PROXY"] = Config.LLM_PROXY
    return kwargs


_llm_kwargs = {}


def _provider_specs() -> list[tuple[str, str, dict[str, Any]]]:
    global _llm_kwargs
    _llm_kwargs = _base_kwargs()
    specs: list[tuple[str, str, dict[str, Any]]] = []

    if Config.GEMINI_API_KEY:
        specs.append(("gemini", Config.MODEL_GEMINI, {"api_key": Config.GEMINI_API_KEY}))
    if Config.GEMINI_API_KEY_2:
        specs.append(("gemini2", Config.MODEL_GEMINI_2, {"api_key": Config.GEMINI_API_KEY_2}))
    if Config.GEMINI_API_KEY_3:
        specs.append(("gemini3", Config.MODEL_GEMINI_3, {"api_key": Config.GEMINI_API_KEY_3}))
    if Config.GROQ_API_KEY:
        specs.append(("groq", Config.MODEL_GROQ, {"api_key": Config.GROQ_API_KEY}))
    if Config.GROQ_API_KEY_2:
        specs.append(("groq2", Config.MODEL_GROQ_2, {"api_key": Config.GROQ_API_KEY_2}))
    if Config.GROQ_API_KEY_3:
        specs.append(("groq3", Config.MODEL_GROQ_3, {"api_key": Config.GROQ_API_KEY_3}))
    if Config.CEREBRAS_API_KEY:
        for idx, model in enumerate(Config.MODEL_CEREBRAS_FALLBACKS, start=1):
            specs.append((f"cerebras{idx}", model, {"api_key": Config.CEREBRAS_API_KEY}))
    if Config.SILICONFLOW_API_KEY:
        specs.append(
            (
                "siliconflow",
                _strip_provider_prefix(Config.MODEL_SILICONFLOW, "siliconflow"),
                {
                    "api_key": Config.SILICONFLOW_API_KEY,
                    "api_base": Config.SILICONFLOW_API_BASE,
                    "custom_llm_provider": "openai",
                },
            )
        )
        if Config.MODEL_SILICONFLOW_2:
            specs.append(
                (
                    "siliconflow2",
                    Config.MODEL_SILICONFLOW_2,
                    {
                        "api_key": Config.SILICONFLOW_API_KEY,
                        "api_base": Config.SILICONFLOW_API_BASE,
                        "custom_llm_provider": "openai",
                    },
                )
            )
        if Config.MODEL_SILICONFLOW_3:
            specs.append(
                (
                    "siliconflow3",
                    Config.MODEL_SILICONFLOW_3,
                    {
                        "api_key": Config.SILICONFLOW_API_KEY,
                        "api_base": Config.SILICONFLOW_API_BASE,
                        "custom_llm_provider": "openai",
                    },
                )
            )
        # Vision models (separate from text models)
        if Config.MODEL_SILICONFLOW_VISION:
            specs.append(
                (
                    "siliconflow_vision",
                    Config.MODEL_SILICONFLOW_VISION,
                    {
                        "api_key": Config.SILICONFLOW_API_KEY,
                        "api_base": Config.SILICONFLOW_API_BASE,
                        "custom_llm_provider": "openai",
                    },
                )
            )
        if Config.MODEL_SILICONFLOW_VISION_2:
            specs.append(
                (
                    "siliconflow_vision2",
                    Config.MODEL_SILICONFLOW_VISION_2,
                    {
                        "api_key": Config.SILICONFLOW_API_KEY,
                        "api_base": Config.SILICONFLOW_API_BASE,
                        "custom_llm_provider": "openai",
                    },
                )
            )
    return specs


def _extract_content(response: Any) -> str | None:
    choice = response.choices[0]
    msg = choice.message
    if isinstance(msg, dict):
        content = msg.get("content")
    else:
        content = getattr(msg, "content", None)
    if content and str(content).strip():
        return str(content).strip()
    return None


def _retry_after_seconds(text: str) -> float | None:
    retry_match = re.search(r"retry in\s+(\d+(?:\.\d+)?)s", text, flags=re.IGNORECASE)
    if retry_match:
        return float(retry_match.group(1))

    ms_match = re.search(r"retry in\s+(\d+(?:\.\d+)?)ms", text, flags=re.IGNORECASE)
    if ms_match:
        return float(ms_match.group(1)) / 1000.0

    hms_match = re.search(
        r"try again in\s+(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?",
        text,
        flags=re.IGNORECASE,
    )
    if not hms_match:
        return None

    hours = float(hms_match.group(1) or 0)
    minutes = float(hms_match.group(2) or 0)
    seconds = float(hms_match.group(3) or 0)
    total = hours * 3600 + minutes * 60 + seconds
    return total if total > 0 else None


def _cooldown_for_error(label: str, model: str, exc: Exception) -> float:
    text = f"{type(exc).__name__}: {exc}".lower()
    parsed_retry = _retry_after_seconds(text)

    daily_quota_hit = any(token in text for token in ["requestsperday", "tokens per day", "limit: 0"])
    hard_auth_or_model_error = any(
        token in text
        for token in ["does not exist", "notfound", "not found", "do not have access", "unauthorized", "forbidden", "invalid api key"]
    )

    if daily_quota_hit:
        if parsed_retry is not None:
            return max(parsed_retry + 5.0, Config.LLM_HARD_ERROR_COOLDOWN_SECONDS)
        return Config.LLM_HARD_ERROR_COOLDOWN_SECONDS

    if parsed_retry is not None:
        return max(parsed_retry + 5.0, Config.LLM_RETRYABLE_ERROR_COOLDOWN_SECONDS)

    if any(token in text for token in ["rate limit", "quota", "resource_exhausted", "too many requests"]):
        return Config.LLM_RATE_LIMIT_COOLDOWN_SECONDS

    if hard_auth_or_model_error:
        return Config.LLM_HARD_ERROR_COOLDOWN_SECONDS

    if any(token in text for token in ["timeout", "timed out", "connection", "temporarily", "service unavailable", "overloaded"]):
        return Config.LLM_RETRYABLE_ERROR_COOLDOWN_SECONDS

    return Config.LLM_RETRYABLE_ERROR_COOLDOWN_SECONDS


async def _provider_available(label: str) -> bool:
    async with _provider_state_lock:
        until = _provider_skip_until.get(label, 0.0)
        if until > time.time():
            return False
        return label not in _provider_inflight


async def _mark_provider_inflight(label: str) -> bool:
    async with _provider_state_lock:
        until = _provider_skip_until.get(label, 0.0)
        if until > time.time() or label in _provider_inflight:
            return False
        _provider_inflight.add(label)
        return True


async def _clear_provider_inflight(label: str) -> None:
    async with _provider_state_lock:
        _provider_inflight.discard(label)


async def _mark_provider_cooldown(label: str, seconds: float) -> None:
    async with _provider_state_lock:
        _provider_skip_until[label] = time.time() + max(1.0, seconds)
        _provider_inflight.discard(label)


async def _mark_provider_ok(label: str) -> None:
    async with _provider_state_lock:
        _provider_skip_until.pop(label, None)
        _provider_inflight.discard(label)


async def complete_chat(
    messages: Sequence[dict[str, MessageContent]],
    *,
    temperature: float = 0.85,
    max_tokens: int = 800,
    log_success: bool = True,
    cheap_only: bool = False,
    image_data: bytes | None = None,
) -> str | None:
    global _logged_provider_chain

    raw_specs = _provider_specs()
    if not raw_specs:
        logger.error("No LLM API keys configured; cannot complete.")
        return None

    if not _logged_provider_chain:
        _logged_provider_chain = True
        logger.info(
            "LLM provider chain: %s",
            " -> ".join(f"{label}({model})" for label, model, _ in raw_specs),
        )

    available_specs: list[tuple[str, str, dict[str, Any]]] = []
    import time as time_module
    current_time = time_module.time()
    
    # cheap_only=True = internal requests (context analysis) = START from weakest (cerebras2/3)
    # cheap_only=False = final response = START from smartest (gemini/groq/cerebras1), NO cerebras2/3
    if cheap_only:
        # Internal: use weakest first -> strongest as fallback
        priority_order = ["cerebras2", "cerebras3", "siliconflow", "siliconflow2", "siliconflow3", "groq", "groq2", "groq3", "gemini", "gemini2", "gemini3", "cerebras1"]
    else:
        # Final: gemini -> siliconflow -> groq -> cerebras1 (NO cerebras2/3)
        priority_order = ["gemini", "gemini2", "gemini3", "siliconflow", "siliconflow2", "siliconflow3", "groq", "groq2", "groq3", "cerebras1"]
    
    # When NO image, filter out vision providers entirely
    vision_labels = {"siliconflow_vision", "siliconflow_vision2"}
    if not image_data:
        raw_specs = [(l, m, k) for l, m, k in raw_specs if l not in vision_labels]
    else:
        # Add vision providers to priority order when image is present (place them first)
        priority_order = ["siliconflow_vision", "siliconflow_vision2"] + priority_order
    
    # Filter out cerebras2/3 for final response (keep vision models)
    if not cheap_only:
        base_priority = ["gemini", "gemini2", "gemini3", "siliconflow", "siliconflow2", "siliconflow3", "groq", "groq2", "groq3", "cerebras1"]
        allowed = set(base_priority) | vision_labels if image_data else set(base_priority)
        raw_specs = [(l, m, k) for l, m, k in raw_specs if l in allowed]
    
    for label, model, kwargs in raw_specs:
        until = _provider_skip_until.get(label, 0.0)
        in_flight = label in _provider_inflight
        
        if until > current_time:
            logger.info("Provider %s is on cooldown until %.1fs (%.1fs remaining)", label, until, until - current_time)
            continue
        if in_flight:
            logger.info("Provider %s is in-flight, skipping", label)
            continue
             
        available_specs.append((label, model, kwargs))
        logger.info("Provider %s is available", label)

    # Reorder available_specs according to priority
    def sort_key(item):
        label, _, _ = item
        try:
            return priority_order.index(label)
        except ValueError:
            return 999
    
    available_specs.sort(key=sort_key)
    
    specs = available_specs or raw_specs
    if not available_specs:
        logger.warning("No providers available, using raw_specs (will retry all)")
    
# Providers that support vision in OpenAI-compatible format
    VISION_SUPPORTED_PROVIDERS = {"gemini", "gemini2", "gemini3", "siliconflow_vision", "siliconflow_vision2"}
    
    last_error: str | None = None
    vision_tried_providers = set()

    for label, model, kwargs in specs:
        claimed = await _mark_provider_inflight(label)
        if not claimed:
            continue
        logger.info(">>> TRYING provider %s with model %s", label, model)
        try:
            logger.info("About to call litellm.acompletion for %s...", label)
            
            # Build messages with image if provided
            call_messages: list[dict[str, MessageContent]] = list(messages)
            use_vision = bool(image_data and label in VISION_SUPPORTED_PROVIDERS)
            
            if use_vision and image_data:
                vision_tried_providers.add(label)
                image_b64 = base64.b64encode(image_data).decode("utf-8")
                image_url = f"data:image/jpeg;base64,{image_b64}"
                # Add image to the last user message with detail: high for better vision
                if call_messages and call_messages[-1]["role"] == "user":
                    # type: ignore[assignment]
                    call_messages[-1]["content"] = [
                        {"type": "text", "text": str(call_messages[-1]["content"])},
                        {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}}
                    ]
            
            response = await litellm.acompletion(
                model=model,
                messages=call_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=Config.LLM_TIMEOUT_SECONDS,
                **{**kwargs, **_llm_kwargs},
            )
            logger.info("Response from %s: type=%s, raw=%s", label, type(response), str(response)[:200])
            content = _extract_content(response)
            logger.info("Extracted content from %s: %s", label, content[:100] if content else None)
            await _mark_provider_ok(label)
            if content:
                if log_success:
                    logger.info("LLM ok via %s (%s)", label, model)
                return content
            else:
                logger.info("Provider %s returned empty content, trying next...", label)
        except Exception as exc:
            exc_text = str(exc).lower()
            
            # Check if it's a vision-related error and we haven't tried text-only yet for this provider
            is_vision_error = any(x in exc_text for x in ["image_url", "content.str", "content.list", "invalid json payload", "value is not one of the allowed"])
            
            if image_data and is_vision_error and label not in vision_tried_providers:
                # Retry without vision format for this provider
                logger.info("Vision format not supported by %s, retrying as text-only", label)
                try:
                    call_messages_text = list(messages)
                    response = await litellm.acompletion(
                        model=model,
                        messages=call_messages_text,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        timeout=Config.LLM_TIMEOUT_SECONDS,
                        **{**kwargs, **_llm_kwargs},
                    )
                    content = _extract_content(response)
                    await _mark_provider_ok(label)
                    if content:
                        if log_success:
                            logger.info("LLM ok via %s (%s) [text-only mode]", label, model)
                        return content
                except Exception as text_exc:
                    exc = text_exc
            
            cooldown = _cooldown_for_error(label, model, exc)
            await _mark_provider_cooldown(label, cooldown)
            last_error = f"{label}/{model}: {exc}"
            logger.warning("LLM fallback: %s | cooling down %ss", last_error, int(cooldown))
        finally:
            await _clear_provider_inflight(label)
    
    # If all vision providers failed but text-only would work, try again without vision
    if image_data and vision_tried_providers and last_error is not None:
        logger.info("All vision providers failed, retrying text-only with all providers")
        # Remove image and retry with lower priority
        for label, model, kwargs in specs:
            if label in vision_tried_providers:
                continue
            claimed = await _mark_provider_inflight(label)
            if not claimed:
                continue
            try:
                response = await litellm.acompletion(
                    model=model,
                    messages=list(messages),
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=Config.LLM_TIMEOUT_SECONDS,
                    **{**kwargs, **_llm_kwargs},
                )
                content = _extract_content(response)
                await _mark_provider_ok(label)
                if content:
                    logger.info("LLM ok via %s (%s) [text-only fallback]", label, model)
                    return content
            except Exception as text_exc:
                cooldown = _cooldown_for_error(label, model, text_exc)
                await _mark_provider_cooldown(label, cooldown)
                last_error = f"{label}/{model}: {text_exc}"
                logger.warning("Text-only fallback failed: %s", last_error)
            finally:
                await _clear_provider_inflight(label)

    logger.error("All LLM providers failed. Last: %s", last_error)
    return None
