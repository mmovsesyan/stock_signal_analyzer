<p align="center">
  <h1 align="center">📈 Stock Signal Analyzer</h1>
  <p align="center"><strong>AI-powered self-learning trading signal system</strong></p>
  <p align="center">
    Многофакторный анализ • LLM sentiment • Самообучение на outcomes • Telegram бот • REST API
  </p>
</p>

---

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/Docker-ready-2496ED?logo=docker" alt="Docker">
  <img src="https://img.shields.io/badge/AI-Ollama_LLM-orange" alt="AI">
  <img src="https://img.shields.io/badge/Markets-US_&_RU-green" alt="Markets">
  <img src="https://img.shields.io/badge/Tests-103_passed-brightgreen" alt="Tests">
  <img src="https://img.shields.io/badge/Version-2.3-purple" alt="Version">
</p>

---

## 💡 Что это

**Stock Signal Analyzer** — система анализа торговых сигналов с самообучением. Анализирует акции по 7+ факторам, генерирует готовые торговые планы с entry/stop/targets, автоматически отслеживает результаты и **учится на своих ошибках** через AI-модель.

В отличие от обычных скринеров:

- 🧠 **Самообучение** — каждые 6 часов анализирует прошлые сигналы через IC (Information Coefficient) + LLM, корректирует веса компонентов
- 🤖 **AI анализ новостей** — Ollama Cloud API или локальный Ollama (qwen2.5:1.5b) понимает финансовый контекст
- 📊 **7 факторов** — техника, импульс, новости, объём, макро-данные, квант-модели, real-time price
- 🏛️ **Режим рынка** — автоопределение bull/bear/sideways для адаптивных порогов и риск-менеджмента
- 🎯 **Торговые планы** — entry, stop, два targets, R:R ≥ 1.5, ATR-based position size
- 📊 **Бэктест с графиками** — equity curve, drawdown, monthly returns, PnL distribution, tier stats
- 📡 **TradingView webhook** — принимайте алерты из TradingView и усиливайте SSA-анализом
- 📱 **Telegram + REST API** — получайте сигналы куда удобно
- ✅ **Win rate фильтр** — сигналы генерируются только при ADX > 20, score ≥ 0.30, R:R ≥ 1.5

---

## 📋 Требования

| Компонент | Минимум | Рекомендуется |
|-----------|:-------:|:-------------:|
| Python | 3.11+ | 3.12 |
| RAM | 2 GB | 4 GB |
| Диск | 5 GB | 10 GB |
| CPU | 2 cores | 4 cores |
| OS | Ubuntu 22.04 / Debian 12 | Ubuntu 24.04 |
| Docker | 24+ | latest |

**Зависимости:**
- **PostgreSQL** — основная БД (в Docker-режиме поднимается автоматически; для systemd — нужен локальный сервер)
- **SQLite** — альтернатива PostgreSQL для минимальной установки
- **Redis** (опционально) — очередь задач Celery для параллельного анализа
- **Ollama Cloud API** (по умолчанию) — AI анализ новостей без нагрузки на сервер
- **Ollama локальный** (опционально) — если хотите локальный LLM; требует +4 GB RAM

---

## 🚀 Быстрый старт

```bash
git clone <repository-url>
cd stock_signal_analyzer
./scripts/deploy.sh
```

Интерактивное меню проведёт через всю установку — запросит API ключи (с инструкциями), установит зависимости, запустит сервисы.

### Три режима запуска

| Режим | Команда | Когда использовать |
|-------|---------|-------------------|
| **Docker** (рекомендуется) | `./scripts/deploy.sh install` → выбрать Docker | Продакшн, полный стек |
| **systemd** | `./scripts/deploy.sh install` → выбрать systemd | VPS без Docker |
| **Тесты** | `./scripts/deploy.sh tests` | CI/CD, разработка |

### Прямые команды

```bash
./scripts/deploy.sh install     # Полная установка (с нуля)
./scripts/deploy.sh configure   # Перенастроить API ключи
./scripts/deploy.sh start       # Запустить сервисы
./scripts/deploy.sh stop        # Остановить
./scripts/deploy.sh restart     # Перезапустить
./scripts/deploy.sh status      # Статус и health check
./scripts/deploy.sh logs        # Логи (выбор сервиса)
./scripts/deploy.sh update-deps # Обновить Python зависимости
./scripts/deploy.sh update      # git pull + пересборка
./scripts/deploy.sh learning    # Управление самообучением
./scripts/deploy.sh backtest    # Бэктестирование
./scripts/deploy.sh tests       # Запустить pytest
./scripts/deploy.sh uninstall   # Удалить всё
```

---

## ⚙️ Переменные окружения

Все параметры в `.env`. Создаётся автоматически при первом запуске `./scripts/deploy.sh` или скопируйте вручную:

```bash
cp .env.example .env
nano .env
```

### Обязательные

| Переменная | Описание | Где получить |
|-----------|----------|:---:|
| `TELEGRAM_BOT_TOKEN` | Токен Telegram бота | [@BotFather](https://t.me/BotFather) |
| `ADMIN_CHAT_ID` | Ваш Telegram ID (для управления доступом) | [@userinfobot](https://t.me/userinfobot) |

### API ключи (опционально, но рекомендуется)

| Переменная | Что даёт | Где получить |
|-----------|----------|:---:|
| `POLYGON_API_KEY` | US котировки, исторические свечи, новости | [massive.com](https://massive.com/dashboard/signup) |
| `FINNHUB_API_KEY` | US real-time котировки, макро-календарь | [finnhub.io](https://finnhub.io/register) |
| `TINKOFF_INVEST_TOKEN` | Мосбиржа real-time, VWAP/POC | [tbank.ru/invest](https://www.tbank.ru/invest/settings/api/) |

### База данных

| Переменная | По умолчанию | Описание |
|-----------|:---:|----------|
| `DATABASE_URL` | `postgresql://ssa:pass@localhost:5432/stock_signals` | URL подключения к PostgreSQL |
| `POSTGRES_PASSWORD` | генерируется | Пароль PostgreSQL |

### Очередь задач (Redis + Celery)

| Переменная | По умолчанию | Описание |
|-----------|:---:|----------|
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Redis для очереди |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/1` | Redis для результатов |

### LLM (Ollama)

| Переменная | По умолчанию | Описание |
|-----------|:---:|----------|
| `OLLAMA_HOST` | `http://localhost:11434` | URL Ollama |
| `OLLAMA_MODEL` | `qwen2.5:1.5b` | Модель для sentiment + обучения |
| `LLM_SENTIMENT` | `1` | Включить LLM sentiment (`0` = только VADER) |
| `LLM_LEARNING` | `1` | Включить LLM обучение (`0` = только числовой IC) |
| `LLM_CACHE_TTL` | `3600` | TTL кэша LLM ответов (сек) |

### Автоматизация

| Переменная | По умолчанию | Описание |
|-----------|:---:|----------|
| `COLLECT_INTERVAL_SEC` | `14400` (4ч) | Автосбор сигналов (`0` = выкл) |
| `NOTIFY_INTERVAL_SEC` | `3600` (1ч) | Проверка сильных сигналов |
| `LEARN_INTERVAL_SEC` | `21600` (6ч) | Цикл самообучения (IC + LLM) |
| `NOTIFY_MIN_TIER` | `A` | Минимальный класс для уведомлений |

### Пути к данным

| Переменная | По умолчанию | Описание |
|-----------|:---:|----------|
| `STOCK_SIGNAL_DATA` | `/var/lib/stock_signal_analyzer` | Директория данных |
| `SSA_SIGNAL_LOG` | `<data>/signals.jsonl` | Лог сигналов |

### MAX мессенджер (опционально)

| Переменная | Описание |
|-----------|----------|
| `MAX_BOT_TOKEN` | Токен бота MAX (получить у @MasterBot в MAX) |
| `MAX_CHAT_ID` | ID чата для уведомлений |
| `MAX_NOTIFY` | `1` = включить дублирование сигналов в MAX |

---

## 🏗️ Архитектура

```
┌─────────────────────────────────────────────────────────────┐
│                     ПОЛЬЗОВАТЕЛИ                             │
│         Telegram Bot    REST API    CLI                      │
└──────────┬──────────────┬───────────┬───────────────────────┘
           │              │           │
           ▼              ▼           ▼
┌─────────────────────────────────────────────────────────────┐
│                    Redis Queue (Celery)                       │
└──────────────────────────┬──────────────────────────────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         Worker 1     Worker 2     Worker N
              └────────────┼────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                  engine.build_report()                        │
│  Technical + Momentum + Sentiment(VADER+LLM) + Volume        │
│  + Quant Models + Macro + Intraday + MarketRegime             │
│  → Score → Tier → Plan                                       │
└──────────┬──────────────┬─────────────┬────────────────────┘
           │              │             │
    PostgreSQL         LLM Client   Data APIs
    (users,         (Ollama Cloud  (Yahoo, Polygon,
     signals,         или local)     Finnhub, MOEX,
     outcomes)                        T-Bank)
```

### Модули

| Модуль | Описание |
|--------|----------|
| `engine.py` | Главный пайплайн — собирает все компоненты, считает score, формирует `SignalReport` |
| `technical.py` | Технический анализ: RSI, MACD, Bollinger Bands, ADX, свечные паттерны |
| `momentum.py` | Импульс: ROC за 5/20/60 дней, ускорение тренда |
| `sentiment.py` + `llm_sentiment.py` | VADER + LLM — анализ новостей и заголовков |
| `volume_pressure.py` | Объём: CMF, OBV, VWAP tape |
| `quant_models.py` | Квант-модели: MTF momentum, z-score, vol regime |
| `market_regime.py` | Определение режима рынка: bull / bear / sideways / transition |
| `signal_filter.py` | Фильтрация: win rate >65%, ADX > 20 (жёсткий блок), score ≥ 0.30 |
| `trade_plan.py` | Торговые планы: entry/stop/targets, R:R ≥ 1.5, ATR-based position size |
| `backtest_charts.py` | Генерация графиков бэктеста (matplotlib) |
| `llm_client.py` | Унифицированный клиент LLM (Ollama Cloud / local) |
| `outcome_tracker.py` | Отслеживание исходов открытых сигналов (win/loss/timeout) |
| `adaptive_weights.py` | IC-адаптация весов: ранговая корреляция Спирмена компонент с реальным PnL |
| `llm_learning.py` | Самообучение: числовой IC-анализ + LLM паттерн-анализ побед/поражений |
| `scheduler.py` | Фоновые задачи: автосбор, outcome check, learning cycle |
| `db.py` | PostgreSQL через SQLAlchemy: пользователи, сигналы, outcomes |
| `subscriptions.py` | Тарифы Free/Pro/Premium и лимиты |
| `risk_manager.py` | Sizing: Kelly criterion, vol targeting, drawdown control |

---

## 🧠 Система самообучения

```
Бот генерирует сигналы
        ↓
outcome_tracker.py — каждый час проверяет достижение TP/стопов
        ↓
adaptive_weights.py — IC (ранговая корреляция компонентов с PnL)
        ↓
llm_learning.py — числовой анализ + LLM паттерны (каждые 6ч)
        ↓
engine.py — применяет скорректированные веса (blend 30%)
```

**Три уровня обучения:**

| Уровень | Метод | Порог |
|---------|-------|:-----:|
| IC числовой | Weighted Spearman correlation | ≥ 50 закрытых outcomes |
| Паттерн-анализ | Статистика wins vs losses по компонентам | ≥ 30 outcomes |
| LLM анализ | Ollama анализирует топ-5 wins/losses, находит паттерны | ≥ 30 outcomes + Ollama |

**Диапазон корректировок весов:** ×0.75 — ×1.25 (не даёт переобучиться на малой выборке).

---

## 💰 Тарифы

| | 🆓 Free | ⭐ Pro | 💎 Premium |
|---|:---:|:---:|:---:|
| Анализов/день | 5 | 50 | ∞ |
| Рынки | US | US + RU | Все |
| LLM sentiment | ❌ | ✅ | ✅ |
| Автосбор | ❌ | ✅ | ✅ |
| Уведомления | ❌ | ✅ | ✅ |
| Per-user learning | ❌ | ❌ | ✅ |
| Watchlist | 5 | 30 | 100 |

Включить: `SUBSCRIPTION_ENABLED=1` в `.env`.

---

## 🧪 Запуск тестов

```bash
# Через меню
./scripts/deploy.sh tests

# Или напрямую (local/venv)
cd stock_signal_analyzer
python3 -m pytest tests/ -v

# Docker
docker compose exec api python3 -m pytest tests/ -v
```

---

## 📱 Telegram бот — команды

### Аналитика

| Команда | Описание |
|---------|----------|
| `/signal AAPL` | Полный анализ с торговым планом (10-30 сек) |
| `/signal SBER.ME` | Анализ российской акции |
| `/price AAPL` | Быстрая котировка |
| `/dashboard` | Свод по watchlist |

### Управление

| Команда | Описание |
|---------|----------|
| `/watchlist add AAPL MSFT` | Добавить в watchlist |
| `/collect` | Запустить массовый сбор сигналов |
| `/status` | Статус системы |
| `/export` | Выгрузить лог сигналов |

### Админ-команды

| Команда | Описание |
|---------|----------|
| `/approve 123456789 pro` | Одобрить пользователя с тарифом |
| `/deny 123456789` | Заблокировать |
| `/users` | Список пользователей |

---

## 🔌 REST API

```bash
curl http://localhost:8000/health              # Health check
curl http://localhost:8000/analyze/AAPL        # Полный анализ
curl "http://localhost:8000/analyze/AAPL?fast=true"  # Быстрый анализ
curl http://localhost:8000/stats               # Статистика
curl http://localhost:8000/learning/report     # Отчёт об обучении
```

### TradingView Webhook

Принимайте алерты из TradingView и усиливайте SSA-анализом:

**Настройка в TradingView:**
```
URL: https://your-host/webhook/tradingview
Message: {"symbol":"{{ticker}}","price":{{close}},"side":"{{strategy.order.action}}","interval":"{{interval}}","time":"{{time}}"}
```

**Опциональная защита:** установите `TV_WEBHOOK_SECRET` в `.env`.

**Пример запроса:**
```bash
curl -X POST http://localhost:8000/webhook/tradingview \
  -H "Content-Type: application/json" \
  -d '{"symbol":"AAPL","price":150.25,"side":"buy","interval":"1h"}'
```

**Ответ:** SSA score, tier, direction, trade plan, и сообщение о совпадении/несовпадении с внешним сигналом.

---

## 🐳 Docker сервисы

| Сервис | Порт | Описание |
|--------|:----:|----------|
| `bot` | — | Telegram бот |
| `api` | 8000 | REST API (FastAPI) |
| `worker` | — | Celery workers |
| `beat` | — | Периодические задачи |
| `postgres` | 5432 | PostgreSQL 16 |
| `redis` | 6379 | Redis 7 |

---

## 📂 Структура проекта

```
├── scripts/
│   └── deploy.sh                    # 🎯 Единая точка входа
│
├── stock_signal_analyzer/           # Основной пакет
│   ├── engine.py                    # Главный пайплайн
│   ├── technical.py                 # RSI, MACD, Bollinger, ADX, паттерны
│   ├── momentum.py                  # ROC, acceleration
│   ├── sentiment.py + llm_sentiment.py  # VADER + LLM анализ
│   ├── volume_pressure.py           # CMF, OBV, VWAP
│   ├── market_regime.py             # Режим рынка (bull/bear/sideways)
│   ├── signal_filter.py             # Фильтрация (win rate >65%)
│   ├── trade_plan.py                # Торговые планы (R:R ≥ 1.5)
│   ├── backtest_charts.py           # Графики бэктеста (matplotlib)
│   ├── llm_client.py                # Унифицированный LLM клиент
│   ├── outcome_tracker.py           # Отслеживание исходов
│   ├── adaptive_weights.py          # IC-адаптация весов
│   ├── llm_learning.py              # Самообучение
│   ├── scheduler.py                 # Фоновые задачи
│   ├── db.py                        # PostgreSQL (SQLAlchemy)
│   └── ...                          # 30+ модулей
│
├── api/main.py                      # REST API (FastAPI)
├── telegram_bot.py                  # Telegram бот
├── tools/backtest.py                # Бэктест v1
├── tools/backtest_v2.py             # Бэктест v2 (candle replay)
├── docker-compose.yml
├── Dockerfile
└── requirements*.txt
```

---

## ✅ Что нового

### v2.3 (2026-05-14) — TradingView интеграция

- **TradingView webhook** (`POST /webhook/tradingview`) — принимает алерты из TradingView, нормализует символы (крипто, US, RU), усиливает SSA-анализом
- Валидация направления из нескольких полей (`direction`/`side`/`action`)
- Опциональная защита `TV_WEBHOOK_SECRET`
- Автоматическое логирование внешних сигналов + SSA результата для walk-forward валидации
- Тесты: 103 passed

### v2.2 (2026-05-14) — Режим рынка и визуализация

- **MarketRegime detection** — автоопределение bull/bear/sideways/transition через reuse существующих анализаторов
- Динамические пороги классов сигналов: bull → лонг легче, шорт сложнее; bear — наоборот; sideways — строже ADX/confidence
- Режим рынка влияет на trade plan (stop/target множители, max hold days) и position size (-15% в sideways)
- **Backtest charts** — 5 типов графиков (equity curve, drawdown, monthly returns, PnL distribution, tier stats) через matplotlib
- **Унифицированный LLM клиент** (`llm_client.py`) — Ollama Cloud API по умолчанию, локальный Ollama как fallback
- Удалён OpenRouter и Docker-контейнер Ollama для снижения нагрузки на сервер
- Обновлён `deploy.sh` — подменю LLM конфигурации, установка локального Ollama через меню
- Тесты: 87 → 97 passed (добавлены тесты regime detection)

### v2.1 (2026-05-13) — Исправления и аудит

- **trade_plan.py** — R:R всегда ≥ 1.5; `_DIR_THRESHOLD` поднят до 0.30; защита от ZeroDivisionError
- **signal_filter.py** — ADX < 20 жёсткий блок; score < 0.30 принудительный блок
- **risk_context.py** — Tier B требует confidence ≥ 0.50 и ADX ≥ 20.0
- **outcome_tracker.py** — timeout 5 дней; защита от None `pnl_pct`
- **volume_pressure.py** — защита от ZeroDivisionError и `log(0)`
- **llm_learning.py** — минимум outcomes 30; TTL кэша весов 1 час
- Тесты: добавлены 15 тестов (R:R, ADX, score, volume, tier B)

---

## 🛡️ Безопасность

- `.env` хранится с правами `600` (только владелец)
- PostgreSQL пароль генерируется автоматически
- API rate limiting per IP + опциональный `X-API-Key`
- Ollama Cloud API или локальный Ollama — данные не передаются третьим лицам
- Токены не попадают в логи

---

## ⚠️ Disclaimer

Только для информационных целей. Не является финансовой рекомендацией. Торговля на финансовых рынках сопряжена с риском потери капитала. Всегда проводите собственный анализ.

---

<p align="center">
  <strong>Version 2.1.0</strong> • Updated 2026-05-13
</p>
