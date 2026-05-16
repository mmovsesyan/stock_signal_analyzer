# Памятка для запуска на сервере

**Дата:** 2026-05-16

---

## Быстрый старт (3 команды)

```bash
# 1. Клонировать
git clone git@github.com:username/stock_signal_analyzer.git
cd stock_signal_analyzer

# 2. Настроить .env
cp .env.example .env
nano .env   # TELEGRAM_BOT_TOKEN, ADMIN_CHAT_ID — обязательно

# 3. Запустить
./scripts/deploy.sh install   # → выбрать Docker
```

---

## GitHub Actions — авто-деплой

**Настройка (один раз):**

```bash
# 1. Сгенерировать SSH-ключ
ssh-keygen -t ed25519 -f /tmp/gh_deploy_key -N ""

# 2. Добавить на сервер
ssh root@213.176.76.35 "mkdir -p ~/.ssh && echo '$(cat /tmp/gh_deploy_key.pub)' >> ~/.ssh/authorized_keys"

# 3. Добавить секреты в GitHub (Settings → Secrets → Actions):
#    SERVER_HOST=213.176.76.35
#    SERVER_USER=root
#    SERVER_SSH_KEY=<содержимое /tmp/gh_deploy_key>
```

**После настройки:** `git push origin main` → сервер обновляется автоматически.

---

## Управление Docker

```bash
# Статус всех сервисов
docker compose ps

# Логи бота
docker compose logs -f bot

# Логи API
docker compose logs -f api

# Перезапуск
./scripts/deploy.sh restart

# Полная пересборка
docker compose build --no-cache
docker compose up -d
```

---

## Что нужно для работы

1. **Docker + Docker Compose** ✅ (проверить: `docker compose version`)
2. **.env файл** с ключами:
   - `TELEGRAM_BOT_TOKEN` (обязательно)
   - `ADMIN_CHAT_ID` (обязательно — уведомления админу)
   - `ADMIN_CONTACT_INFO` (опц. — контакт для новых пользователей)
   - `POLYGON_API_KEY` (опц.)
   - `FINNHUB_API_KEY` (опц.)
   - `TINKOFF_INVEST_TOKEN` (опц.)

---

## Где взять ключи

1. **Telegram Bot Token**
   - https://t.me/BotFather
   - Команда: `/newbot`

2. **Telegram Admin ID**
   - https://t.me/userinfobot
   - Отправьте `/start` — получите числовой ID

3. **Polygon API Key**
   - https://massive.com/dashboard/signup
   - Free tier: 5 req/min

4. **Finnhub API Key**
   - https://finnhub.io/register
   - Бесплатный план

5. **T-Bank Token**
   - https://www.tbank.ru/invest/settings/api/
   - Права: "Только чтение"

---

## Структура на сервере

```
/root/stock_signal_analyzer/
├── docker-compose.yml       # Определение сервисов
├── .env                     # Ключи (НЕ в git!)
├── .dockerignore            # .env здесь — не попадает в образ
├── telegram_bot.py          # Telegram бот
├── api/main.py              # REST API
├── stock_signal_analyzer/   # Основной пакет
│   ├── engine.py            # Пайплайн анализа
│   ├── circuit_breaker.py   # Защита API
│   ├── admin_alerts.py      # Уведомления админу
│   ├── scheduler.py         # Фоновые задачи
│   └── tasks.py             # Celery задачи
├── .github/workflows/
│   ├── ci.yml               # Тесты, ruff, mypy
│   └── deploy.yml           # Авто-деплой на сервер
└── scripts/
    └── deploy.sh            # Интерактивный деплой

/var/lib/stock_signal_analyzer/
└── signals.jsonl            # Лог сигналов (volume)
```

---

## Проверка работы

### 1. API здоров
```bash
curl http://localhost:8000/health
# {"status":"ok"} ✅
```

### 2. Telegram отвечает
Отправьте `/start` — должен ответить с меню ✅

### 3. Анализ работает
```bash
curl http://localhost:8000/analyze/AAPL
# JSON с score, tier, trade_plan ✅
```

### 4. Российские акции (авто .ME)
```bash
curl http://localhost:8000/analyze/SBER
# Должен вернуть анализ SBER.ME ✅
```

### 5. Логи без ошибок
```bash
docker compose logs -f bot | grep -i error
# Нет критических ошибок ✅
```

---

## Частые ошибки

### `TELEGRAM_BOT_TOKEN not set`
→ Создай `.env`: `cp .env.example .env` и заполни

### `ADMIN_CHAT_ID not set`
→ Добавь `ADMIN_CHAT_ID=123456789` в `.env`, пересобери: `./scripts/deploy.sh restart`

### `.env в Docker-образе` (старый токен)
→ `.env` в `.dockerignore`, но старый образ может кэшировать:
```bash
docker system prune -a
./scripts/deploy.sh restart
```

### Бот не отвечает
```bash
docker compose logs -f bot | tail -20
```

### Circuit breaker OPEN спам
→ Нормально при массовом сканировании. Порог=15, восстановление=180с. Если спам частый — проверьте rate limiting (`_YF_MIN_DELAY=0.25`).

---

## Обновление токена Telegram

```bash
# 1. Обновить .env
nano .env
# TELEGRAM_BOT_TOKEN=новый_токен

# 2. Убедиться что .env в .dockerignore
grep .env .dockerignore

# 3. Пересобрать
./scripts/deploy.sh restart

# 4. Проверить в контейнере
docker compose exec bot env | grep TELEGRAM
```

---

## Документация

- **README.md** — полная документация
- **QUICKSTART.md** — быстрый старт
- **CHANGELOG.md** — история версий

---

**Версия:** 2.5.0
**Дата:** 2026-05-16
