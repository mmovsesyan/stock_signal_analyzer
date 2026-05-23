"""
Database layer — PostgreSQL через SQLAlchemy.

Заменяет JSON-файлы (telegram_users.json, signals.jsonl, outcomes.jsonl)
на нормальную БД для multi-tenant использования.

Переменные окружения:
  DATABASE_URL — строка подключения (по умолчанию sqlite для dev)
    Примеры:
      postgresql://user:pass@localhost:5432/stock_signals
      sqlite:///data/stock_signals.db

Использование:
  from stock_signal_analyzer.db import get_session, User, Signal, Outcome

  with get_session() as session:
      user = session.query(User).filter_by(telegram_id=123).first()
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    create_engine,
    Index,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Session,
    relationship,
    sessionmaker,
)

_DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///data/stock_signals.db")

_engine_kwargs = {
    "pool_pre_ping": True,
    "echo": False,
}
if not _DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["pool_size"] = 5
    _engine_kwargs["max_overflow"] = 10

_engine = create_engine(_DATABASE_URL, **_engine_kwargs)

_SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# ── Модели ───────────────────────────────────────────────────────────────────


class User(Base):
    """Пользователь Telegram."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(Integer, unique=True, nullable=False, index=True)
    username = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    is_active = Column(Boolean, default=False)

    # Subscription
    tier = Column(String(20), default="free")  # free, pro, premium
    tier_expires_at = Column(DateTime, nullable=True)

    # Preferences
    notify_strong_outside = Column(Boolean, default=True)
    strong_threshold = Column(Float, default=0.35)
    notify_cooldown_sec = Column(Integer, default=86400)
    use_default_tickers = Column(Boolean, default=True)

    # Extended preferences (migrated from telegram_users.json)
    signal_filter_type = Column(String(20), default="balanced")
    language = Column(String(10), default="ru")
    auto_collect = Column(Boolean, default=False)
    receive_learning_report = Column(Boolean, default=False)
    notify_drawdown = Column(Boolean, default=True)
    daily_digest = Column(Boolean, default=False)
    max_watchlist_size = Column(Integer, default=30)
    autocollect_tickers = Column(JSON, default=list)
    notify_outside_scope = Column(String(10), default="all")

    # Relations
    watchlist = relationship("Watchlist", back_populates="user", cascade="all, delete-orphan")
    signals = relationship("Signal", back_populates="user", cascade="all, delete-orphan")

    def watchlist_symbols(self) -> list[str]:
        return [w.symbol for w in self.watchlist]


class Watchlist(Base):
    """Watchlist пользователя."""
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    symbol = Column(String(20), nullable=False)
    added_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="watchlist")

    __table_args__ = (
        Index("ix_watchlist_user_symbol", "user_id", "symbol", unique=True),
    )


class Signal(Base):
    """Сигнал (запись из signal_log)."""
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # null = system signal
    symbol = Column(String(20), nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    # Core scores
    score = Column(Float, nullable=False)
    score_before_macro = Column(Float)
    confidence = Column(Float)
    signal_tier = Column(String(5))
    direction = Column(String(10))  # long, short, neutral

    # Component scores
    technical_score = Column(Float)
    momentum_score = Column(Float)
    news_score = Column(Float)
    volume_score = Column(Float)
    intraday_score = Column(Float, nullable=True)

    # Trade plan
    ref_price = Column(Float)
    tp_entry = Column(Float, nullable=True)
    tp_stop = Column(Float, nullable=True)
    tp_target1 = Column(Float, nullable=True)
    tp_target2 = Column(Float, nullable=True)
    tp_max_hold_days = Column(Integer, nullable=True)

    # Context
    adx14 = Column(Float)
    atr_pct = Column(Float, nullable=True)
    macro_dampening = Column(Float)
    regime = Column(String(30))
    weekly_regime = Column(String(30))

    # Relations
    user = relationship("User", back_populates="signals")
    outcome = relationship("Outcome", back_populates="signal", uselist=False)

    __table_args__ = (
        Index("ix_signals_symbol_created", "symbol", "created_at"),
        Index("ix_signals_tier_created", "signal_tier", "created_at"),
    )


class Outcome(Base):
    """Результат сигнала (из outcome_tracker)."""
    __tablename__ = "outcomes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(Integer, ForeignKey("signals.id"), unique=True, nullable=False)
    checked_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    outcome = Column(String(20), nullable=False)  # win_t1, win_t2, loss, timeout
    exit_price = Column(Float, nullable=True)
    exit_date = Column(DateTime, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    hold_days = Column(Integer, nullable=True)

    signal = relationship("Signal", back_populates="outcome")

    __table_args__ = (
        Index("ix_outcomes_outcome", "outcome"),
    )


class Subscription(Base):
    """История подписок."""
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    tier = Column(String(20), nullable=False)  # pro, premium
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=False)
    payment_id = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True)


class NotifyLog(Base):
    """Лог уведомлений (anti-spam)."""
    __tablename__ = "notify_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    symbol = Column(String(20), nullable=False)
    notified_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_notify_user_symbol", "user_id", "symbol"),
    )


class DailyUsage(Base):
    """Счётчик дневных использований (rate limiting, persistent across restarts)."""
    __tablename__ = "daily_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    date = Column(String(10), nullable=False)  # YYYY-MM-DD
    count = Column(Integer, default=0)
    us_count = Column(Integer, default=0)  # анализов US тикеров
    ru_count = Column(Integer, default=0)  # анализов RU тикеров

    __table_args__ = (
        Index("ix_daily_usage_user_date", "user_id", "date", unique=True),
    )


# ── Session management ───────────────────────────────────────────────────────


@contextmanager
def get_session(read_only: bool = False) -> Generator[Session, None, None]:
    """Context manager для сессии БД.

    read_only=True — только чтение, без auto-commit (для SELECT-запросов).
    read_only=False — commit при успешном выходе (для INSERT/UPDATE/DELETE).
    """
    session = _SessionFactory()
    try:
        yield session
        if not read_only:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Создать все таблицы (для первого запуска или dev)."""
    Base.metadata.create_all(_engine)


def db_available() -> bool:
    """Проверить доступность БД."""
    try:
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
