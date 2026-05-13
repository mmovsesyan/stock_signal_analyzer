"""
LLM-анализ новостей через Ollama (локальная модель).

Использует qwen2.5:1.5b (~1.5 GB RAM) — компактная модель, достаточная для
классификации sentiment и извлечения ключевых событий из финансовых новостей.

Переменные окружения:
  OLLAMA_HOST       — URL Ollama (по умолчанию http://localhost:11434)
  OLLAMA_MODEL      — модель (по умолчанию qwen2.5:1.5b)
  LLM_SENTIMENT     — включить LLM sentiment (1 = да, 0 = нет; по умолчанию 1 если Ollama доступен)
  LLM_CACHE_TTL     — TTL кэша в секундах (по умолчанию 3600)

Архитектура:
  - Батчевый анализ: до 10 заголовков за один запрос к LLM
  - Кэширование результатов (in-memory, TTL 1 час)
  - Graceful fallback на VADER если Ollama недоступен
  - Structured output: JSON с полями sentiment, score, catalysts
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from .news_feeds import NewsItem
from .retry_utils import retry_with_backoff

_log = logging.getLogger(__name__)

# ── Конфигурация ─────────────────────────────────────────────────────────────

_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b")
_LLM_ENABLED = os.environ.get("LLM_SENTIMENT", "1").strip()
_CACHE_TTL = int(os.environ.get("LLM_CACHE_TTL", "3600"))
_REQUEST_TIMEOUT = 30.0  # секунд на один запрос к Ollama
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
    # Ограничиваем размер кэша
    if len(_cache) > 500:
        # Удаляем самые старые
        oldest = sorted(_cache.items(), key=lambda x: x[1][1])[:100]
        for k, _ in oldest:
            del _cache[k]
    _cache[key] = (value, time.time())


# ── Проверка доступности ─────────────────────────────────────────────────────


def ollama_available() -> bool:
    """Проверить, доступен ли Ollama и включён ли LLM sentiment."""
    if _LLM_ENABLED == "0":
        return False
    try:
        r = requests.get(f"{_OLLAMA_HOST}/api/tags", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


def ollama_model_ready() -> bool:
    """Проверить, загружена ли нужная модель."""
    try:
        r = requests.get(f"{_OLLAMA_HOST}/api/tags", timeout=5.0)
        if r.status_code != 200:
            return False
        data = r.json()
        models = data.get("models") or []
        model_base = _OLLAMA_MODEL.split(":")[0]
        for m in models:
            name = m.get("name", "")
            if model_base in name:
                return True
        return False
    except Exception:
        return False


def ensure_model_pulled() -> bool:
    """Подтянуть модель если её нет. Возвращает True если модель готова."""
    if ollama_model_ready():
        return True
    _log.info("Pulling Ollama model %s...", _OLLAMA_MODEL)
    try:
        r = requests.post(
            f"{_OLLAMA_HOST}/api/pull",
            json={"name": _OLLAMA_MODEL, "stream": False},
            timeout=300.0,  # pull может занять время
        )
        return r.status_code == 200
    except Exception as e:
        _log.warning("Failed to pull model %s: %s", _OLLAMA_MODEL, e)
        return False


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


# ── Вызов Ollama ─────────────────────────────────────────────────────────────


@dataclass
class LLMSentimentResult:
    """Результат LLM-анализа."""
    overall_score: float  # -1.0 to 1.0
    overall_sentiment: str  # bullish/bearish/neutral
    catalysts: list[str]
    per_headline_scores: list[float]
    source: str  # "llm" or "fallback"
    detail: str


@retry_with_backoff(max_retries=2, initial_delay=0.5, backoff_factor=2.0,
                    retry_on=(requests.RequestException,))
def _ollama_chat_raw(payload: dict) -> requests.Response:
    """HTTP POST к Ollama с retry (transient network errors)."""
    r = requests.post(
        f"{_OLLAMA_HOST}/api/chat",
        json=payload,
        timeout=_REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return r


def _call_ollama(headlines: list[str]) -> dict[str, Any] | None:
    """Вызвать Ollama API и получить structured JSON."""
    payload = {
        "model": _OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_prompt(headlines)},
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "num_predict": 512,
        },
    }
    try:
        r = _ollama_chat_raw(payload)
    except requests.RequestException as e:
        _log.warning("Ollama request failed after retries: %s", e)
        return None
    except Exception as e:
        _log.warning("Ollama unexpected error: %s", e)
        return None

    if r.status_code != 200:
        _log.warning("Ollama returned %d: %s", r.status_code, r.text[:200])
        return None
    try:
        data = r.json()
    except json.JSONDecodeError as e:
        _log.warning("Ollama returned invalid JSON: %s", e)
        return None

    content = data.get("message", {}).get("content", "")
    if not content:
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        _log.warning("Failed to parse Ollama response: %s", e)
        return None


def analyze_headlines_llm(items: list[NewsItem]) -> LLMSentimentResult | None:
    """
    Анализ заголовков через LLM.
    Возвращает None если Ollama недоступен (caller должен fallback на VADER).
    """
    if not items:
        return None
    if not ollama_available():
        return None

    headlines = [it.title.strip()[:200] for it in items[:_MAX_HEADLINES_PER_BATCH] if it.title.strip()]
    if not headlines:
        return None

    # Проверяем кэш
    key = _cache_key(headlines)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    # Убедимся что модель есть
    if not ollama_model_ready():
        if not ensure_model_pulled():
            return None

    result = _call_ollama(headlines)
    if result is None:
        return None

    # Парсим результат
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
        detail = f"LLM ({_OLLAMA_MODEL}): {overall_sentiment} ({overall_score:+.2f}), катализаторы: {cat_str}"

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
    Возвращает (blended_score, detail_note).
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
