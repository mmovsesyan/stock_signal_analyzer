"""Централизованная конфигурация проекта.

Все переменные окружения загружаются один раз при старте.
Использование:
    from stock_signal_analyzer.config import get_settings
    settings = get_settings()
    print(settings.database_url)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Все настройки проекта."""

    # Database
    database_url: str = "sqlite:///data/stock_signals.db"

    # API Keys
    finnhub_api_key: str | None = None
    tinkoff_invest_token: str | None = None
    tinkoff_token: str | None = None
    polygon_api_key: str | None = None

    # Ollama / LLM
    llm_provider: str = "ollama"
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:1.5b"
    ollama_cloud_api_key: str | None = None
    ollama_cloud_model: str = "qwen2.5:1.5b"
    llm_sentiment_enabled: bool = True
    llm_cache_ttl: int = 3600

    # Scheduler
    scheduler_mode: str = "apscheduler"  # celery | apscheduler | disabled
    outcome_interval_sec: int = 3600
    learn_interval_sec: int = 21600
    collect_interval_sec: int = 14400
    cleanup_interval_sec: int = 86400
    health_check_interval_sec: int = 300

    # Paths
    stock_signal_data: str = "/var/lib/stock_signal_analyzer"
    ssa_signal_log: str | None = None
    ssa_drawdown_state: str | None = None

    # Subscriptions
    subscription_enabled: bool = False

    # Admin contact for new users
    admin_contact_info: str = ""

    # News
    default_news_feeds: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # Ensure data directory exists
        Path(self.stock_signal_data).mkdir(parents=True, exist_ok=True)

    @property
    def signal_log_path(self) -> str | None:
        return self.ssa_signal_log or os.path.join(self.stock_signal_data, "signals.jsonl")

    @property
    def outcomes_path(self) -> str:
        return os.path.join(self.stock_signal_data, "outcomes.jsonl")

    @property
    def drawdown_state_path(self) -> str | None:
        return self.ssa_drawdown_state or os.path.join(self.stock_signal_data, "drawdown.json")

    @property
    def telegram_users_path(self) -> str:
        return os.path.join(self.stock_signal_data, "telegram_users.json")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Загрузить настройки из переменных окружения (кэшируются)."""
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "sqlite:///data/stock_signals.db"),
        finnhub_api_key=os.environ.get("FINNHUB_API_KEY") or os.environ.get("FINNHUB_TOKEN"),
        tinkoff_invest_token=os.environ.get("TINKOFF_INVEST_TOKEN"),
        tinkoff_token=os.environ.get("TINKOFF_TOKEN"),
        polygon_api_key=os.environ.get("POLYGON_API_KEY"),
        llm_provider=os.environ.get("LLM_PROVIDER", "ollama").strip().lower(),
        ollama_host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        ollama_model=os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b"),
        ollama_cloud_api_key=os.environ.get("OLLAMA_CLOUD_API_KEY") or None,
        ollama_cloud_model=os.environ.get("OLLAMA_CLOUD_MODEL", "qwen2.5:1.5b"),
        llm_sentiment_enabled=os.environ.get("LLM_SENTIMENT", "1").strip() != "0",
        llm_cache_ttl=int(os.environ.get("LLM_CACHE_TTL", "3600")),
        scheduler_mode=os.environ.get("SCHEDULER_MODE", "apscheduler"),
        outcome_interval_sec=int(os.environ.get("OUTCOME_INTERVAL_SEC", "3600")),
        learn_interval_sec=int(os.environ.get("LEARN_INTERVAL_SEC", "21600")),
        collect_interval_sec=int(os.environ.get("COLLECT_INTERVAL_SEC", "14400")),
        cleanup_interval_sec=int(os.environ.get("CLEANUP_INTERVAL_SEC", "86400")),
        health_check_interval_sec=int(os.environ.get("HEALTH_CHECK_INTERVAL_SEC", "300")),
        stock_signal_data=os.environ.get("STOCK_SIGNAL_DATA", "/var/lib/stock_signal_analyzer"),
        ssa_signal_log=os.environ.get("SSA_SIGNAL_LOG"),
        ssa_drawdown_state=os.environ.get("SSA_DRAWDOWN_STATE"),
        subscription_enabled=os.environ.get("SUBSCRIPTION_ENABLED", "0").strip() == "1",
        admin_contact_info=os.environ.get("ADMIN_CONTACT_INFO", ""),
        default_news_feeds=None,
    )


# Legacy aliases for backward compatibility
DEFAULT_NEWS_FEEDS = [
    "https://finance.yahoo.com/news/rssindex",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^TNX&region=US&lang=en-US",
]

GOOGLE_NEWS_RSS_TEMPLATE = (
    "https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en"
)
