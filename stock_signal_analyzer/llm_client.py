"""
Унифицированный LLM-клиент: Ollama Cloud API или локальный Ollama API.

Переменные окружения:
  LLM_PROVIDER         — "ollama_cloud" | "ollama" (по умолчанию "ollama")
  OLLAMA_HOST          — URL локального Ollama (по умолчанию http://localhost:11434)
  OLLAMA_MODEL         — локальная модель (по умолчанию qwen2.5:1.5b)
  OLLAMA_CLOUD_API_KEY — ключ Ollama Cloud
  OLLAMA_CLOUD_MODEL   — модель Ollama Cloud (по умолчанию qwen2.5:1.5b)

Ollama Cloud — официальный cloud API от Ollama (OpenAI-compatible).
Локальный Ollama — работает только если пользователь сам поднял сервис.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

_log = logging.getLogger(__name__)

# ── Конфигурация ─────────────────────────────────────────────────────────────

# Предпочитаем config.py, но fallback на env для автономного импорта
try:
    from stock_signal_analyzer.config import get_settings
    _settings = get_settings()
    _PROVIDER = _settings.llm_provider
    _OLLAMA_HOST = _settings.ollama_host
    _OLLAMA_MODEL = _settings.ollama_model
    _OLLAMA_CLOUD_API_KEY = (_settings.ollama_cloud_api_key or "").strip()
    _OLLAMA_CLOUD_MODEL = _settings.ollama_cloud_model
    _OPENROUTER_API_KEY = (_settings.openrouter_api_key or "").strip()
    _OPENROUTER_MODEL = _settings.openrouter_model
except Exception:
    _PROVIDER = os.environ.get("LLM_PROVIDER", "ollama").strip().lower()
    _OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    _OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b")
    _OLLAMA_CLOUD_API_KEY = os.environ.get("OLLAMA_CLOUD_API_KEY", "").strip()
    _OLLAMA_CLOUD_MODEL = os.environ.get("OLLAMA_CLOUD_MODEL", "qwen2.5:1.5b")
    _OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
    _OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-chat:free")

_OLLAMA_CLOUD_URL = "https://ollama.com/v1/chat/completions"
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_REQUEST_TIMEOUT = 60.0


def llm_available() -> bool:
    """Проверить доступность LLM (cloud API или локальный)."""
    if _PROVIDER == "ollama_cloud" and _OLLAMA_CLOUD_API_KEY:
        return True
    if _PROVIDER == "openrouter" and _OPENROUTER_API_KEY:
        return True
    # Проверяем локальный Ollama (если пользователь поднял сам)
    try:
        r = requests.get(f"{_OLLAMA_HOST}/api/tags", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


def _call_ollama_cloud(messages: list[dict[str, str]], temperature: float = 0.1, max_tokens: int = 512) -> str | None:
    """Вызвать Ollama Cloud API (OpenAI-compatible)."""
    try:
        payload = {
            "model": _OLLAMA_CLOUD_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        r = requests.post(
            _OLLAMA_CLOUD_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {_OLLAMA_CLOUD_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=_REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            _log.warning("Ollama Cloud returned %d: %s", r.status_code, r.text[:200])
            return None
        data = r.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content or None
    except Exception as e:
        _log.warning("Ollama Cloud request failed: %s", e)
        return None


def _call_openrouter(messages: list[dict[str, str]], temperature: float = 0.1, max_tokens: int = 512) -> str | None:
    """Вызвать OpenRouter API (OpenAI-compatible, агрегатор моделей)."""
    try:
        payload = {
            "model": _OPENROUTER_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        r = requests.post(
            _OPENROUTER_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {_OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/stock-signal-analyzer",
                "X-Title": "Stock Signal Analyzer",
            },
            timeout=_REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            _log.warning("OpenRouter returned %d: %s", r.status_code, r.text[:200])
            return None
        data = r.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content or None
    except Exception as e:
        _log.warning("OpenRouter request failed: %s", e)
        return None


def _call_ollama_local(messages: list[dict[str, str]], temperature: float = 0.1, num_predict: int = 512) -> str | None:
    """Вызвать локальный Ollama API."""
    try:
        payload = {
            "model": _OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
            },
        }
        r = requests.post(
            f"{_OLLAMA_HOST}/api/chat",
            json=payload,
            timeout=_REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            _log.warning("Ollama local returned %d", r.status_code)
            return None
        data = r.json()
        return data.get("message", {}).get("content", "")
    except Exception as e:
        _log.warning("Ollama local request failed: %s", e)
        return None


def llm_chat(messages: list[dict[str, str]], *, temperature: float = 0.1, max_tokens: int = 512, json_mode: bool = False) -> str | None:
    """
    Унифицированный вызов LLM. Автоматически выбирает провайдер.

    Args:
        messages: список {"role": "system"/"user", "content": "..."}
        temperature: температура сэмплирования
        max_tokens: максимум токенов в ответе
        json_mode: если True, добавляем инструкцию для JSON-ответа

    Returns:
        Текст ответа или None при ошибке.
    """
    if json_mode:
        system_msgs = [m for m in messages if m.get("role") == "system"]
        if system_msgs:
            system_msgs[0]["content"] += "\nReturn ONLY valid JSON, no markdown, no explanations outside JSON."
        else:
            messages = [{"role": "system", "content": "Return ONLY valid JSON, no markdown, no explanations outside JSON."}] + messages

    if _PROVIDER == "ollama_cloud" and _OLLAMA_CLOUD_API_KEY:
        return _call_ollama_cloud(messages, temperature, max_tokens)
    if _PROVIDER == "openrouter" and _OPENROUTER_API_KEY:
        return _call_openrouter(messages, temperature, max_tokens)
    return _call_ollama_local(messages, temperature, max_tokens)


def llm_chat_json(messages: list[dict[str, str]], *, temperature: float = 0.1, max_tokens: int = 512) -> dict[str, Any] | None:
    """Вызвать LLM и распарсить JSON-ответ."""
    content = llm_chat(messages, temperature=temperature, max_tokens=max_tokens, json_mode=True)
    if not content:
        return None
    try:
        cleaned = content.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        return json.loads(cleaned.strip())
    except json.JSONDecodeError as e:
        _log.warning("Failed to parse LLM JSON response: %s | content: %s", e, content[:200])
        return None


def current_provider() -> str:
    """Вернуть текущего провайдера для логирования."""
    if _PROVIDER == "ollama_cloud" and _OLLAMA_CLOUD_API_KEY:
        return f"ollama_cloud/{_OLLAMA_CLOUD_MODEL}"
    if _PROVIDER == "openrouter" and _OPENROUTER_API_KEY:
        return f"openrouter/{_OPENROUTER_MODEL}"
    return f"ollama/{_OLLAMA_MODEL}"
