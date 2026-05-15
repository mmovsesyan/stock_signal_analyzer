"""
LLM-анализ новостей через Ollama (локальная или cloud модель).

Использует qwen2.5:1.5b (~1.5 GB RAM локально) или cloud API (OpenRouter/Ollama Cloud).

Переменные окружения:
  LLM_PROVIDER       — "ollama" | "openrouter" | "ollama_cloud" (по умолчанию "ollama")
  OLLAMA_HOST        — URL Ollama (по умолчанию http://localhost:11434)
  OLLAMA_MODEL       — модель (по умолчанию qwen2.5:1.5b)
  OPENROUTER_API_KEY — ключ OpenRouter (для cloud)
  OPENROUTER_MODEL   — модель OpenRouter (по умолчанию qwen/qwen-2.5-1.5b-instruct)
  OLLAMA_CLOUD_API_KEY — ключ Ollama Cloud
  LLM_SENTIMENT      — включить LLM sentiment (1 = да, 0 = нет)
  LLM_CACHE_TTL      — TTL кэша в секундах (по умолчанию 3600)

Архитектура:
  - Батчевый анализ: до 10 заголовков за один запрос к LLM
  - Кэширование результатов (in-memory, TTL 1 час)
  - Graceful fallback на VADER если LLM недоступен
  - Structured output: JSON с полями sentiment, score, catalysts
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from .llm_client import llm_available, llm_chat_json, current_provider
from .news_feeds import NewsItem

_log = logging.getLogger(__name__)

# ── Конфигурация ─────────────────────────────────────────────────────────────

_LLM_ENABLED = os.environ.get("LLM_SENTIMENT", "1").strip()
_CACHE_TTL = int(os.environ.get("LLM_CACHE_TTL", "3600"))
_MAX_HEADLINES_PER_BATCH = 10

# ── Кэш ─────────────────────────────────────────────────────────────────────

_cache: dict[str, tuple[Any, float]] = {}


def _cache_key(headlines: list[str]) -> str:
    """Стабильный ключ для набора заголовков."""
    combined = "|".join(sorted(h.strip().lower()[:200] for h in headlines))
    return hashlib.md5(combined.encode()).hexdigest()


def _cache_get(key: str) -> Any | None:
    if key in _cache:
        val, ts = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            return val
        del _cache[key]
    return None


def _cache_set(key: str, value: Any) -> None:
    if len(_cache) > 500:
        oldest = sorted(_cache.items(), key=lambda x: x[1][1])[:100]
        for k, _ in oldest:
            del _cache[k]
    _cache[key] = (value, time.time())


# ── Проверка доступности ─────────────────────────────────────────────────────


def ollama_available() -> bool:
    """Проверить, доступен ли LLM (локальный или cloud)."""
    if _LLM_ENABLED == "0":
        return False
    return llm_available()


def ollama_model_ready() -> bool:
    """Для cloud-провайдеров модель всегда готова. Для локального — проверяем."""
    return llm_available()


def ensure_model_pulled() -> bool:
    """Для cloud не нужно pull. Для локального — проверяем доступность."""
    return llm_available()


# ── Промпт ───────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a financial news analyst. Analyze the given headlines and return a JSON object.

Rules:
- Score each headline from -1.0 (very bearish) to +1.0 (very bullish)
- Identify key catalysts (earnings beat, rate cut, downgrade, etc.)
- Be concise in explanations
- Return ONLY valid JSON, no markdown

Output format:
{
  "overall_sentiment": "bullish" | "bearish" | "neutral",
  "overall_score": float (-1.0 to 1.0),
  "catalysts": ["catalyst1", "catalyst2"],
  "headlines": [
    {"score": float, "catalyst": "string or null"}
  ]
}"""


def _build_prompt(headlines: list[str]) -> str:
    """Построить промпт для батча заголовков."""
    numbered = "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines))
    return f"Analyze these financial news headlines:\n\n{numbered}"


# ── Вызов LLM ─────────────────────────────────────────────────────────────


@dataclass
class LLMSentimentResult:
    """Результат LLM-анализа."""
    overall_score: float
    overall_sentiment: str
    catalysts: list[str]
    per_headline_scores: list[float]
    source: str
    detail: str


def _call_llm(headlines: list[str]) -> dict[str, Any] | None:
    """Вызвать LLM API и получить structured JSON."""
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_prompt(headlines)},
    ]
    return llm_chat_json(messages, temperature=0.1, max_tokens=512)


def analyze_headlines_llm(items: list[NewsItem]) -> LLMSentimentResult | None:
    """
    Анализ заголовков через LLM.
    Возвращает None если LLM недоступен (caller должен fallback на VADER).
    """
    if not items:
        return None
    if not ollama_available():
        return None

    headlines = [it.title.strip()[:200] for it in items[:_MAX_HEADLINES_PER_BATCH] if it.title.strip()]
    if not headlines:
        return None

    key = _cache_key(headlines)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    result = _call_llm(headlines)
    if result is None:
        return None

    try:
        overall_score = float(result.get("overall_score", 0.0))
        overall_score = max(-1.0, min(1.0, overall_score))
        overall_sentiment = str(result.get("overall_sentiment", "neutral"))
        catalysts = result.get("catalysts", [])
        if not isinstance(catalysts, list):
            catalysts = []
        catalysts = [str(c) for c in catalysts[:5]]

        per_headline = result.get("headlines", [])
        per_headline_scores = []
        for h in per_headline:
            if isinstance(h, dict):
                s = float(h.get("score", 0.0))
                per_headline_scores.append(max(-1.0, min(1.0, s)))
            else:
                per_headline_scores.append(0.0)

        cat_str = ", ".join(catalysts) if catalysts else "нет"
        provider = current_provider()
        detail = f"LLM ({provider}): {overall_sentiment} ({overall_score:+.2f}), катализаторы: {cat_str}"

        llm_result = LLMSentimentResult(
            overall_score=overall_score,
            overall_sentiment=overall_sentiment,
            catalysts=catalysts,
            per_headline_scores=per_headline_scores,
            source="llm",
            detail=detail,
        )
        _cache_set(key, llm_result)
        return llm_result
    except (TypeError, ValueError, KeyError) as e:
        _log.warning("Failed to parse LLM result: %s", e)
        return None


def blend_sentiment_scores(vader_score: float, llm_result: LLMSentimentResult | None) -> tuple[float, str]:
    """
    Смешать VADER и LLM scores.
    LLM получает больший вес (0.65) т.к. лучше понимает финансовый контекст.
    """
    if llm_result is None:
        return vader_score, ""

    llm_weight = 0.65
    vader_weight = 0.35
    blended = vader_weight * vader_score + llm_weight * llm_result.overall_score
    blended = max(-1.0, min(1.0, blended))

    note = f"LLM blend: VADER={vader_score:+.3f}×{vader_weight} + LLM={llm_result.overall_score:+.3f}×{llm_weight} = {blended:+.3f}"
    if llm_result.catalysts:
        note += f" | катализаторы: {', '.join(llm_result.catalysts[:3])}"
    return blended, note
