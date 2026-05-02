# ✅ Готово! Запуск на сервере

**Дата:** 2026-05-02  
**Версия:** 1.4.0

---

## 🎯 Что сделано

1. ✅ **setup.py исправлен** — убрана проверка модулей, работает с venv
2. ✅ **Tinkoff API добавлен** — модуль `tinkoff_api.py` для российских акций
3. ✅ **requirements.txt обновлён** — добавлен `tinkoff-investments`
4. ✅ **install.sh** — полная автоматическая установка (venv + зависимости + systemd)
5. ✅ **Автоматическая настройка** — всё устанавливается одной командой

---

## 🚀 Запуск на сервере (2 команды)

```bash
# 1. Клонировать репозиторий
git clone git@github.com:username/stock_signal_analyzer.git
cd stock_signal_analyzer

# 2. Запустить автоустановку
sudo ./install.sh
```

**Всё!** Скрипт автоматически:
- ✅ Создаст venv
- ✅ Установит все зависимости из requirements.txt
- ✅ Создаст директории для данных
- ✅ Запросит все ключи (Telegram, Tinkoff, Finnhub)
- ✅ Создаст .env файл
- ✅ Настроит systemd сервис
- ✅ Запустит бота в фоновом режиме

---

## 📋 Что спросит install.sh

### 1. Telegram Bot Token
```
📱 Telegram Bot Token
Получить: https://t.me/BotFather
Введите Bot Token (или Enter для пропуска): 
→ Вставь токен: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz
```

### 2. Tinkoff Token
```
🏦 Tinkoff/T-Bank Token
Получить: https://www.tbank.ru/invest/settings/api/
Введите Tinkoff Token (или Enter для пропуска): 
→ Вставь токен: t.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 3. Finnhub API Key (опционально)
```
📊 Finnhub API Key (для US акций)
Получить: https://finnhub.io/register
Введите Finnhub API Key (или Enter для пропуска): 
→ Вставь ключ или Enter для пропуска
```

---

## 🔑 Где взять токены

### Telegram Bot Token
1. Открыть: https://t.me/BotFather
2. Отправить: `/newbot`
3. Следовать инструкциям
4. Скопировать токен: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`

### Tinkoff Token
1. Открыть: https://www.tbank.ru/invest/settings/api/
2. Создать токен с правами: **Только чтение**
3. Скопировать токен: `t.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

### Finnhub API Key (опционально)
1. Открыть: https://finnhub.io/register
2. Зарегистрироваться
3. Скопировать API Key

---

## ✅ После установки

Скрипт автоматически:
- ✅ Создаст `.env` файл со всеми ключами
- ✅ Добавит переменные в `~/.bashrc`
- ✅ Создаст systemd сервис
- ✅ Запустит бота в фоновом режиме

**Проверка:**
```bash
sudo systemctl status stock-signal-bot.service
```

Должно быть: `Active: active (running)`

---

## 🎮 Управление ботом

### Посмотреть логи в реальном времени
```bash
sudo journalctl -u stock-signal-bot.service -f
```

### Перезапустить
```bash
sudo systemctl restart stock-signal-bot.service
```

### Остановить
```bash
sudo systemctl stop stock-signal-bot.service
```

### Отключить автозапуск
```bash
sudo systemctl disable stock-signal-bot.service
```

---

## 🔄 Обновление

```bash
cd /root/stock_signal_analyzer
sudo systemctl stop stock-signal-bot.service
git pull origin main
source venv/bin/activate
pip install -r requirements.txt --upgrade
sudo systemctl start stock-signal-bot.service
```

---

## 🐛 Если что-то не работает

### Бот не запускается
```bash
# Проверь логи
sudo journalctl -u stock-signal-bot.service -e

# Запусти вручную для отладки
cd /root/stock_signal_analyzer
source venv/bin/activate
python telegram_bot.py
```

### Проверить .env файл
```bash
cat .env
```

Должны быть все токены.

### Tinkoff не работает
```bash
# Проверь токен
cat .env | grep TINKOFF

# Проверь модуль
source venv/bin/activate
python3 -c "from stock_signal_analyzer.tinkoff_api import is_tinkoff_available; print(is_tinkoff_available())"
```

---

## 📚 Документация

- **BACKGROUND_RUN_TINKOFF.md** — полная инструкция по фоновому запуску
- **TELEGRAM_AUTOCOLLECT_READY.md** — управление автосбором через меню
- **QUICK_START_MONETIZATION.md** — путь к монетизации

---

## 🎯 Что дальше

После установки бот будет:
- ✅ Работать в фоновом режиме 24/7
- ✅ Автоматически собирать сигналы каждые 4 часа
- ✅ Использовать Tinkoff API для российских акций
- ✅ Отправлять уведомления в Telegram
- ✅ Логировать все сигналы в файл

**Через 1-2 недели:**
- Наберётся 50+ сигналов
- Можно запустить бэктест
- Оценить прибыльность стратегии

**Через месяц:**
- Если метрики хорошие (win rate >60%, profit factor >2.0)
- Можно запускать MVP подписку

---

**Версия:** 1.4.0  
**Дата:** 2026-05-02  
**Автор:** Claude (Kiro)  

🚀 Готово к запуску!


### 1. Путь к файлу сигналов
```
По умолчанию: /var/lib/stock_signal_analyzer/signals.jsonl
→ Нажми Enter
```

### 2. Путь к данным
```
По умолчанию: /var/lib/stock_signal_analyzer
→ Нажми Enter
```

### 3. Частота автосбора
```
1) Каждые 4 часа (рекомендуется)
2) Каждый час
3) Каждые 8 часов
4) Отключить
→ Введи: 1
```

### 4. Telegram Bot Token
```
Настроить Telegram бота? [y/N]: y
Введите Bot Token: 
→ Вставь токен от @BotFather
```

### 5. API ключи
```
Настроить API ключи? [y/N]: y

Finnhub API Key (для US акций):
→ Вставь ключ или Enter для пропуска

Tinkoff/T-Bank Token (для RU акций):
→ Вставь токен или Enter для пропуска
```

---

## 🔑 Где взять токены

### Telegram Bot Token
1. Открыть: https://t.me/BotFather
2. Отправить: `/newbot`
3. Следовать инструкциям
4. Скопировать токен: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`

### Tinkoff Token
1. Открыть: https://www.tbank.ru/invest/settings/api/
2. Создать токен с правами: **Только чтение**
3. Скопировать токен: `t.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

### Finnhub API Key (опционально)
1. Открыть: https://finnhub.io/register
2. Зарегистрироваться
3. Скопировать API Key

---

## ✅ Проверка работы

### 1. Бот запущен
```bash
sudo systemctl status stock-signal-bot.service
```

Должно быть: `Active: active (running)`

### 2. Логи без ошибок
```bash
sudo journalctl -u stock-signal-bot.service -n 20
```

### 3. Telegram отвечает
Отправить в бот: `/start`

Должен ответить с меню.

### 4. Tinkoff работает
Отправить в бот: `/price SBER.ME`

Должен вернуть цену Сбербанка.

---

## 🎮 Управление ботом

### Посмотреть логи в реальном времени
```bash
sudo journalctl -u stock-signal-bot.service -f
```

### Перезапустить
```bash
sudo systemctl restart stock-signal-bot.service
```

### Остановить
```bash
sudo systemctl stop stock-signal-bot.service
```

### Отключить автозапуск
```bash
sudo systemctl disable stock-signal-bot.service
```

---

## 🔄 Обновление

```bash
cd /root/stock_signal_analyzer
sudo systemctl stop stock-signal-bot.service
git pull origin main
source venv/bin/activate
pip install -r requirements.txt --upgrade
sudo systemctl start stock-signal-bot.service
```

---

## 🐛 Если что-то не работает

### Ошибка: externally-managed-environment
```bash
# Это нормально! Используй venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Ошибка: stock_signal_analyzer не установлен
```bash
# Это исправлено в новом setup.py
# Просто запусти:
python3 setup.py
```

### Бот не запускается
```bash
# Проверь логи
sudo journalctl -u stock-signal-bot.service -e

# Запусти вручную для отладки
cd /root/stock_signal_analyzer
source venv/bin/activate
python telegram_bot.py
```

### Tinkoff не работает
```bash
# Проверь токен
cat .env | grep TINKOFF

# Проверь модуль
source venv/bin/activate
python3 -c "from stock_signal_analyzer.tinkoff_api import is_tinkoff_available; print(is_tinkoff_available())"
```

---

## 📚 Документация

- **BACKGROUND_RUN_TINKOFF.md** — полная инструкция по фоновому запуску
- **TELEGRAM_AUTOCOLLECT_READY.md** — управление автосбором через меню
- **QUICK_START_MONETIZATION.md** — путь к монетизации

---

## 🎯 Что дальше

После установки бот будет:
- ✅ Работать в фоновом режиме 24/7
- ✅ Автоматически собирать сигналы каждые 4 часа
- ✅ Использовать Tinkoff API для российских акций
- ✅ Отправлять уведомления в Telegram
- ✅ Логировать все сигналы в файл

**Через 1-2 недели:**
- Наберётся 50+ сигналов
- Можно запустить бэктест
- Оценить прибыльность стратегии

**Через месяц:**
- Если метрики хорошие (win rate >60%, profit factor >2.0)
- Можно запускать MVP подписку

---

**Версия:** 1.4.0  
**Дата:** 2026-05-02  
**Автор:** Claude (Kiro)  

🚀 Готово к запуску!
