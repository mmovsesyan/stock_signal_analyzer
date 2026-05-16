# Быстрый старт

## Docker (рекомендуется)

```bash
git clone git@github.com:username/stock_signal_analyzer.git
cd stock_signal_analyzer
cp .env.example .env
# Отредактируйте .env — минимум TELEGRAM_BOT_TOKEN и ADMIN_CHAT_ID
./scripts/deploy.sh install   # → выбрать Docker
```

Сервисы поднимутся автоматически: API (8000), бот, worker, beat, postgres, redis.

## systemd (VPS без Docker)

```bash
git clone git@github.com:username/stock_signal_analyzer.git
cd stock_signal_analyzer
./scripts/deploy.sh install   # → выбрать systemd
```

## Проверка

```bash
# Health API
curl http://localhost:8000/health

# Анализ акции
curl http://localhost:8000/analyze/AAPL

# Анализ российской акции (авто-дописывание .ME)
curl http://localhost:8000/analyze/SBER

# Статус Docker
docker compose ps

# Логи бота
docker compose logs -f bot
```

## Управление

```bash
# Перезапуск
./scripts/deploy.sh restart

# Логи
./scripts/deploy.sh logs

# Остановка
./scripts/deploy.sh stop

# Тесты
./scripts/deploy.sh tests
```

## Обновление

```bash
cd /path/to/stock_signal_analyzer
./scripts/deploy.sh update
```

Или через GitHub Actions (auto-deploy):

```bash
git push origin main
# Сервер обновится автоматически через 30-60 секунд
```

## Авто-детекция рынка

Бот автоматически определяет российские тикеры и дописывает `.ME`:

| Что ввели пользователь | Что анализируется |
|------------------------|-------------------|
| `SBER` | `SBER.ME` |
| `GAZP` | `GAZP.ME` |
| `AAPL` | `AAPL` (без изменений) |
| `MSFT` | `MSFT` (без изменений) |

Работает во всех командах: `/signal`, `/price`, `/dashboard`, webhook, API.

## GitHub Actions — авто-деплой

1. **Сгенерировать SSH-ключ** (на сервере или локально):
   ```bash
   ssh-keygen -t ed25519 -f /tmp/gh_deploy_key -N ""
   cat /tmp/gh_deploy_key.pub
   ```

2. **Добавить публичный ключ на сервер**:
   ```bash
   ssh root@213.176.76.35 "mkdir -p ~/.ssh && echo '$(cat /tmp/gh_deploy_key.pub)' >> ~/.ssh/authorized_keys"
   ```

3. **Добавить секреты в GitHub** (Settings → Secrets and variables → Actions):
   - `SERVER_HOST` — IP сервера (например `213.176.76.35`)
   - `SERVER_USER` — `root`
   - `SERVER_SSH_KEY` — содержимое приватного ключа (`cat /tmp/gh_deploy_key`)

4. **Проверить** — при следующем `git push origin main` сервер обновится автоматически.

## Безопасность .env

`.env` уже в `.dockerignore` — токены не попадают в Docker-образ. Если раньше `.env` был в образе:

```bash
# Очистить старые образы с утечкой токенов
docker system prune -a
# Пересобрать
./scripts/deploy.sh restart
```

## Зависимости

| Компонент | Обязательно | Описание |
|-----------|:-----------:|----------|
| PostgreSQL | да | Основная БД (поднимается в Docker) |
| Redis | да | Cache + rate limiter + Celery (поднимается в Docker) |
| Telegram Bot Token | да | `@BotFather` |
| ADMIN_CHAT_ID | да | Telegram ID админа для уведомлений |
| Polygon API Key | опц. | US котировки и новости |
| Finnhub API Key | опц. | US real-time и макро |
| T-Bank Token | опц. | MOEX котировки |

---

**Полная документация:** [README.md](README.md)
