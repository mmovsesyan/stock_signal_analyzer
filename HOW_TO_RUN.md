# 🚀 КАК ЗАПУСТИТЬ БОТА

**Дата:** 2026-05-02

---

## ✅ Вариант 1: Автоматическая установка + фоновый запуск (рекомендуется)

```bash
# На сервере
cd /root/stock_signal_analyzer

# Запустить установку
sudo ./install.sh
```

**Скрипт сделает:**
1. ✅ Создаст venv
2. ✅ Установит все зависимости
3. ✅ Запросит ключи (Telegram, Tinkoff, Finnhub)
4. ✅ Создаст .env файл
5. ✅ Настроит systemd сервис
6. ✅ Запустит бота в фоновом режиме

**Проверка:**
```bash
sudo systemctl status stock-signal-bot.service
```

---

## 🎮 Вариант 2: Ручной запуск (для отладки)

```bash
# На сервере
cd /root/stock_signal_analyzer

# Запустить скрипт
./start.sh
```

**Или вручную:**
```bash
# Активировать venv
source venv/bin/activate

# Запустить бота
python telegram_bot.py
```

---

## 📋 Что нужно перед запуском

### 1. Создать .env файл

```bash
cat > /root/stock_signal_analyzer/.env << EOF
# Telegram
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz

# Tinkoff (опционально)
TINKOFF_TOKEN=t.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Finnhub (опционально)
FINNHUB_API_KEY=ck1234567890abcdef

# Пути
SSA_SIGNAL_LOG=/var/lib/stock_signal_analyzer/signals.jsonl
STOCK_SIGNAL_DATA=/var/lib/stock_signal_analyzer

# Автосбор
COLLECT_INTERVAL_SEC=14400
NOTIFY_INTERVAL_SEC=3600
EOF
```

### 2. Создать директории

```bash
sudo mkdir -p /var/lib/stock_signal_analyzer
sudo mkdir -p /var/log/stock_signal
sudo chown -R $USER /var/lib/stock_signal_analyzer
sudo chown -R $USER /var/log/stock_signal
```

### 3. Создать venv и установить зависимости

```bash
cd /root/stock_signal_analyzer
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## 🎯 Управление ботом

### Если запущен через systemd

```bash
# Статус
sudo systemctl status stock-signal-bot.service

# Логи в реальном времени
sudo journalctl -u stock-signal-bot.service -f

# Последние 50 строк
sudo journalctl -u stock-signal-bot.service -n 50

# Перезапуск
sudo systemctl restart stock-signal-bot.service

# Остановка
sudo systemctl stop stock-signal-bot.service

# Запуск
sudo systemctl start stock-signal-bot.service
```

### Если запущен вручную

```bash
# Нажать Ctrl+C для остановки
```

---

## 🐛 Отладка

### Ошибка: `python: not found`

**Причина:** Бот запущен без venv

**Решение:**
```bash
cd /root/stock_signal_analyzer
source venv/bin/activate
python telegram_bot.py
```

### Ошибка: `TELEGRAM_BOT_TOKEN not set`

**Причина:** .env файл не создан или не загружен

**Решение:**
```bash
# Проверить .env
cat /root/stock_signal_analyzer/.env

# Если пусто, создать:
sudo ./install.sh
```

### Бот не отвечает в Telegram

**Проверить:**
```bash
# Логи
sudo journalctl -u stock-signal-bot.service -e

# Или запустить вручную
source venv/bin/activate
python telegram_bot.py
```

---

## ✨ Итог

**Самый простой способ:**

```bash
cd /root/stock_signal_analyzer
sudo ./install.sh
```

Скрипт сделает всё остальное автоматически!

---

**Версия:** 1.4.0  
**Дата:** 2026-05-02
