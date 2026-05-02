# 🚀 Руководство по деплою на сервер

Полное руководство по установке Stock Signal Analyzer на Ubuntu сервер.

---

## 📋 Содержание

1. [Быстрый старт](#быстрый-старт)
2. [Методы установки](#методы-установки)
3. [Интерактивная установка](#интерактивная-установка)
4. [Автоматический деплой](#автоматический-деплой)
5. [Docker установка](#docker-установка)
6. [Systemd сервисы](#systemd-сервисы)
7. [Мониторинг и управление](#мониторинг-и-управление)
8. [Troubleshooting](#troubleshooting)

---

## 🚀 Быстрый старт

### Вариант 1: Автоматический деплой (рекомендуется)

```bash
# На вашем Mac
cd /Users/mhermovsisyan/Documents/GitHub/stock_signal_analyzer
chmod +x deploy/deploy.sh

# Полная установка на сервер
./deploy/deploy.sh user@your-server.com --full
```

### Вариант 2: Docker (самый простой)

```bash
# На сервере
git clone <repo> && cd stock_signal_analyzer
cp env.example .env
# Отредактировать .env (добавить TELEGRAM_BOT_TOKEN)
docker-compose up -d
```

### Вариант 3: Ручная установка

```bash
# На сервере
git clone <repo> && cd stock_signal_analyzer
pip install -r requirements.txt
python setup.py
```

---

## 📦 Методы установки

### Сравнение методов

| Метод | Сложность | Время | Автозапуск | Изоляция |
|-------|-----------|-------|------------|----------|
| **Автоматический деплой** | ⭐ Легко | 5 мин | ✅ Да | ❌ Нет |
| **Docker** | ⭐⭐ Средне | 3 мин | ✅ Да | ✅ Да |
| **Ручная установка** | ⭐⭐⭐ Сложно | 10 мин | ⚠️ Вручную | ❌ Нет |

---

## 🎯 Интерактивная установка

### Что это?

Скрипт `setup.py` автоматически:
- Определяет ОС (работает только на Ubuntu/Linux)
- Задает вопросы о конфигурации
- Создает директории
- Сохраняет настройки в `.env` и `~/.bashrc`
- Проверяет установку

### Использование

```bash
# На Ubuntu сервере
python setup.py
```

### Пример интерактивного меню

```
╔════════════════════════════════════════════════════════════════╗
║   Stock Signal Analyzer - Интерактивная установка             ║
╚════════════════════════════════════════════════════════════════╝

[1/6] Где хранить файл сигналов?
  По умолчанию: /var/lib/stock_signal_analyzer/signals.jsonl
  Ваш выбор [Enter = по умолчанию]: _

[2/6] Где хранить данные?
  По умолчанию: /var/lib/stock_signal_analyzer
  Ваш выбор [Enter = по умолчанию]: _

[3/6] Как часто собирать сигналы?
  1) Каждые 4 часа (рекомендуется, ~6 сигналов/день)
  2) Каждый час (агрессивно, ~24 сигнала/день)
  3) Каждые 8 часов (консервативно, ~3 сигнала/день)
  4) Отключить автосбор (собирать вручную)
  Ваш выбор [1-4]: 1

[4/6] Telegram Bot Token (опционально)
  Если есть токен от @BotFather, введите его.
  Настроить Telegram бота? [y/N]: y
  Введите Bot Token: 123456:ABC-DEF...

[5/6] API ключи (опционально)
  Настроить API ключи? [y/N]: n

[6/6] Создание директорий
  ✓ Создана директория: /var/lib/stock_signal_analyzer

✅ Установка завершена!
```

### На macOS

Если запустить на Mac, покажет:

```
╔════════════════════════════════════════════════════════════════╗
║ Stock Signal Analyzer                                          ║
║                                                                ║
║ Обнаружена macOS.                                              ║
║                                                                ║
║ Интерактивная установка предназначена только для Ubuntu сервера.║
║ На macOS все уже установлено и готово к использованию.        ║
╚════════════════════════════════════════════════════════════════╝
```

---

## 🤖 Автоматический деплой

### Скрипт `deploy/deploy.sh`

Автоматически:
- Проверяет SSH подключение
- Копирует проект на сервер
- Устанавливает зависимости
- Запускает интерактивную установку
- Настраивает systemd сервисы
- Запускает бота

### Использование

```bash
# Базовый деплой (только копирование файлов)
./deploy/deploy.sh user@server.com

# Установить системные зависимости
./deploy/deploy.sh user@server.com --install-deps

# Настроить systemd сервисы
./deploy/deploy.sh user@server.com --setup-systemd

# Запустить сервисы после установки
./deploy/deploy.sh user@server.com --start-services

# Полная установка (все опции)
./deploy/deploy.sh user@server.com --full
```

### Требования

1. **SSH доступ** к серверу:
```bash
# Добавить SSH ключ
ssh-copy-id user@server.com

# Проверить подключение
ssh user@server.com
```

2. **Sudo права** на сервере (для systemd)

### Что происходит

1. Проверяет SSH подключение
2. Создает архив проекта (без venv, .git, logs)
3. Копирует на сервер в `/opt/stock_signal_analyzer`
4. Устанавливает Python зависимости
5. Запускает `python setup.py` (интерактивное меню)
6. Настраивает systemd сервисы (если `--setup-systemd`)
7. Запускает сервисы (если `--start-services`)

---

## 🐳 Docker установка

### Преимущества

- ✅ Изолированная среда
- ✅ Легко обновлять
- ✅ Автоматический перезапуск
- ✅ Управление логами
- ✅ Не нужно устанавливать Python

### Быстрый старт

```bash
# 1. Клонировать репозиторий
git clone <repo>
cd stock_signal_analyzer

# 2. Создать .env файл
cp env.example .env
nano .env  # Добавить TELEGRAM_BOT_TOKEN

# 3. Запустить
docker-compose up -d
```

### Структура

**Сервисы:**
- `bot` - Telegram бот (работает 24/7)
- `tracker` - Outcome tracker (запускается по расписанию)
- `cron` - Планировщик для tracker (каждый час)

### Управление

```bash
# Запустить все сервисы
docker-compose up -d

# Остановить все сервисы
docker-compose down

# Посмотреть логи бота
docker-compose logs -f bot

# Посмотреть логи tracker
docker-compose logs tracker

# Перезапустить бота
docker-compose restart bot

# Проверить статус
docker-compose ps

# Обновить код
git pull
docker-compose build
docker-compose up -d
```

### Мониторинг сигналов

```bash
# Войти в контейнер
docker-compose exec bot bash

# Запустить мониторинг
python tools/monitor_signals.py

# Запустить бэктест
python tools/backtest.py /data/signals/signals.jsonl --min-tier A
```

### Данные

Все данные хранятся в `./data/`:
- `./data/signals/signals.jsonl` - Сигналы
- `./data/outcomes.jsonl` - Результаты

Логи в `./logs/`:
- `./logs/bot.log` - Логи бота
- `./logs/tracker.log` - Логи tracker

---

## ⚙️ Systemd сервисы

### Что это?

Systemd автоматически:
- Запускает бота при загрузке сервера
- Перезапускает при падении
- Запускает outcome tracker каждый час
- Управляет логами

### Файлы

- `deploy/stock-signal-bot.service` - Telegram бот
- `deploy/stock-signal-tracker.service` - Outcome tracker
- `deploy/stock-signal-tracker.timer` - Таймер для tracker

### Установка вручную

```bash
# 1. Скопировать service файлы
sudo cp deploy/*.service /etc/systemd/system/
sudo cp deploy/*.timer /etc/systemd/system/

# 2. Заменить плейсхолдеры
sudo sed -i "s|%USER%|$USER|g" /etc/systemd/system/stock-signal-*.service
sudo sed -i "s|%WORKING_DIR%|$(pwd)|g" /etc/systemd/system/stock-signal-*.service

# 3. Перезагрузить systemd
sudo systemctl daemon-reload

# 4. Включить автозапуск
sudo systemctl enable stock-signal-bot.service
sudo systemctl enable stock-signal-tracker.timer

# 5. Запустить
sudo systemctl start stock-signal-bot.service
sudo systemctl start stock-signal-tracker.timer
```

### Управление

```bash
# Проверить статус бота
sudo systemctl status stock-signal-bot

# Проверить статус tracker timer
sudo systemctl status stock-signal-tracker.timer

# Посмотреть логи бота
sudo journalctl -u stock-signal-bot -f

# Посмотреть логи tracker
sudo journalctl -u stock-signal-tracker -f

# Перезапустить бота
sudo systemctl restart stock-signal-bot

# Остановить все
sudo systemctl stop stock-signal-bot stock-signal-tracker.timer

# Отключить автозапуск
sudo systemctl disable stock-signal-bot stock-signal-tracker.timer
```

---

## 📊 Мониторинг и управление

### Проверка статуса

```bash
# Сколько сигналов собрано
python tools/monitor_signals.py

# Статус сервисов (systemd)
sudo systemctl status stock-signal-bot
sudo systemctl status stock-signal-tracker.timer

# Статус контейнеров (Docker)
docker-compose ps
```

### Логи

```bash
# Systemd
sudo journalctl -u stock-signal-bot -f
sudo journalctl -u stock-signal-tracker -f

# Docker
docker-compose logs -f bot
docker-compose logs -f tracker

# Файлы (если настроено)
tail -f logs/bot.log
tail -f logs/tracker.log
```

### Бэктест

```bash
# На сервере
cd /opt/stock_signal_analyzer
source venv/bin/activate
python tools/backtest.py $SSA_SIGNAL_LOG --min-tier A

# В Docker
docker-compose exec bot python tools/backtest.py /data/signals/signals.jsonl --min-tier A
```

### Обновление

```bash
# Systemd
cd /opt/stock_signal_analyzer
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart stock-signal-bot

# Docker
cd /path/to/project
git pull
docker-compose build
docker-compose up -d
```

---

## 🔧 Troubleshooting

### Бот не запускается

**Проверить:**
```bash
# Systemd
sudo systemctl status stock-signal-bot
sudo journalctl -u stock-signal-bot -n 50

# Docker
docker-compose logs bot
```

**Частые причины:**
- ❌ Неверный TELEGRAM_BOT_TOKEN
- ❌ Нет прав на директорию `/var/lib/stock_signal_analyzer`
- ❌ Не установлены зависимости

**Решение:**
```bash
# Проверить .env
cat .env | grep TELEGRAM_BOT_TOKEN

# Проверить права
ls -la /var/lib/stock_signal_analyzer

# Переустановить зависимости
pip install -r requirements.txt
```

### Сигналы не собираются

**Проверить:**
```bash
# Переменная окружения
echo $SSA_SIGNAL_LOG

# Файл существует
ls -la $SSA_SIGNAL_LOG

# Права на запись
touch $SSA_SIGNAL_LOG
```

**Решение:**
```bash
# Создать директорию
sudo mkdir -p /var/lib/stock_signal_analyzer
sudo chown $USER /var/lib/stock_signal_analyzer

# Установить переменную
export SSA_SIGNAL_LOG="/var/lib/stock_signal_analyzer/signals.jsonl"
```

### Outcome tracker не работает

**Проверить:**
```bash
# Systemd timer
sudo systemctl status stock-signal-tracker.timer
sudo systemctl list-timers | grep stock-signal

# Запустить вручную
python -m stock_signal_analyzer.outcome_tracker
```

### Docker контейнер падает

**Проверить:**
```bash
# Логи
docker-compose logs bot

# Перезапустить
docker-compose restart bot

# Пересобрать
docker-compose build --no-cache
docker-compose up -d
```

---

## 📚 Дополнительные ресурсы

- `QUICK_START_MONETIZATION.md` - Руководство по монетизации
- `MONETIZATION_READY.md` - Статус компонентов
- `README.md` - Общая документация

---

## ✅ Чек-лист установки

### Перед установкой
- [ ] Ubuntu сервер доступен
- [ ] SSH ключ добавлен
- [ ] Есть Telegram Bot Token
- [ ] Есть sudo права (для systemd)

### После установки
- [ ] Бот запущен и работает
- [ ] Tracker настроен (каждый час)
- [ ] Сигналы логируются в файл
- [ ] Логи доступны
- [ ] Мониторинг работает

### Через 1-2 недели
- [ ] Собрано 50+ сигналов
- [ ] Запущен бэктест
- [ ] Win rate >60%
- [ ] Profit factor >2.0

---

**Версия:** 1.2.0  
**Дата:** 2026-05-01  
**Автор:** Claude (Kiro)
