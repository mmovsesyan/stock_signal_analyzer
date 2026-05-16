# 🚀 Быстрый старт

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

## Зависимости

| Компонент | Обязательно | Описание |
|-----------|:-----------:|----------|
| PostgreSQL | да | Основная БД (поднимается в Docker) |
| Redis | да | Cache + rate limiter + Celery (поднимается в Docker) |
| Telegram Bot Token | да | `@BotFather` |
| Polygon API Key | опц. | US котировки и новости |
| Finnhub API Key | опц. | US real-time и макро |
| T-Bank Token | опц. | MOEX котировки |

---

**Полная документация:** [README.md](README.md)
