# 📈 Stock Signal Analyzer

Система многофакторного анализа торговых сигналов для акций с Telegram-ботом. Анализирует технические индикаторы, импульс, новости, объёмы и макроэкономический контекст для генерации торговых планов.

## 🎯 Возможности

- **Многофакторный анализ:**
  - Технический анализ (RSI, MACD, Bollinger Bands, ADX)
  - Анализ импульса и momentum
  - Sentiment анализ новостей (RSS, Google News, Finnhub)
  - Анализ объёмов и давления (CMF, OBV)
  - Внутридневные данные (real-time через WebSocket)
  - Макроэкономический контекст
  - Квантовые модели (MTF momentum, trend strength, volatility regime)

- **Торговые планы:**
  - Точки входа/выхода
  - Стоп-лоссы и тейк-профиты (3 уровня)
  - Risk/Reward соотношения
  - Размер позиции с учётом риск-менеджмента
  - Trailing stop рекомендации

- **Telegram-бот:**
  - Котировки по запросу
  - Полные отчёты по тикерам
  - Дашборды по спискам (РФ голубые фишки / US / дивидендные)
  - Автоматические уведомления о сильных сигналах
  - Интерактивное меню на русском языке
  - Управление автосбором через меню

- **Поддержка рынков:**
  - 🇺🇸 US акции (Yahoo Finance, Finnhub)
  - 🇷🇺 Российские акции (MOEX ISS, Tinkoff Invest API)
  - Real-time данные через WebSocket

- **Монетизация:**
  - Автоматический сбор сигналов
  - Отслеживание результатов (outcome tracker)
  - Бэктестер для оценки прибыльности
  - Фильтрация сигналов (3 пресета)

## 📋 Требования

- Python 3.10+
- Ubuntu 22.04+ или Debian 11+ (для продакшена)
- API ключи:
  - **Telegram Bot Token** (обязательно) — получить у [@BotFather](https://t.me/BotFather)
  - **Tinkoff Token** (для РФ акций) — [tbank.ru/invest/settings/api](https://www.tbank.ru/invest/settings/api/)
  - **Finnhub API Key** (для US акций) — [finnhub.io](https://finnhub.io)

## 🚀 Быстрый старт на сервере

### Автоматическая установка (одна команда)

```bash
git clone git@github.com:username/stock_signal_analyzer.git && \
cd stock_signal_analyzer && \
sudo ./install.sh
```

**Скрипт автоматически:**
- ✅ Создаст venv и установит все зависимости
- ✅ Запросит все ключи (Telegram, Tinkoff, Finnhub)
- ✅ Создаст .env файл
- ✅ Настроит systemd сервис
- ✅ Запустит бота в фоновом режиме

### Проверка работы

```bash
# Статус бота
sudo systemctl status stock-signal-bot.service

# Логи в реальном времени
sudo journalctl -u stock-signal-bot.service -f

# Отправить в Telegram
/start
```

## 📱 Telegram команды

### Аналитика
- `/signal AAPL` — полный анализ тикера с торговым планом
- `/price SBER.ME` — быстрая цена
- `/dashboard` — обзор рынка (топ сигналов)

### Сбор сигналов
- `/collect` — массовый сбор сигналов (30+ тикеров)
- `/status` — статус сбора (сколько сигналов собрано)
- `/export` — выгрузить все сигналы в файл

### Управление
- **⚙️ Настройки** → **🤖 Настройка автосбора**
  - Включить/выключить дефолтные 30 тикеров
  - Добавить свои тикеры для автосбора
  - Просмотреть текущую конфигурацию

## 🎮 Управление ботом

```bash
# Перезапуск
sudo systemctl restart stock-signal-bot.service

# Остановка
sudo systemctl stop stock-signal-bot.service

# Логи
sudo journalctl -u stock-signal-bot.service -n 50
```

## 🔄 Обновление

```bash
cd /root/stock_signal_analyzer
sudo systemctl stop stock-signal-bot.service
git pull origin main
source venv/bin/activate
pip install -r requirements.txt --upgrade
sudo systemctl start stock-signal-bot.service
```

## 📊 Монетизация

### Путь к прибыли

**Неделя 1-2: Сбор данных**
- Бот автоматически собирает сигналы каждые 4 часа
- Цель: 50-100 сигналов

**Неделя 2: Бэктест**
```bash
source venv/bin/activate
python tools/backtest.py /var/lib/stock_signal_analyzer/signals.jsonl --min-tier A
```

**Целевые метрики:**
- Win rate: >60%
- Profit factor: >2.0

**Неделя 3+: Оптимизация и paper trading**

**Месяц 2: MVP подписка**
- Запуск платной подписки ($50-100/месяц)

## 📚 Документация

| Файл | Описание |
|------|---------|
| **QUICKSTART.md** | Быстрый старт (одна команда) |
| **INSTALLATION_COMPLETE.md** | Полное описание установки |
| **READY_TO_RUN.md** | Инструкция по запуску |
| **BACKGROUND_RUN_TINKOFF.md** | Фоновый запуск + Tinkoff API |
| **TELEGRAM_AUTOCOLLECT_READY.md** | Управление автосбором |
| **QUICK_START_MONETIZATION.md** | Путь к монетизации |

## 🛠️ Разработка

### Локальный запуск (macOS/Linux)

```bash
# Создать venv
python3 -m venv venv
source venv/bin/activate

# Установить зависимости
pip install -r requirements.txt

# Создать .env
cp .env.example .env
# Отредактировать .env (добавить токены)

# Запустить бота
python telegram_bot.py
```

## 🏗️ Архитектура

```
stock_signal_analyzer/
├── engine.py              # Движок анализа
├── signal_filter.py       # Фильтрация сигналов
├── outcome_tracker.py     # Отслеживание результатов
├── tinkoff_api.py         # Tinkoff API интеграция
├── user_store.py          # Настройки пользователей
└── ...

telegram_bot.py            # Telegram бот

tools/
├── backtest.py            # Бэктестер
├── monitor_signals.py     # Мониторинг сбора
└── verify_monetization.py # Проверка компонентов

deploy/
├── stock-signal-bot-simple.service  # Systemd сервис
└── ...

install.sh                 # Автоматическая установка
```

## 📄 Лицензия

MIT

## 🤝 Поддержка

- GitHub Issues: [github.com/username/stock_signal_analyzer/issues](https://github.com/username/stock_signal_analyzer/issues)
- Документация: см. файлы `*.md` в корне проекта

---

**Версия:** 1.4.0  
**Дата:** 2026-05-02  
**Статус:** ✅ Готово к использованию

# Создать виртуальное окружение
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# или
venv\Scripts\activate  # Windows

# Установить зависимости
pip install -r requirements.txt

# Опционально: для российского рынка
pip install -r requirements-tbank.txt
```

### 2. Настройка

```bash
# Создать .env файл
cp .env.example .env

# Отредактировать .env и добавить токены
nano .env  # или любой редактор
```

Минимальная конфигурация `.env`:
```bash
# Обязательно для Telegram-бота
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather

# Опционально
FINNHUB_API_KEY=your_finnhub_key
TINKOFF_INVEST_TOKEN=your_tbank_token
```

### 3. Использование

#### CLI - Анализ тикера

```bash
# Простой анализ
python main.py AAPL

# Российская акция
python main.py SBER.ME

# Режим мониторинга (обновление каждые 5 минут)
python main.py AAPL --watch --interval 300

# С real-time данными Finnhub (нужен API ключ)
python main.py AAPL --finnhub-ws --ws-seconds 10
```

#### Telegram-бот

```bash
python telegram_bot.py
```

Команды бота:
- `/start` — главное меню
- `/quote AAPL` — быстрая котировка
- `/signal AAPL` — полный анализ с торговым планом
- `/dash` — свод по списку тикеров
- `/add AAPL` — добавить в watchlist
- `/list` — показать watchlist

## 📊 Пример вывода

```
=== 2026-05-01 18:30:00 UTC | AAPL — Apple Inc. ===
Инструмент: US equity, blue chip

  ╔══ ТОРГОВЫЙ ПЛАН ══════════════════════
  ║  LONG AAPL @ 175.50
  ║  Стоп: 172.30 (-1.82%)
  ║  Цель 1: 179.20 (+2.11%)  R:R 1.2 — закрыть 50%
  ║  Цель 2: 182.80 (+4.16%)  R:R 2.3 — остаток
  ║  Трейлинг: после +3.0% → безубыток
  ║  Удержание: до 5 дней  |  Позиция: 15%  |  Класс: B
  ╚═══════════════════════════════════════

Итоговый балл: +0.248  (-1…+1)  (до макро: +0.261)
Согласованность: 0.72  |  ADX14≈23.5  |  Режим: trending
Класс качества: B  |  ATR(14): 2.15%  |  стоп-ориентир ~1.5×ATR: 3.23%
Контекст: [weekly uptrend] Недельный тренд совпадает с дневным сигналом

Компоненты:
  Техника:   +0.320  |  RSI14=58.2 (нейтрально), MACD бычий кроссовер
  Импульс:   +0.280  |  5д: +2.1%, 20д: +5.8%, ускорение положительное
  Новости:   +0.150  |  Sentiment: слабо позитивный (3 новости)
  Объём:     +0.220  |  CMF=+0.18, объём выше среднего на 15%
```

## 🏗️ Архитектура

```
stock_signal_analyzer/
├── main.py                    # CLI entry point
├── telegram_bot.py            # Telegram bot entry point
├── stock_signal_analyzer/     # Основной пакет
│   ├── engine.py              # Главный движок анализа
│   ├── technical.py           # Технический анализ
│   ├── momentum.py            # Анализ импульса
│   ├── sentiment.py           # Sentiment анализ новостей
│   ├── volume_pressure.py     # Анализ объёмов
│   ├── intraday.py            # Real-time данные
│   ├── trade_plan.py          # Генерация торговых планов
│   ├── risk_manager.py        # Управление рисками
│   ├── quant_models.py        # Квантовые модели
│   ├── market_data.py         # Загрузка котировок
│   ├── news_feeds.py          # Парсинг новостей
│   ├── finnhub_live.py        # Finnhub API
│   ├── moex_iss.py            # MOEX ISS API
│   ├── tbank_invest.py        # T-Bank Invest API
│   └── ...
├── tests/                     # Unit-тесты (82 теста)
├── docs/                      # Документация
├── requirements.txt           # Python зависимости
└── requirements-tbank.txt     # T-Bank SDK (опционально)
```

## 🧪 Тестирование

```bash
# Установить pytest
pip install pytest

# Запустить все тесты
pytest tests/ -v

# Запустить с coverage
pytest tests/ --cov=stock_signal_analyzer
```

Все 82 теста должны проходить успешно.

## 📦 Установка на VPS (Ubuntu)

Для автоматической установки на чистом Ubuntu сервере:

```bash
git clone <repository-url>
cd stock_signal_analyzer

# Интерактивная установка
bash install.sh

# Автоматическая установка
TELEGRAM_BOT_TOKEN=your_token bash install.sh --auto
```

Скрипт установит:
- Python 3 и зависимости
- Виртуальное окружение
- Systemd сервис для автозапуска
- Настроит логирование

Подробнее: [docs/VPS_UBUNTU.md](docs/VPS_UBUNTU.md)

## 🔧 Конфигурация

Все настройки через переменные окружения в `.env`:

```bash
# Telegram-бот
TELEGRAM_BOT_TOKEN=...

# API ключи
FINNHUB_API_KEY=...
TINKOFF_INVEST_TOKEN=...

# Пути
STOCK_SIGNAL_DATA=/var/lib/stock_signal_analyzer
SSA_SIGNAL_LOG=/var/lib/stock_signal_analyzer/signals.jsonl

# Уведомления
NOTIFY_INTERVAL_SEC=3600        # Интервал проверки сильных сигналов
OUTSIDE_SCAN_MAX=120            # Сколько тикеров сканировать
NOTIFY_MIN_TIER=A               # Минимальный класс для уведомлений

# Автосбор сигналов
COLLECT_INTERVAL_SEC=14400      # Каждые 4 часа (0 = выключен)

# T-Bank дополнительно
SSA_TBANK_VOLUME=1              # Запрашивать свечи для VWAP/POC
```

## 📈 Классификация сигналов

Система присваивает каждому сигналу класс качества:

- **A** — Сильный сигнал: высокая согласованность, хорошие условия, нет противоречий
- **B** — Средний сигнал: умеренная уверенность, есть небольшие риски
- **C** — Слабый сигнал: низкая согласованность или неблагоприятные условия

Факторы, влияющие на класс:
- Согласованность компонентов (техника, импульс, новости, объём)
- ADX (сила тренда)
- Макроэкономический фон
- Окно отчётности
- Направление индекса
- Ликвидность

## 🛠️ Разработка

### Структура кода

- Каждый модуль отвечает за свою область анализа
- `engine.py` объединяет все компоненты
- Все функции покрыты unit-тестами
- Используется type hints (Python 3.9+)

### Добавление нового индикатора

1. Добавить функцию в соответствующий модуль (например, `technical.py`)
2. Добавить тесты в `tests/test_core.py`
3. Интегрировать в `engine.py`
4. Обновить документацию

### Code style

```bash
# Форматирование
black stock_signal_analyzer/

# Линтинг
flake8 stock_signal_analyzer/

# Type checking
mypy stock_signal_analyzer/
```

## 📝 Лицензия

[Укажите лицензию проекта]

## 🤝 Вклад

Pull requests приветствуются! Для крупных изменений сначала откройте issue для обсуждения.

## 📞 Поддержка

- Issues: [GitHub Issues](https://github.com/your-repo/issues)
- Документация: [docs/](docs/)
- Telegram: [ваш канал]

## ⚠️ Disclaimer

Этот инструмент предназначен только для информационных целей. Не является финансовой рекомендацией. Торговля на финансовых рынках сопряжена с риском потери капитала. Всегда проводите собственный анализ и консультируйтесь с финансовым советником.

---

**Версия:** 1.0.0  
**Последнее обновление:** 2026-05-01
