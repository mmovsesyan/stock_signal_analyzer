# ✅ ПОЛНАЯ УСТАНОВКА - ГОТОВО!

**Дата:** 2026-05-02  
**Версия:** 1.4.0  
**Статус:** ✅ Готово к запуску на сервере

---

## 🎯 Что реализовано

### 1. Автоматическая установка (install.sh)
- ✅ Создание venv
- ✅ Установка всех зависимостей из requirements.txt
- ✅ Создание директорий для данных
- ✅ Запрос всех ключей (Telegram, Tinkoff, Finnhub)
- ✅ Создание .env файла
- ✅ Добавление переменных в ~/.bashrc
- ✅ Настройка systemd сервиса
- ✅ Автозапуск при загрузке сервера
- ✅ Запуск бота в фоновом режиме

### 2. Tinkoff API интеграция
- ✅ Модуль `tinkoff_api.py` для работы с T-Bank API
- ✅ Получение цен российских акций
- ✅ Получение исторических свечей
- ✅ Получение портфеля пользователя
- ✅ Добавлен в requirements.txt

### 3. Исправления
- ✅ setup.py — убрана проверка модулей (работает с venv)
- ✅ requirements.txt — добавлен tinkoff-investments
- ✅ systemd сервис — для фонового запуска

---

## 🚀 Запуск на сервере (одна команда)

```bash
git clone git@github.com:username/stock_signal_analyzer.git && \
cd stock_signal_analyzer && \
sudo ./install.sh
```

**Всё!** Скрипт сделает всё остальное автоматически.

---

## 📋 Что спросит install.sh

```
🔑 Настройка ключей и токенов
==============================

📱 Telegram Bot Token
Получить: https://t.me/BotFather
Введите Bot Token (или Enter для пропуска): 
→ Вставь: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz

🏦 Tinkoff/T-Bank Token
Получить: https://www.tbank.ru/invest/settings/api/
Введите Tinkoff Token (или Enter для пропуска): 
→ Вставь: t.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

📊 Finnhub API Key (для US акций)
Получить: https://finnhub.io/register
Введите Finnhub API Key (или Enter для пропуска): 
→ Вставь или Enter для пропуска
```

---

## ✅ После установки

Скрипт автоматически:

1. **Создаст .env файл** со всеми ключами:
```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TINKOFF_TOKEN=t.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FINNHUB_API_KEY=ck1234567890abcdef
SSA_SIGNAL_LOG=/var/lib/stock_signal_analyzer/signals.jsonl
STOCK_SIGNAL_DATA=/var/lib/stock_signal_analyzer
COLLECT_INTERVAL_SEC=14400
NOTIFY_INTERVAL_SEC=3600
```

2. **Добавит переменные в ~/.bashrc**

3. **Создаст systemd сервис** `/etc/systemd/system/stock-signal-bot.service`

4. **Запустит бота** в фоновом режиме

---

## 🎮 Управление ботом

### Проверить статус
```bash
sudo systemctl status stock-signal-bot.service
```

### Посмотреть логи в реальном времени
```bash
sudo journalctl -u stock-signal-bot.service -f
```

### Посмотреть последние 50 строк логов
```bash
sudo journalctl -u stock-signal-bot.service -n 50
```

### Перезапустить бота
```bash
sudo systemctl restart stock-signal-bot.service
```

### Остановить бота
```bash
sudo systemctl stop stock-signal-bot.service
```

### Отключить автозапуск
```bash
sudo systemctl disable stock-signal-bot.service
```

---

## 📂 Структура после установки

```
/root/stock_signal_analyzer/
├── venv/                          # Виртуальное окружение
├── .env                           # Конфигурация (создана автоматически)
├── telegram_bot.py                # Основной бот
├── requirements.txt               # Зависимости
├── install.sh                     # Скрипт установки
├── setup.py                       # Интерактивная настройка
├── stock_signal_analyzer/
│   ├── engine.py                  # Движок анализа
│   ├── tinkoff_api.py             # Tinkoff API (новый)
│   ├── signal_filter.py           # Фильтрация сигналов
│   └── ...
├── tools/
│   ├── backtest.py                # Бэктестер
│   ├── monitor_signals.py         # Мониторинг
│   └── verify_monetization.py     # Проверка компонентов
└── deploy/
    ├── stock-signal-bot-simple.service
    └── ...

/var/lib/stock_signal_analyzer/
├── signals.jsonl                  # Логи сигналов
└── outcomes.jsonl                 # Результаты

/var/log/stock_signal/
└── (логи systemd)
```

---

## 🔄 Обновление кода

```bash
cd /root/stock_signal_analyzer

# Остановить бота
sudo systemctl stop stock-signal-bot.service

# Обновить код
git pull origin main

# Обновить зависимости
source venv/bin/activate
pip install -r requirements.txt --upgrade

# Запустить бота
sudo systemctl start stock-signal-bot.service

# Проверить
sudo systemctl status stock-signal-bot.service
```

---

## 🐛 Отладка

### Бот не запускается
```bash
# Проверить логи
sudo journalctl -u stock-signal-bot.service -e

# Запустить вручную
cd /root/stock_signal_analyzer
source venv/bin/activate
python telegram_bot.py
```

### Проверить .env файл
```bash
cat /root/stock_signal_analyzer/.env
```

### Проверить Tinkoff
```bash
source /root/stock_signal_analyzer/venv/bin/activate
python3 -c "from stock_signal_analyzer.tinkoff_api import is_tinkoff_available; print(is_tinkoff_available())"
```

### Проверить Telegram
Отправить в бот: `/start`

---

## 📊 Что делает бот

### Автоматически (каждые 4 часа)
- ✅ Анализирует 30+ акций (РФ + US + дивидендные)
- ✅ Генерирует торговые сигналы
- ✅ Логирует сигналы в `/var/lib/stock_signal_analyzer/signals.jsonl`
- ✅ Отслеживает результаты (прибыль/убыток)

### Через Telegram
- ✅ `/start` — главное меню
- ✅ `/signal AAPL` — анализ тикера
- ✅ `/price SBER.ME` — цена акции
- ✅ `/collect` — ручной сбор сигналов
- ✅ `/status` — статус сбора
- ✅ `/export` — выгрузить сигналы

### Управление через меню
- ✅ ⚙️ Настройки → 🤖 Настройка автосбора
- ✅ Включить/выключить дефолтные тикеры
- ✅ Добавить свои тикеры
- ✅ Просмотреть конфигурацию

---

## 🎯 Путь к монетизации

### Неделя 1-2: Сбор данных
```bash
# Бот автоматически собирает сигналы
# Через 1-2 недели наберется 50+ сигналов
```

### Неделя 2: Первый бэктест
```bash
source venv/bin/activate
python tools/backtest.py /var/lib/stock_signal_analyzer/signals.jsonl --min-tier A
```

**Целевые метрики:**
- Win rate: >60%
- Profit factor: >2.0

### Неделя 3+: Оптимизация
- Фильтрация сигналов
- Тестирование разных стратегий
- Paper trading

### Месяц 2: MVP подписка
- Если метрики хорошие
- Запуск платной подписки ($50-100/месяц)

---

## 📚 Документация

| Файл | Описание |
|------|---------|
| **QUICKSTART.md** | Быстрый старт (одна команда) |
| **READY_TO_RUN.md** | Полная инструкция по запуску |
| **BACKGROUND_RUN_TINKOFF.md** | Фоновый запуск + Tinkoff API |
| **TELEGRAM_AUTOCOLLECT_READY.md** | Управление автосбором |
| **QUICK_START_MONETIZATION.md** | Путь к монетизации |
| **MONETIZATION_READY.md** | Статус компонентов |

---

## ✨ Итог

**Всё готово к запуску!**

Одна команда на сервере:
```bash
git clone git@github.com:username/stock_signal_analyzer.git && cd stock_signal_analyzer && sudo ./install.sh
```

И бот будет:
- ✅ Работать 24/7 в фоновом режиме
- ✅ Автоматически собирать сигналы
- ✅ Использовать Tinkoff API для РФ акций
- ✅ Отправлять уведомления в Telegram
- ✅ Логировать всё в файлы
- ✅ Отслеживать результаты

**Готово к монетизации!** 🚀

---

**Версия:** 1.4.0  
**Дата:** 2026-05-02  
**Автор:** Claude (Kiro)  
**Статус:** ✅ Полностью готово
