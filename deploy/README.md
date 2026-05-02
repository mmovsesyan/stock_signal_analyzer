# 🚀 Deployment Files

Файлы для автоматического деплоя на Ubuntu сервер.

---

## 📁 Содержимое

| Файл | Описание |
|------|----------|
| `deploy.sh` | Скрипт автоматического деплоя |
| `stock-signal-bot.service` | Systemd сервис для Telegram бота |
| `stock-signal-tracker.service` | Systemd сервис для outcome tracker |
| `stock-signal-tracker.timer` | Systemd таймер (запуск каждый час) |

---

## 🚀 Быстрый старт

### Полная автоматическая установка

```bash
./deploy/deploy.sh user@your-server.com --full
```

Это выполнит:
- ✅ Копирование файлов на сервер
- ✅ Установку зависимостей
- ✅ Интерактивную настройку
- ✅ Настройку systemd сервисов
- ✅ Запуск бота

---

## 📖 Использование

### Базовый деплой

```bash
# Только копирование файлов
./deploy/deploy.sh user@server.com
```

### С опциями

```bash
# Установить системные зависимости
./deploy/deploy.sh user@server.com --install-deps

# Настроить systemd сервисы
./deploy/deploy.sh user@server.com --setup-systemd

# Запустить сервисы
./deploy/deploy.sh user@server.com --start-services

# Все вместе
./deploy/deploy.sh user@server.com --full
```

---

## ⚙️ Systemd сервисы

### Установка вручную

Если не используете `deploy.sh`:

```bash
# 1. Скопировать файлы
sudo cp deploy/*.service /etc/systemd/system/
sudo cp deploy/*.timer /etc/systemd/system/

# 2. Заменить плейсхолдеры
sudo sed -i "s|%USER%|$USER|g" /etc/systemd/system/stock-signal-*.service
sudo sed -i "s|%WORKING_DIR%|$(pwd)|g" /etc/systemd/system/stock-signal-*.service

# 3. Включить и запустить
sudo systemctl daemon-reload
sudo systemctl enable stock-signal-bot.service
sudo systemctl enable stock-signal-tracker.timer
sudo systemctl start stock-signal-bot.service
sudo systemctl start stock-signal-tracker.timer
```

### Управление

```bash
# Статус
sudo systemctl status stock-signal-bot
sudo systemctl status stock-signal-tracker.timer

# Логи
sudo journalctl -u stock-signal-bot -f
sudo journalctl -u stock-signal-tracker -f

# Перезапуск
sudo systemctl restart stock-signal-bot

# Остановка
sudo systemctl stop stock-signal-bot stock-signal-tracker.timer
```

---

## 📋 Требования

### На вашей машине (Mac)

- SSH доступ к серверу
- SSH ключ добавлен: `ssh-copy-id user@server.com`

### На сервере (Ubuntu)

- Ubuntu 20.04+ или Debian 10+
- Python 3.9+
- Sudo права (для systemd)
- Минимум 1GB RAM
- Минимум 5GB свободного места

---

## 🔧 Troubleshooting

### SSH подключение не работает

```bash
# Добавить SSH ключ
ssh-copy-id user@server.com

# Проверить подключение
ssh user@server.com
```

### Нет прав для создания директорий

```bash
# На сервере
sudo mkdir -p /opt/stock_signal_analyzer
sudo chown $USER /opt/stock_signal_analyzer
```

### Systemd сервис не запускается

```bash
# Проверить логи
sudo journalctl -u stock-signal-bot -n 50

# Проверить конфигурацию
sudo systemctl cat stock-signal-bot

# Проверить .env файл
cat /opt/stock_signal_analyzer/.env
```

---

## 📚 Дополнительная документация

- `../DEPLOY_GUIDE.md` - Полное руководство по деплою
- `../QUICK_START_MONETIZATION.md` - Руководство по монетизации
- `../docker-compose.yml` - Docker установка

---

**Версия:** 1.2.0  
**Дата:** 2026-05-01
