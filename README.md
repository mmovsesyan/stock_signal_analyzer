<p align="center">
  <h1 align="center">📈 Stock Signal Analyzer</h1>
  <p align="center"><strong>AI-powered self-learning trading signal system</strong></p>
  <p align="center">
    Многофакторный анализ • LLM sentiment • Самообучение • Telegram бот • REST API
  </p>
</p>

---

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/Docker-ready-2496ED?logo=docker" alt="Docker">
  <img src="https://img.shields.io/badge/AI-Ollama_LLM-orange" alt="AI">
  <img src="https://img.shields.io/badge/Markets-US_&_RU-green" alt="Markets">
  <img src="https://img.shields.io/badge/Tests-82_passed-brightgreen" alt="Tests">
  <img src="https://img.shields.io/badge/Version-2.0-purple" alt="Version">
</p>

---

## 💡 Что это

Stock Signal Analyzer — система, которая **анализирует акции по 7+ факторам**, генерирует торговые планы и **автоматически учится на своих результатах** через локальную AI-модель.

В отличие от обычных скринеров:
- 🧠 **Самообучается** — каждые 6 часов анализирует свои прошлые сигналы и корректирует веса
- 🤖 **LLM внутри** — локальная модель (Ollama) понимает финансовый контекст новостей
- 📊 **Не один индикатор, а 7 факторов** — техника, импульс, новости, объём, макро, квант-модели, real-time
- 🎯 **Готовые торговые планы** — entry, stop, targets, R:R, position size
- 📱 **Telegram + API** — получайте сигналы куда удобно

---

## 🚀 Быстрый старт (5 минут)

```bash
git clone <repository-url>
cd stock_signal_analyzer
./scripts/deploy.sh
```

Интерактивное меню проведёт через всю установку:
- Запросит API ключи (с инструкциями и ссылками)
- Установит Docker, PostgreSQL, Redis, Ollama
- Скачает AI-модель
- Запустит все сервисы

**Требования:** Linux/macOS, 4 GB RAM, Docker.

---

## 📱 Telegram бот — команды

### Аналитика

| Команда | Описание |
|---------|----------|
| `/signal AAPL` | Полный анализ с торговым планом (10-30 сек) |
| `/signal SBER.ME` | Анализ российской акции |
| `/price AAPL` | Быстрая котировка (1 сек) |
| `/dashboard` | Свод по вашему watchlist |
| `/dashboard AAPL MSFT GOOGL` | Свод по указанным тикерам |

### Watchlist и подбор

| Команда | Описание |
|---------|----------|
| `/watchlist add AAPL MSFT` | Добавить тикеры в watchlist |
| `/watchlist remove AAPL` | Удалить из watchlist |
| `/watchlist` или `/list` | Показать watchlist |
| `/pick` | Подбор тикеров по категориям (интерактивные кнопки) |

### Сбор и экспорт

| Команда | Описание |
|---------|----------|
| `/collect` | Запустить массовый сбор сигналов |
| `/status` | Статус сбора (сколько сигналов накоплено) |
| `/export` | Выгрузить лог сигналов в файл |

### Настройки (через меню бота)

| Действие | Как |
|----------|-----|
| Включить/выключить уведомления | ⚙️ Настройки → 🔔 Уведомления |
| Настроить автосбор | ⚙️ Настройки → 🤖 Автосбор |
| Добавить свои тикеры в автосбор | ⚙️ Настройки → ➕ Добавить тикеры |
| Включить дефолтные 30 тикеров | ⚙️ Настройки → ✅ Дефолтные тикеры |

### Админ-команды (только для владельца)

| Команда | Описание |
|---------|----------|
| `/approve 123456789` | Одобрить пользователя (план Free) |
| `/approve 123456789 pro` | Одобрить с планом Pro |
| `/approve 123456789 premium` | Одобрить с планом Premium |
| `/deny 123456789` | Отклонить / заблокировать |
| `/users` | Список всех одобренных пользователей |

---

## 🔐 Система доступов

Бот работает по модели **admin-approval**: новые пользователи не получают доступ автоматически.

**Как это работает:**

1. Новый пользователь отправляет `/start`
2. Видит приветствие и выбирает план (Free / Pro / Premium)
3. Админу приходит уведомление с кнопками одобрения
4. Админ нажимает кнопку — пользователь получает доступ

**Что видит админ:**
```
🆕 Новый пользователь запрашивает доступ

👤 Имя: Иван Петров
📛 Username: @ivanpetrov
🆔 ID: 123456789
🌐 Язык: ru
📋 Выбранный план: ⭐ Pro

[✅ Одобрить (Pro)] [❌ Отклонить]
[🆓 Дать Free] [⭐ Дать Pro] [💎 Дать Premium]
```

Если `ADMIN_CHAT_ID` не задан — доступ открыт всем (без модерации).

---

## 🔌 REST API

```bash
# Health check
curl http://localhost:8000/health

# Детальный health (все компоненты)
curl http://localhost:8000/health/detailed

# Быстрая котировка
curl http://localhost:8000/quote/AAPL

# Полный анализ (GET)
curl http://localhost:8000/analyze/AAPL

# Полный анализ (POST, с параметрами)
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"symbol": "MSFT", "fast_mode": false}'

# Быстрый анализ (без новостей, 3-5 сек вместо 15-30)
curl "http://localhost:8000/analyze/AAPL?fast=true"

# Статистика системы
curl http://localhost:8000/stats

# Отчёт об обучении
curl http://localhost:8000/learning/report

# Информация о подписке
curl http://localhost:8000/subscription/123456789
```

---

## 🛠️ Управление — интерактивное меню

```bash
./scripts/deploy.sh
```

```
╔══════════════════════════════════════════════════════════════╗
║        Stock Signal Analyzer — Deploy & Manage              ║
╚══════════════════════════════════════════════════════════════╝

  ── Установка и настройка ──────────────
    1) 🚀 Полная установка (с нуля)
    2) 🔑 Настроить API ключи
    3) 📦 Обновить (git pull + rebuild)

  ── Управление ─────────────────────────
    4) ▶️  Запустить
    5) ⏹️  Остановить
    6) 🔄 Перезапустить
    7) 📊 Масштабировать workers

  ── Мониторинг ─────────────────────────
    8) 📋 Статус и health check
    9) 📜 Логи

  ── Аналитика ─────────────────────────
   10) 🧠 Обучение (learning)
   11) 📈 Бэктест
```

Или напрямую:

```bash
./scripts/deploy.sh install     # Полная установка
./scripts/deploy.sh configure   # Перенастроить ключи
./scripts/deploy.sh start       # Запустить
./scripts/deploy.sh stop        # Остановить
./scripts/deploy.sh restart     # Перезапустить
./scripts/deploy.sh status      # Статус
./scripts/deploy.sh logs        # Логи (выбор сервиса)
./scripts/deploy.sh scale       # Масштабировать workers
./scripts/deploy.sh update      # Обновить код
./scripts/deploy.sh learning    # Управление обучением
./scripts/deploy.sh backtest    # Бэктестирование
```

---

## ⚙️ Конфигурация — что можно менять

Все параметры в файле `.env`. Изменить можно через меню (`./scripts/deploy.sh configure`) или вручную:

```bash
nano .env
./scripts/deploy.sh restart   # применить изменения
```

### API ключи

| Переменная | Что даёт | Где получить |
|-----------|----------|:---:|
| `TELEGRAM_BOT_TOKEN` | Telegram бот | [t.me/BotFather](https://t.me/BotFather) |
| `ADMIN_CHAT_ID` | Управление доступом (ваш Telegram ID) | [t.me/userinfobot](https://t.me/userinfobot) |
| `POLYGON_API_KEY` | US котировки, свечи, новости | [massive.com](https://massive.com/dashboard/signup) |
| `FINNHUB_API_KEY` | US real-time, аналитика | [finnhub.io](https://finnhub.io/register) |
| `TINKOFF_INVEST_TOKEN` | РФ real-time | [tbank.ru/invest/settings/api](https://www.tbank.ru/invest/settings/api/) |

### Уведомления в MAX (опционально)

| Переменная | Описание |
|-----------|----------|
| `MAX_BOT_TOKEN` | Токен бота MAX (получить у @MasterBot в MAX) |
| `MAX_CHAT_ID` | ID чата для уведомлений |
| `MAX_NOTIFY` | `1` = включить, `0` = выключить |

Сильные сигналы и заявки новых пользователей дублируются в MAX параллельно с Telegram. Если MAX не настроен — всё работает только через Telegram.

### Автоматизация (интервалы)

| Переменная | По умолчанию | Описание |
|-----------|:---:|----------|
| `COLLECT_INTERVAL_SEC` | `14400` (4ч) | Автосбор сигналов. `0` = выключить |
| `NOTIFY_INTERVAL_SEC` | `3600` (1ч) | Проверка сильных сигналов вне watchlist |
| `LEARN_INTERVAL_SEC` | `21600` (6ч) | Цикл обучения (IC + LLM) |
| `NOTIFY_MIN_TIER` | `A` | Минимальный класс для уведомлений (`A`, `B`, `C`) |

### LLM (AI модель)

| Переменная | По умолчанию | Описание |
|-----------|:---:|----------|
| `OLLAMA_MODEL` | `qwen2.5:1.5b` | Модель. Варианты: `gemma2:2b`, `phi3:mini` |
| `LLM_SENTIMENT` | `1` | Включить LLM sentiment. `0` = только VADER |
| `LLM_LEARNING` | `1` | Включить LLM обучение. `0` = только числовой IC |
| `LLM_CACHE_TTL` | `3600` | Кэш LLM ответов (секунды) |

### API и лимиты

| Переменная | По умолчанию | Описание |
|-----------|:---:|----------|
| `API_RATE_LIMIT_PER_MIN` | `30` | Лимит запросов на IP в минуту |
| `SUBSCRIPTION_ENABLED` | `0` | Включить систему подписок |

### Масштабирование

```bash
# Добавить workers (параллельный анализ)
docker compose up -d --scale worker=4

# Или через меню
./scripts/deploy.sh scale
```

---

## 🧠 Система самообучения

### Как это работает

```
Бот генерирует сигналы → outcome tracker проверяет достижение целей/стопов
                                              ↓
                              Числовой IC + LLM анализ паттернов
                                              ↓
                              Корректировка весов компонентов
                                              ↓
                              Следующие сигналы точнее
```

### Три уровня обучения

| Уровень | Что делает | Когда работает |
|---------|-----------|:---:|
| **IC (числовой)** | Корреляция каждого компонента с реальным PnL | Всегда (≥30 outcomes) |
| **Паттерн-анализ** | Оптимальные пороги, delta wins vs losses | Всегда (≥20 outcomes) |
| **LLM (Ollama)** | Качественный анализ: "высокий news + низкий volume = ложный сигнал" | Если Ollama доступен |

### Управление обучением

```bash
# Запустить обучение вручную
./scripts/deploy.sh learning

# Или через API
curl http://localhost:8000/learning/report

# Или напрямую
docker compose exec api python -m stock_signal_analyzer.llm_learning --force
```

---

## 📊 Бэктестирование

### v1 — по сохранённым сигналам

```bash
# Через меню
./scripts/deploy.sh backtest

# Или напрямую
docker compose exec api python tools/backtest.py /data/signals/signals.jsonl --min-tier A
docker compose exec api python tools/backtest.py /data/signals/signals.jsonl --min-tier B --target 2
```

### v2 — candle replay (полная эмуляция)

```bash
docker compose exec api python tools/backtest_v2.py \
  --symbols AAPL MSFT GOOGL NVDA \
  --days 180 \
  --slippage 0.02 \
  --commission 0.1 \
  --export /data/backtest_results.json
```

Метрики: Sharpe, Sortino, Calmar, max drawdown, profit factor, equity curve, breakdown по tier и symbol.

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
              │            │            │
              └────────────┼────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    engine.build_report()                      │
│                                                              │
│  Technical + Momentum + Sentiment(VADER+LLM) + Volume        │
│  + Quant Models + Macro + Intraday                           │
│  → Score → Tier → Trade Plan                                 │
└──────────────────────────┬──────────────────────────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
    PostgreSQL          Ollama           Data APIs
    (users,signals,    (qwen2.5:1.5b)   (Yahoo, Polygon,
     outcomes)                           Finnhub, MOEX,
                                         T-Bank)
```

---

## 📂 Структура проекта

```
├── scripts/
│   └── deploy.sh                    # 🎯 Единая точка входа (меню)
│
├── stock_signal_analyzer/           # Основной пакет
│   ├── engine.py                    # Ядро анализа
│   ├── technical.py                 # RSI, MACD, Bollinger, ADX, паттерны
│   ├── momentum.py                  # ROC, acceleration
│   ├── sentiment.py                 # VADER + финансовый лексикон
│   ├── llm_sentiment.py            # LLM анализ новостей (Ollama)
│   ├── llm_learning.py             # Самообучение (IC + LLM)
│   ├── volume_pressure.py           # CMF, OBV, tape
│   ├── quant_models.py              # MTF momentum, z-score, vol regime
│   ├── polygon_data.py              # Massive (ex-Polygon.io) API
│   ├── finnhub_live.py              # Finnhub API
│   ├── trade_plan.py                # Торговые планы
│   ├── risk_manager.py              # Position sizing
│   ├── db.py                        # PostgreSQL (SQLAlchemy)
│   ├── tasks.py                     # Celery tasks
│   ├── scheduler.py                 # Планировщик задач
│   ├── subscriptions.py             # Тарифы и лимиты
│   ├── monitoring.py                # Health checks
│   └── ...                          # 30+ модулей
│
├── api/main.py                      # REST API (FastAPI)
├── telegram_bot.py                  # Telegram бот
├── main.py                          # CLI
├── tools/
│   ├── backtest.py                  # Бэктест v1
│   └── backtest_v2.py               # Бэктест v2 (candle replay)
│
├── docker-compose.yml               # Все сервисы
├── Dockerfile
└── requirements*.txt
```

---

## 🐳 Docker сервисы

| Сервис | Порт | Описание |
|--------|:----:|----------|
| `bot` | — | Telegram бот |
| `api` | 8000 | REST API (FastAPI) |
| `worker` | — | Celery workers (масштабируемые) |
| `beat` | — | Периодические задачи |
| `postgres` | 5432 | PostgreSQL 16 |
| `redis` | 6379 | Redis 7 (очередь + кэш) |
| `ollama` | 11434 | LLM (sentiment + обучение) |

---

## 📈 Пример вывода сигнала

```
📈 AAPL — Apple Inc.
Тип: US equity, blue chip

ТОРГОВЫЙ ПЛАН
🟢 LONG AAPL @ 198.50
Стоп: 194.80 (-1.86%)
Цель 1: 203.20 (+2.37%)  R:R 1.3 — закрыть 50%
Цель 2: 207.40 (+4.48%)  R:R 2.4 — остаток
Трейлинг: после +3.0% стоп → безубыток
Удержание: до 5 дней  |  Позиция: 12%  |  Класс: A

Итог: +0.312 (-1…+1)
Согласованность: 0.78  |  ADX14≈26.3  |  Режим: trending
Класс качества: A

Компоненты:
  Техника:  +0.380  |  RSI14=62.1, MACD бычий, выше SMA50
  Импульс:  +0.290  |  5д: +1.8%, 20д: +4.2%, ускорение +
  Новости:  +0.210  |  LLM: bullish, катализаторы: earnings beat
  Объём:    +0.250  |  CMF=+0.22, объём +18% vs среднего

Квант-модели: +0.180
  MTF momentum: aligned across timeframes
  Trend strength: strong (score=0.72)

📝 Вывод простым языком
💰 Цена: $198.50
📈 Направление: заметный сигнал на рост
✅ Индикаторы хорошо согласованы
✅ Класс A — сильный сигнал. Можно рассмотреть покупку.
```

---

## 💰 Тарифы (при включении подписок)

| | Free | Pro | Premium |
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

## 📋 Требования

| Компонент | Минимум | Рекомендуется |
|-----------|:---:|:---:|
| RAM | 4 GB | 8 GB |
| Диск | 5 GB | 10 GB |
| CPU | 2 cores | 4 cores |
| OS | Ubuntu 22.04 | Ubuntu 24.04 |
| Docker | 24+ | latest |
| Python | 3.9+ | 3.11+ |

---

## 🔑 Где получить ключи

| Сервис | Ссылка | Free tier |
|--------|--------|-----------|
| Telegram Bot | [t.me/BotFather](https://t.me/BotFather) | Бесплатно |
| Ваш Telegram ID | [t.me/userinfobot](https://t.me/userinfobot) | Бесплатно |
| Massive (ex-Polygon) | [massive.com/dashboard/signup](https://massive.com/dashboard/signup) | 5 req/min |
| Finnhub | [finnhub.io/register](https://finnhub.io/register) | 60 req/min |
| T-Bank Invest | [tbank.ru/invest/settings/api](https://www.tbank.ru/invest/settings/api/) | Бесплатно |
| MAX мессенджер | @MasterBot в MAX → /newbot | Бесплатно |
| Ollama | Устанавливается автоматически | Локально, бесплатно |

---

## 🛡️ Безопасность

- API ключи хранятся в `.env` с правами `600` (только владелец)
- PostgreSQL пароль генерируется автоматически
- API rate limiting per IP
- Ollama работает локально (данные не уходят наружу)
- Telegram Bot Token не логируется
- WebSocket URLs с токенами не попадают в логи

---

## ⚠️ Disclaimer

Этот инструмент предназначен только для информационных целей. Не является финансовой рекомендацией. Торговля на финансовых рынках сопряжена с риском потери капитала. Всегда проводите собственный анализ.

---

<p align="center">
  <strong>Version 2.1.0</strong> • Updated 2026-05-09
</p>
