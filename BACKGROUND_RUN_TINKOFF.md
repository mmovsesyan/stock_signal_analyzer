# 🚀 Запуск бота в фоновом режиме + Tinkoff API

**Дата:** 2026-05-02  
**Версия:** 1.4.0

---

## ✅ Что исправлено

1. **setup.py** — убрана проверка модулей (работает с venv)
2. **requirements.txt** — добавлен `tinkoff-investments` SDK
3. **tinkoff_api.py** — новый модуль для работы с T-Bank API
4. **systemd сервис** — для фонового запуска

---

## 🚀 Быстрая установка на сервере

### Шаг 1: Клонировать и настроить

```bash
# Подключиться к серверу
ssh root@your-server.com

# Клонировать репозиторий
cd /root
git clone git@github.com:username/stock_signal_analyzer.git
cd stock_signal_analyzer

# Создать venv
python3 -m venv venv

# Активировать
source venv/bin/activate

# Установить зависимости
pip install -r requirements.txt
```

### Шаг 2: Запустить интерактивную установку

```bash
# Запустить setup.py (теперь без ошибок)
python3 setup.py
```

**Ответы на вопросы:**
```
[1/6] Где хранить файл сигналов?
  → Enter (использовать по умолчанию)

[2/6] Где хранить данные?
  → Enter

[3/6] Как часто собирать сигналы?
  → 1 (каждые 4 часа)

[4/6] Telegram Bot Token?
  → y
  → Вставить токен от @BotFather

[5/6] API ключи?
  → y
  → Finnhub API Key: ваш_ключ
  → Tinkoff Token: ваш_токен_тинькофф

[6/6] Создание директорий
  → Автоматически
```

### Шаг 3: Настроить systemd для фонового запуска

```bash
# Скопировать systemd сервис
sudo cp deploy/stock-signal-bot-simple.service /etc/systemd/system/stock-signal-bot.service

# Перезагрузить systemd
sudo systemctl daemon-reload

# Включить автозапуск
sudo systemctl enable stock-signal-bot.service

# Запустить бота
sudo systemctl start stock-signal-bot.service

# Проверить статус
sudo systemctl status stock-signal-bot.service
```

---

## 🔧 Настройка Tinkoff API

### Получить токен:

1. Открыть: https://www.tbank.ru/invest/settings/api/
2. Создать токен с правами: **Только чтение**
3. Скопировать токен

### Добавить в .env:

```bash
nano .env
```

Добавить строку:
```env
TINKOFF_TOKEN=your_tinkoff_token_here
```

Или:
```env
TBANK_TOKEN=your_tinkoff_token_here
```

### Проверить работу:

```bash
source venv/bin/activate
python3 -c "from stock_signal_analyzer.tinkoff_api import is_tinkoff_available; print('Tinkoff:', is_tinkoff_available())"
```

Должно вывести: `Tinkoff: True`

---

## 📊 Использование Tinkoff API

### В коде:

```python
from stock_signal_analyzer.tinkoff_api import (
    fetch_tinkoff_price,
    fetch_tinkoff_candles,
    get_tinkoff_portfolio,
)

# Получить цену
price_data = fetch_tinkoff_price("SBER")
print(price_data)
# {'price': 285.5, 'currency': 'RUB', 'volume': 1234567, ...}

# Получить свечи (30 дней)
candles = fetch_tinkoff_candles("GAZP", days=30)

# Получить портфель
portfolio = get_tinkoff_portfolio()
```

### В Telegram боте:

Бот автоматически будет использовать Tinkoff API для российских акций (*.ME тикеры).

---

## 🎯 Управление фоновым ботом

### Проверить статус:

```bash
sudo systemctl status stock-signal-bot.service
```

### Посмотреть логи:

```bash
# Последние 50 строк
sudo journalctl -u stock-signal-bot.service -n 50

# В реальном времени
sudo journalctl -u stock-signal-bot.service -f
```

### Перезапустить:

```bash
sudo systemctl restart stock-signal-bot.service
```

### Остановить:

```bash
sudo systemctl stop stock-signal-bot.service
```

### Отключить автозапуск:

```bash
sudo systemctl disable stock-signal-bot.service
```

---

## 🔄 Обновление кода

```bash
# Подключиться к серверу
ssh root@your-server.com
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

### Бот не запускается:

```bash
# Проверить логи
sudo journalctl -u stock-signal-bot.service -e

# Запустить вручную для отладки
cd /root/stock_signal_analyzer
source venv/bin/activate
python telegram_bot.py
```

### Tinkoff API не работает:

```bash
# Проверить токен
cat .env | grep TINKOFF

# Проверить модуль
source venv/bin/activate
python3 -c "import tinkoff.invest; print('OK')"

# Проверить доступность
python3 -c "from stock_signal_analyzer.tinkoff_api import is_tinkoff_available; print(is_tinkoff_available())"
```

### Проверить переменные окружения:

```bash
# Применить .env
source ~/.bashrc

# Проверить
echo $TELEGRAM_BOT_TOKEN
echo $TINKOFF_TOKEN
echo $SSA_SIGNAL_LOG
```

---

## 📋 Полный .env файл

```env
# Telegram
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz

# Tinkoff / T-Bank
TINKOFF_TOKEN=t.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# или
TBANK_TOKEN=t.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Finnhub (для US акций)
FINNHUB_API_KEY=ck1234567890abcdef
FINNUB_TOKEN=ck1234567890abcdef

# Пути
SSA_SIGNAL_LOG=/var/lib/stock_signal_analyzer/signals.jsonl
STOCK_SIGNAL_DATA=/var/lib/stock_signal_analyzer

# Автосбор
COLLECT_INTERVAL_SEC=14400  # Каждые 4 часа
NOTIFY_INTERVAL_SEC=3600    # Каждый час
```

---

## ✅ Проверка работы

### 1. Бот запущен:

```bash
sudo systemctl status stock-signal-bot.service
# ● stock-signal-bot.service - Stock Signal Analyzer Telegram Bot
#    Loaded: loaded
#    Active: active (running)
```

### 2. Telegram отвечает:

Отправить в бот: `/start`

Должен ответить с меню.

### 3. Tinkoff работает:

Отправить в бот: `/price SBER.ME`

Должен вернуть цену Сбербанка.

### 4. Автосбор работает:

```bash
# Проверить файл сигналов
tail -f /var/lib/stock_signal_analyzer/signals.jsonl
```

Через 4 часа должны появиться новые сигналы.

---

## 🎯 Итог

Теперь бот:
- ✅ Работает в фоновом режиме (systemd)
- ✅ Автоматически запускается при загрузке сервера
- ✅ Перезапускается при падении
- ✅ Поддерживает Tinkoff API для российских акций
- ✅ Логи доступны через journalctl
- ✅ Управляется через systemctl

---

**Версия:** 1.4.0  
**Дата:** 2026-05-02  
**Автор:** Claude (Kiro)
