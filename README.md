# 📈 Stock Signal Analyzer

**Самообучающаяся система многофакторного анализа торговых сигналов с AI.**

Анализирует технические индикаторы, импульс, новости (VADER + LLM), объёмы, макро-контекст и квантовые модели. Генерирует торговые планы с точками входа, стоп-лоссами и тейк-профитами. Автоматически обучается на результатах своих сигналов через Ollama.

---

## ✨ Ключевые возможности

### 🧠 Самообучение (AI)
- **LLM анализ** через Ollama (qwen2.5:1.5b, 2-3 GB RAM)
- Автоматическое обучение на исторических outcomes каждые 6 часов
- Числовой IC (Information Coefficient) + качественный LLM-анализ паттернов
- Адаптивные веса компонентов — система сама определяет что работает лучше

### 📊 Многофакторный анализ
- **Технический**: RSI, MACD, Bollinger Bands, ADX, свечные паттерны, MACD divergence
- **Импульс**: ROC multi-timeframe, acceleration, overextension detection
- **Sentiment**: VADER + финансовый лексикон + LLM-анализ новостей
- **Объём**: CMF, OBV divergence, volume spike, tape imbalance
- **Квант-модели**: MTF momentum, z-score, volatility regime, trend strength
- **Макро**: ЦБ заседания, инфляция, cross-asset regime (risk-on/off)

### 📱 Доставка сигналов
- **Telegram бот** — интерактивное меню, watchlist, автосбор, уведомления
- **REST API** (FastAPI) — для масштабирования и интеграций
- **CLI** — быстрый анализ из терминала

### 🌍 Рынки
- 🇺🇸 US акции (Yahoo Finance, Polygon.io, Finnhub)
- 🇷🇺 Российские акции (MOEX ISS, T-Bank Invest API)
- Real-time данные через WebSocket

---

## 🏗️ Архитектура

```
┌─────────────────────────────────────────────────────────────────┐
│                        ВХОДНЫЕ ДАННЫЕ                            │
├──────────┬──────────┬──────────┬──────────┬────────────────────┤
│  Yahoo   │ Polygon  │ Finnhub  │ MOEX ISS │   T-Bank Invest    │
│ Finance  │   .io    │   API    │  (free)  │     (gRPC)         │
└────┬─────┴────┬─────┴────┬─────┴────┬─────┴────────┬───────────┘
     │          │          │          │              │
     ▼          ▼          ▼          ▼              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    engine.py — ЯДРО АНАЛИЗА                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐   │
│  │Technical │ │ Momentum │ │Sentiment │ │ Volume Pressure  │   │
│  │RSI, MACD │ │ROC, accel│ │VADER+LLM │ │ CMF, OBV, tape   │   │
│  │ADX, BB   │ │MTF, trend│ │Finnhub   │ │ spike detection  │   │
│  │patterns  │ │overext.  │ │Polygon   │ │                  │   │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────────┬─────────┘   │
│       │            │            │                 │              │
│       ▼            ▼            ▼                 ▼              │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │         Взвешенный Score (-1…+1) + мультипликаторы        │   │
│  │  × macro × confidence × volume_align × weekly × regime    │   │
│  └────────────────────────────┬─────────────────────────────┘   │
│                               │                                  │
│       ┌───────────────────────┼───────────────────────┐         │
│       ▼                       ▼                       ▼         │
│  ┌─────────┐          ┌────────────┐          ┌───────────┐    │
│  │Tier A/B/C│          │Trade Plan  │          │Quant Models│    │
│  │classify  │          │entry/stop/ │          │MTF, zscore │    │
│  │          │          │targets, R:R│          │vol regime  │    │
│  └─────────┘          └────────────┘          └───────────┘    │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│                    САМООБУЧЕНИЕ (каждые 6ч)                      │
│  ┌────────────────┐    ┌─────────────────┐    ┌──────────────┐  │
│  │Outcome Tracker │───▶│ Числовой IC     │───▶│ LLM Learning │  │
│  │(проверка PnL)  │    │ (корреляции)    │    │ (Ollama)     │  │
│  └────────────────┘    └─────────────────┘    └──────┬───────┘  │
│                                                       │          │
│                              ┌─────────────────────────┘         │
│                              ▼                                   │
│                    ┌──────────────────┐                          │
│                    │learning_state.json│                          │
│                    │weight adjustments │                          │
│                    │win/loss patterns  │                          │
│                    └──────────────────┘                          │
└─────────────────────────────────────────────────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
     ┌──────────────┐  ┌────────────┐  ┌────────────┐
     │ Telegram Bot │  │  REST API  │  │    CLI     │
     │ (PTB v21)   │  │  (FastAPI) │  │ main.py    │
     └──────────────┘  └────────────┘  └────────────┘
```

---

## 📂 Структура проекта

```
stock_signal_analyzer/
├── main.py                          # CLI: python main.py AAPL
├── telegram_bot.py                  # Telegram бот (async, PTB v21)
├── api/
│   └── main.py                      # REST API (FastAPI, rate limiting)
│
├── stock_signal_analyzer/           # Основной пакет
│   ├── engine.py                    # Ядро: build_report() — объединяет всё
│   │
│   ├── # ─── Анализ ───
│   ├── technical.py                 # RSI, MACD, Bollinger, ADX, паттерны
│   ├── momentum.py                  # ROC, acceleration, overextension
│   ├── sentiment.py                 # VADER + финансовый лексикон
│   ├── llm_sentiment.py            # LLM анализ новостей (Ollama)
│   ├── volume_pressure.py           # CMF, OBV, volume spike, tape
│   ├── candlestick_patterns.py      # Свечные паттерны
│   ├── chart_patterns.py            # Графические паттерны
│   ├── levels.py                    # Support/resistance (pivot points)
│   ├── quant_models.py              # MTF momentum, z-score, vol regime
│   ├── regime.py                    # Cross-asset regime (risk-on/off)
│   │
│   ├── # ─── Обучение ───
│   ├── adaptive_weights.py          # IC tracking, адаптивные веса
│   ├── llm_learning.py             # LLM обучение на outcomes (Ollama)
│   ├── outcome_tracker.py           # Отслеживание результатов сигналов
│   │
│   ├── # ─── Данные ───
│   ├── market_data.py               # Yahoo → T-Bank → MOEX → Polygon
│   ├── polygon_data.py              # Polygon.io (котировки, свечи, новости)
│   ├── finnhub_live.py              # Finnhub REST/WS (котировки, новости)
│   ├── moex_iss.py                  # MOEX ISS (РФ, бесплатный)
│   ├── tbank_invest.py              # T-Bank Invest SDK (РФ real-time)
│   ├── news_feeds.py                # RSS / Google News
│   ├── intraday.py                  # Real-time провайдеры (4 источника)
│   │
│   ├── # ─── Торговля ───
│   ├── trade_plan.py                # Entry/stop/targets, R:R, position size
│   ├── risk_manager.py              # Kelly sizing, drawdown, circuit breaker
│   ├── risk_context.py              # ATR%, signal tier classification
│   ├── signal_filter.py             # Фильтрация (3 пресета)
│   │
│   ├── # ─── Контекст ───
│   ├── macro_calendar.py            # ЦБ, инфляция, заседания
│   ├── timing_context.py            # Earnings, weekly trend, index tailwind
│   ├── universe.py                  # Классификация инструментов
│   │
│   └── # ─── Инфраструктура ───
│       ├── telegram_format.py       # HTML форматирование
│       ├── signal_log.py            # JSONL лог сигналов
│       ├── user_store.py            # Настройки пользователей
│       └── retry_utils.py           # Retry + rate limiting
│
├── tools/
│   ├── backtest.py                  # Бэктест v1 (по signals.jsonl)
│   ├── backtest_v2.py               # Бэктест v2 (candle replay)
│   ├── monitor_signals.py           # Мониторинг сбора
│   └── summarize_signal_log.py      # Сводка по логу
│
├── scripts/
│   ├── install_ubuntu.sh            # Полная установка (1 команда)
│   └── install_ollama.sh            # Установка Ollama + модель
│
├── tests/                           # 82 unit-теста
├── requirements.txt                 # Production
├── requirements-api.txt             # FastAPI
├── requirements-dev.txt             # Dev tools
├── requirements-tbank.txt           # T-Bank SDK
├── Dockerfile
├── docker-compose.yml               # bot + api + ollama + tracker + learning
└── manage.sh                        # Интерактивное управление (VPS)
```

---

## 🚀 Быстрый старт

### Установка на Ubuntu (одна команда)

```bash
git clone <repository-url> && cd stock_signal_analyzer
sudo ./scripts/install_ubuntu.sh
```

Скрипт автоматически установит:
- Python venv + все зависимости
- Ollama + модель qwen2.5:1.5b (LLM sentiment + обучение)
- Systemd сервисы (бот, API, tracker, learning)
- Запросит API ключи и создаст `.env`

### Локальная разработка (macOS/Linux)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполнить токены

# CLI анализ
python main.py AAPL
python main.py SBER.ME --fast

# Telegram бот
python telegram_bot.py

# REST API
pip install -r requirements-api.txt
uvicorn api.main:app --port 8000
```

### Docker

```bash
docker compose up -d --build
```

Запускает: бот + API (`:8000`) + Ollama (`:11434`) + outcome tracker + learning.

---

## 🔑 API ключи

| Ключ | Для чего | Обязательный |
|------|----------|:---:|
| `TELEGRAM_BOT_TOKEN` | Telegram бот | ✅ |
| `POLYGON_API_KEY` | US котировки, свечи, новости | Рекомендуется |
| `FINNHUB_API_KEY` | US real-time, аналитика Wall St | Опционально |
| `TINKOFF_INVEST_TOKEN` | РФ real-time котировки | Опционально |

Ollama работает локально, ключи не нужны.

---

## 📱 Telegram команды

| Команда | Описание |
|---------|----------|
| `/signal AAPL` | Полный анализ с торговым планом |
| `/price SBER.ME` | Быстрая котировка |
| `/dashboard` | Свод по watchlist |
| `/collect` | Массовый сбор сигналов |
| `/status` | Статус сбора |
| `/export` | Выгрузить лог в файл |

Бот также имеет интерактивное меню с кнопками для навигации.

---

## 🔌 REST API

```bash
# Health check
curl http://localhost:8000/health

# Быстрая котировка
curl http://localhost:8000/quote/AAPL

# Полный анализ
curl http://localhost:8000/analyze/AAPL?fast=true

# POST с параметрами
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"symbol": "MSFT", "fast_mode": false}'
```

Rate limiting: 30 запросов/мин на IP (настраивается через `API_RATE_LIMIT_PER_MIN`).

---

## 🧠 Система самообучения

### Как работает

```
Сигналы → outcomes.jsonl (реальный PnL) → анализ → корректировка весов
```

1. **Outcome Tracker** (каждый час) — проверяет, достигли ли открытые сигналы целей или стопов
2. **Числовой IC** — ранговая корреляция каждого компонента с реальным PnL
3. **LLM Learning** (каждые 6ч) — Ollama анализирует паттерны win/loss, рекомендует корректировки
4. **Engine** — при каждом анализе применяет обученные веса

### Три уровня (совместимые)

| Уровень | Что делает | Требования |
|---------|-----------|-----------|
| IC (числовой) | Корреляция компонент → PnL | 30+ outcomes |
| Паттерн-анализ | Delta wins vs losses, optimal thresholds | 20+ outcomes |
| LLM (Ollama) | Качественный анализ комбинаций | Ollama + 20+ outcomes |

Если Ollama недоступен — работают только числовые уровни. Система не ломается.

### Ручной запуск

```bash
python -m stock_signal_analyzer.llm_learning --force
```

---

## 📊 Бэктестирование

### v1 — по сохранённым сигналам

```bash
python tools/backtest.py data/signals.jsonl --min-tier A
```

Проверяет зафиксированные торговые планы на реальных свечах.

### v2 — candle replay

```bash
python tools/backtest_v2.py --symbols AAPL MSFT GOOGL --days 180 --export results.json
```

Полная эмуляция: прогон анализа день за днём, вход по Open, slippage, комиссии, trailing stop, equity curve.

Метрики: Sharpe, Sortino, Calmar, max drawdown, profit factor, win rate по tier/symbol.

---

## 📊 Пример вывода

```
=== 2026-05-09 14:30:00 UTC | AAPL — Apple Inc. ===
Инструмент: US equity, blue chip

  ╔══ ТОРГОВЫЙ ПЛАН ══════════════════════
  ║  LONG AAPL @ 198.50
  ║  Стоп: 194.80 (-1.86%)
  ║  Цель 1: 203.20 (+2.37%)  R:R 1.3 — закрыть 50%
  ║  Цель 2: 207.40 (+4.48%)  R:R 2.4 — остаток
  ║  Трейлинг: после +3.0% → безубыток
  ║  Удержание: до 5 дней  |  Позиция: 12%  |  Класс: A
  ╚═══════════════════════════════════════

Итоговый балл: +0.312  (-1…+1)
Согласованность: 0.78  |  ADX14≈26.3  |  Режим: trending
Класс качества: A  |  ATR(14): 1.87%

Компоненты:
  Техника:   +0.380  |  RSI14=62.1, MACD бычий, выше SMA50
  Импульс:   +0.290  |  5д: +1.8%, 20д: +4.2%, ускорение +
  Новости:   +0.210  |  LLM: bullish, катализаторы: earnings beat
  Объём:     +0.250  |  CMF=+0.22, объём +18% vs среднего

Квант-модели: +0.180
  MTF momentum: aligned across timeframes
  Trend strength: strong (score=0.72)
```

---

## ⚙️ Конфигурация

Все настройки через `.env`:

```bash
# Telegram
TELEGRAM_BOT_TOKEN=...

# Данные
FINNHUB_API_KEY=...
POLYGON_API_KEY=...
TINKOFF_INVEST_TOKEN=...

# LLM (Ollama)
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen2.5:1.5b
LLM_SENTIMENT=1

# Автоматизация
COLLECT_INTERVAL_SEC=14400      # автосбор каждые 4ч
NOTIFY_INTERVAL_SEC=3600        # уведомления каждый час
LEARN_INTERVAL_SEC=21600        # обучение каждые 6ч
NOTIFY_MIN_TIER=A               # уведомлять только о классе A

# API
API_RATE_LIMIT_PER_MIN=30

# Пути
SSA_SIGNAL_LOG=/var/lib/stock_signal_analyzer/signals.jsonl
STOCK_SIGNAL_DATA=/var/lib/stock_signal_analyzer
```

---

## 🛠️ Разработка

```bash
# Тесты (82 теста)
pytest tests/ -v

# Форматирование
black stock_signal_analyzer/ && isort stock_signal_analyzer/

# Type checking
mypy stock_signal_analyzer/

# Coverage
pytest tests/ --cov=stock_signal_analyzer
```

### Добавление нового индикатора

1. Добавить функцию в соответствующий модуль (`technical.py`, `momentum.py`, etc.)
2. Добавить тесты в `tests/test_core.py`
3. Интегрировать в `engine.py` (в `_gather_inputs` или `_compute_score`)
4. Outcome tracker автоматически начнёт отслеживать IC нового компонента

---

## 📈 Классификация сигналов

| Класс | Условия | Действие |
|:---:|---------|----------|
| **A** | Высокий score + confidence > 0.6 + нет headwinds | Торговать |
| **B** | Умеренный score или 1 headwind | С осторожностью |
| **C** | Низкий score или противоречия | Наблюдать |

Headwinds: earnings window, index против, низкая ликвидность, macro uncertainty.

---

## 🐳 Docker сервисы

| Сервис | Порт | Описание |
|--------|:----:|----------|
| `bot` | — | Telegram бот |
| `api` | 8000 | REST API (FastAPI) |
| `ollama` | 11434 | LLM для sentiment + обучения |
| `tracker` | — | Outcome tracker (каждый час) |
| `learning` | — | LLM learning (каждые 6ч) |
| `cron` | — | Планировщик для tracker/learning |

---

## 📋 Требования

- Python 3.9+
- RAM: 4 GB минимум (2-3 GB для Ollama + остальное)
- Диск: ~3 GB (модель + зависимости + данные)
- Ubuntu 22.04+ для продакшена

---

## ⚠️ Disclaimer

Этот инструмент предназначен только для информационных целей. Не является финансовой рекомендацией. Торговля на финансовых рынках сопряжена с риском потери капитала.

---

**Версия:** 2.0.0  
**Обновлено:** 2026-05-09
