# 🚀 Быстрый старт на сервере

## Одна команда для полной установки

```bash
git clone git@github.com:username/stock_signal_analyzer.git && cd stock_signal_analyzer && sudo ./install.sh
```

---

## Что произойдёт

1. ✅ Создаст venv
2. ✅ Установит все зависимости (Python пакеты)
3. ✅ Создаст директории для данных
4. ✅ Запросит ключи:
   - Telegram Bot Token
   - Tinkoff Token
   - Finnhub API Key (опционально)
5. ✅ Создаст .env файл
6. ✅ Настроит systemd сервис
7. ✅ Запустит бота в фоновом режиме

---

## Проверка

```bash
# Статус бота
sudo systemctl status stock-signal-bot.service

# Логи в реальном времени
sudo journalctl -u stock-signal-bot.service -f

# Отправить в Telegram
/start
```

---

## Управление

```bash
# Перезапуск
sudo systemctl restart stock-signal-bot.service

# Остановка
sudo systemctl stop stock-signal-bot.service

# Логи
sudo journalctl -u stock-signal-bot.service -n 50
```

---

## Обновление

```bash
cd /root/stock_signal_analyzer
sudo systemctl stop stock-signal-bot.service
git pull
source venv/bin/activate
pip install -r requirements.txt --upgrade
sudo systemctl start stock-signal-bot.service
```

---

**Полная документация:** READY_TO_RUN.md
