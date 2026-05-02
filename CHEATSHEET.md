# 📝 ПАМЯТКА ДЛЯ ЗАПУСКА НА СЕРВЕРЕ

**Дата:** 2026-05-02

---

## 🚀 БЫСТРЫЙ СТАРТ (3 команды)

```bash
# 1. Клонировать
git clone git@github.com:username/stock_signal_analyzer.git
cd stock_signal_analyzer

# 2. Установить
sudo ./install.sh

# 3. Проверить
sudo systemctl status stock-signal-bot.service
```

---

## ❌ ЕСЛИ ОШИБКА: `python: not found`

**НЕ ПРАВИЛЬНО:**
```bash
python telegram_bot.py  # ❌ Не работает без venv
```

**ПРАВИЛЬНО:**
```bash
# Вариант 1: Через venv
source venv/bin/activate
python telegram_bot.py

# Вариант 2: Через скрипт
./start.sh

# Вариант 3: Через systemd (лучше всего)
sudo systemctl start stock-signal-bot.service
```

---

## 📋 ЧТО НУЖНО ДЛЯ РАБОТЫ

1. **Python 3.10+** ✅
2. **venv** (создаётся автоматически) ✅
3. **Зависимости** (устанавливаются автоматически) ✅
4. **.env файл** с ключами:
   - TELEGRAM_BOT_TOKEN (обязательно)
   - TINKOFF_TOKEN (опционально)
   - FINNHUB_API_KEY (опционально)

---

## 🎮 УПРАВЛЕНИЕ

```bash
# Статус
sudo systemctl status stock-signal-bot.service

# Логи
sudo journalctl -u stock-signal-bot.service -f

# Перезапуск
sudo systemctl restart stock-signal-bot.service

# Остановка
sudo systemctl stop stock-signal-bot.service
```

---

## 🔑 ГДЕ ВЗЯТЬ КЛЮЧИ

1. **Telegram Bot Token**
   - https://t.me/BotFather
   - Команда: `/newbot`

2. **Tinkoff Token**
   - https://www.tbank.ru/invest/settings/api/
   - Права: "Только чтение"

3. **Finnhub API Key**
   - https://finnhub.io/register
   - Бесплатный план

---

## 📂 СТРУКТУРА

```
/root/stock_signal_analyzer/
├── venv/                    # Виртуальное окружение
├── .env                     # Ключи (создаётся install.sh)
├── telegram_bot.py          # Основной бот
├── install.sh               # Автоустановка
├── start.sh                 # Ручной запуск
└── ...

/var/lib/stock_signal_analyzer/
└── signals.jsonl            # Логи сигналов

/var/log/stock_signal/
└── (логи systemd)
```

---

## ✅ ПРОВЕРКА РАБОТЫ

### 1. Бот запущен
```bash
sudo systemctl status stock-signal-bot.service
# Active: active (running) ✅
```

### 2. Telegram отвечает
Отправить: `/start`
Должен ответить с меню ✅

### 3. Логи без ошибок
```bash
sudo journalctl -u stock-signal-bot.service -n 20
# Нет ошибок ✅
```

---

## 🐛 ЧАСТЫЕ ОШИБКИ

### `python: not found`
→ Запускай через venv: `source venv/bin/activate`

### `TELEGRAM_BOT_TOKEN not set`
→ Создай .env: `sudo ./install.sh`

### `externally-managed-environment`
→ Используй venv: `python3 -m venv venv`

### Бот не отвечает
→ Проверь логи: `sudo journalctl -u stock-signal-bot.service -e`

---

## 📚 ДОКУМЕНТАЦИЯ

- **HOW_TO_RUN.md** — как запустить (подробно)
- **QUICKSTART.md** — быстрый старт
- **INSTALLATION_COMPLETE.md** — полное описание
- **FINAL_REPORT.md** — финальный отчёт

---

**Версия:** 1.4.0  
**Дата:** 2026-05-02  

🚀 **ГОТОВО К ЗАПУСКУ!**
