#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  Полная установка Stock Signal Analyzer на Ubuntu VPS
#  Включает: Python, venv, зависимости, Ollama + LLM, systemd сервисы
#
#  Использование:
#    git clone <repo> && cd stock_signal_analyzer
#    sudo ./scripts/install_ubuntu.sh
#
#  Или неинтерактивно:
#    TELEGRAM_BOT_TOKEN=xxx POLYGON_API_KEY=yyy sudo ./scripts/install_ubuntu.sh --auto
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${BLUE}[ℹ]${NC} $1"; }
ok()    { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[⚠]${NC} $1"; }
fail()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/venv"
ENV_FILE="$PROJECT_DIR/.env"
DATA_DIR="/var/lib/stock_signal_analyzer"
LOG_DIR="/var/log/stock_signal"
SERVICE_NAME="stock-signal-bot"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:1.5b}"
AUTO_MODE="${1:-}"

# Определить пользователя
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    RUN_USER="$SUDO_USER"
else
    RUN_USER="$(whoami)"
fi

echo -e "${BOLD}${GREEN}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   Stock Signal Analyzer — Полная установка (Ubuntu)         ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo "  Проект:    $PROJECT_DIR"
echo "  Пользователь: $RUN_USER"
echo "  Модель LLM: $OLLAMA_MODEL"
echo ""

# ── Проверка ОС ──────────────────────────────────────────────────────────────

if [[ "$(uname -s)" != "Linux" ]]; then
    fail "Этот скрипт для Linux (Ubuntu/Debian). Текущая ОС: $(uname -s)"
fi

if [ "$(id -u)" -ne 0 ]; then
    fail "Запустите с sudo: sudo ./scripts/install_ubuntu.sh"
fi

# ═══════════════════════════════════════════════════════════════════════
#  1. СИСТЕМНЫЕ ПАКЕТЫ
# ═══════════════════════════════════════════════════════════════════════

info "1/7 Системные пакеты..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl wget
ok "Системные пакеты установлены"

# Проверка Python версии
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
PYMAJ=$(python3 -c 'import sys; print(sys.version_info[0])')
PYMIN=$(python3 -c 'import sys; print(sys.version_info[1])')
if (( PYMAJ < 3 || (PYMAJ == 3 && PYMIN < 9) )); then
    fail "Нужен Python 3.9+, сейчас: $PYVER"
fi
ok "Python $PYVER"

# ═══════════════════════════════════════════════════════════════════════
#  2. ВИРТУАЛЬНОЕ ОКРУЖЕНИЕ И ЗАВИСИМОСТИ
# ═══════════════════════════════════════════════════════════════════════

info "2/7 Виртуальное окружение и зависимости..."
if [ ! -f "$VENV_DIR/bin/python" ]; then
    python3 -m venv "$VENV_DIR"
fi
chown -R "$RUN_USER":"$RUN_USER" "$VENV_DIR" 2>/dev/null || true

"$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel -q
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt" -q
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements-api.txt" -q 2>/dev/null || true
ok "Python зависимости установлены"

# T-Bank SDK (опционально)
if [ -f "$PROJECT_DIR/requirements-tbank.txt" ]; then
    "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements-tbank.txt" -q 2>/dev/null || \
        warn "T-Bank SDK не установился (не критично)"
fi

# ═══════════════════════════════════════════════════════════════════════
#  3. ДИРЕКТОРИИ
# ═══════════════════════════════════════════════════════════════════════

info "3/7 Директории..."
mkdir -p "$DATA_DIR" "$LOG_DIR"
chown -R "$RUN_USER":"$RUN_USER" "$DATA_DIR" "$LOG_DIR"
chmod 755 "$DATA_DIR" "$LOG_DIR"
ok "Директории: $DATA_DIR, $LOG_DIR"

# ═══════════════════════════════════════════════════════════════════════
#  4. OLLAMA + LLM
# ═══════════════════════════════════════════════════════════════════════

info "4/7 Ollama + LLM модель ($OLLAMA_MODEL)..."

if command -v ollama &>/dev/null; then
    ok "Ollama уже установлен"
else
    info "Устанавливаю Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    command -v ollama &>/dev/null || fail "Не удалось установить Ollama"
    ok "Ollama установлен"
fi

# Запуск сервиса
systemctl enable ollama 2>/dev/null || true
systemctl start ollama 2>/dev/null || true
sleep 3

# Ждём доступности
for i in $(seq 1 15); do
    if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
    warn "Ollama не отвечает. LLM sentiment будет отключён."
    warn "Попробуйте позже: systemctl restart ollama && ollama pull $OLLAMA_MODEL"
else
    ok "Ollama сервис запущен"
    # Загрузка модели
    MODEL_BASE="${OLLAMA_MODEL%%:*}"
    if ollama list 2>/dev/null | grep -q "$MODEL_BASE"; then
        ok "Модель $OLLAMA_MODEL уже загружена"
    else
        info "Загружаю модель $OLLAMA_MODEL (~1.5 GB)..."
        ollama pull "$OLLAMA_MODEL" && ok "Модель загружена" || warn "Не удалось загрузить модель"
    fi
fi

# ═══════════════════════════════════════════════════════════════════════
#  5. КОНФИГУРАЦИЯ (.env)
# ═══════════════════════════════════════════════════════════════════════

info "5/7 Конфигурация..."

if [ -f "$ENV_FILE" ]; then
    ok ".env уже существует"
else
    # Интерактивный или автоматический режим
    TG_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
    FH_KEY="${FINNHUB_API_KEY:-}"
    PG_KEY="${POLYGON_API_KEY:-}"
    ADMIN_ID="${ADMIN_CHAT_ID:-}"
    ADMIN_UID="${ADMIN_USER_ID:-}"
    API_KEY="${API_SECRET_KEY:-}"

    if [ "$AUTO_MODE" != "--auto" ]; then
        # Telegram Token — обязательно (бот без токена не работает)
        while [ -z "$TG_TOKEN" ]; do
            read -r -p "  Telegram Bot Token (обязательно, от @BotFather): " TG_TOKEN
            if [ -z "$TG_TOKEN" ]; then
                echo -e "${RED}  ⚠ Токен бота обязателен! Получите у @BotFather.${NC}"
            fi
        done
        if [ -z "$PG_KEY" ]; then
            read -r -p "  Polygon API Key (Enter = пропустить): " PG_KEY
        fi
        if [ -z "$FH_KEY" ]; then
            read -r -p "  Finnhub API Key (Enter = пропустить): " FH_KEY
        fi
        # Admin ID — обязательно, повторяет пока не введёшь
        while [ -z "$ADMIN_ID" ]; do
            read -r -p "  Admin Chat ID (обязательно, узнать через @userinfobot): " ADMIN_ID
            if [ -z "$ADMIN_ID" ]; then
                echo -e "${RED}  ⚠ Admin Chat ID обязателен для безопасности бота!${NC}"
            fi
        done
        # Admin User ID — обязательно, повторяет пока не введёшь
        while [ -z "$ADMIN_UID" ]; do
            read -r -p "  Admin User ID (обязательно, ваш Telegram ID): " ADMIN_UID
            if [ -z "$ADMIN_UID" ]; then
                echo -e "${RED}  ⚠ Admin User ID обязателен для доступа к боту!${NC}"
            fi
        done
        if [ -z "$API_KEY" ]; then
            read -r -p "  API Secret Key (Enter = пропустить, API будет закрыт): " API_KEY
        fi
    else
        # Автоматический режим — проверяем обязательные поля, падаем если нет
        if [ -z "$TG_TOKEN" ]; then
            fail "TELEGRAM_BOT_TOKEN обязателен в автоматическом режиме."
        fi
        if [ -z "$ADMIN_ID" ]; then
            fail "ADMIN_CHAT_ID обязателен в автоматическом режиме."
        fi
        if [ -z "$ADMIN_UID" ]; then
            fail "ADMIN_USER_ID обязателен в автоматическом режиме."
        fi
    fi

    cat > "$ENV_FILE" << ENVEOF
# Stock Signal Analyzer — конфигурация
# Сгенерировано: $(date '+%Y-%m-%d %H:%M:%S')

# ── Telegram ──────────────────────────────────
TELEGRAM_BOT_TOKEN=${TG_TOKEN}

# ── Admin (обязательно) ───────────────────────
ADMIN_CHAT_ID=${ADMIN_ID}
ADMIN_USER_ID=${ADMIN_UID}

# ── API ключи ─────────────────────────────────
FINNHUB_API_KEY=${FH_KEY}
POLYGON_API_KEY=${PG_KEY}

# ── API security ──────────────────────────────
API_SECRET_KEY=${API_KEY}

# ── LLM Sentiment (Ollama) ────────────────────
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=${OLLAMA_MODEL}
LLM_SENTIMENT=1

# ── Пути ──────────────────────────────────────
SSA_SIGNAL_LOG=${DATA_DIR}/signals.jsonl
STOCK_SIGNAL_DATA=${DATA_DIR}

# ── Автосбор и уведомления ────────────────────
COLLECT_INTERVAL_SEC=14400
NOTIFY_INTERVAL_SEC=3600
NOTIFY_MIN_TIER=A

# ── API layer ─────────────────────────────────
API_RATE_LIMIT_PER_MIN=30
ENVEOF

    chmod 600 "$ENV_FILE"
    chown "$RUN_USER":"$RUN_USER" "$ENV_FILE"
    ok ".env создан"
fi

# ═══════════════════════════════════════════════════════════════════════
#  6. SYSTEMD СЕРВИСЫ
# ═══════════════════════════════════════════════════════════════════════

info "6/7 Systemd сервисы..."

# Telegram бот
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << SVCEOF
[Unit]
Description=Stock Signal Analyzer Telegram Bot
After=network-online.target ollama.service
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
Environment="PYTHONUNBUFFERED=1"
ExecStart=$VENV_DIR/bin/python telegram_bot.py
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$PROJECT_DIR $DATA_DIR $LOG_DIR
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SVCEOF

# API сервис
cat > "/etc/systemd/system/stock-signal-api.service" << APIEOF
[Unit]
Description=Stock Signal Analyzer REST API
After=network-online.target ollama.service
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
Environment="PYTHONUNBUFFERED=1"
ExecStart=$VENV_DIR/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$PROJECT_DIR $DATA_DIR $LOG_DIR
PrivateTmp=true

[Install]
WantedBy=multi-user.target
APIEOF

# Outcome tracker timer
cat > "/etc/systemd/system/stock-signal-tracker.service" << TRKEOF
[Unit]
Description=Stock Signal Analyzer — Outcome Tracker
After=network-online.target

[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
Environment="PYTHONUNBUFFERED=1"
ExecStart=$VENV_DIR/bin/python -m stock_signal_analyzer.outcome_tracker
StandardOutput=journal
StandardError=journal
TRKEOF

cat > "/etc/systemd/system/stock-signal-tracker.timer" << TMREOF
[Unit]
Description=Stock Signal Analyzer — Outcome Tracker Timer

[Timer]
OnBootSec=5min
OnUnitActiveSec=1h

[Install]
WantedBy=timers.target
TMREOF

# LLM Learning сервис (раз в 6 часов)
cat > "/etc/systemd/system/stock-signal-learning.service" << LRNEOF
[Unit]
Description=Stock Signal Analyzer — LLM Learning
After=network-online.target ollama.service

[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
Environment="PYTHONUNBUFFERED=1"
ExecStart=$VENV_DIR/bin/python -m stock_signal_analyzer.llm_learning
StandardOutput=journal
StandardError=journal
LRNEOF

cat > "/etc/systemd/system/stock-signal-learning.timer" << LTMEOF
[Unit]
Description=Stock Signal Analyzer — LLM Learning Timer

[Timer]
OnBootSec=10min
OnUnitActiveSec=6h

[Install]
WantedBy=timers.target
LTMEOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" stock-signal-api stock-signal-tracker.timer stock-signal-learning.timer 2>/dev/null
ok "Systemd сервисы установлены"

# ═══════════════════════════════════════════════════════════════════════
#  7. ЗАПУСК
# ═══════════════════════════════════════════════════════════════════════

info "7/7 Запуск сервисов..."

systemctl start "$SERVICE_NAME" 2>/dev/null || true
systemctl start stock-signal-api 2>/dev/null || true
systemctl start stock-signal-tracker.timer 2>/dev/null || true
systemctl start stock-signal-learning.timer 2>/dev/null || true
sleep 2

# Статус
echo ""
echo -e "${BOLD}Статус сервисов:${NC}"
for svc in "$SERVICE_NAME" stock-signal-api ollama; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        echo -e "  ${GREEN}●${NC} $svc: активен"
    else
        echo -e "  ${YELLOW}○${NC} $svc: не запущен"
    fi
done

# RAM
echo ""
info "Использование RAM:"
free -h | head -2

# ═══════════════════════════════════════════════════════════════════════
#  ИТОГ
# ═══════════════════════════════════════════════════════════════════════

echo ""
echo -e "${BOLD}${GREEN}═══ Установка завершена! ═══${NC}"
echo ""
echo "  Сервисы:"
echo "    Telegram бот:  systemctl status $SERVICE_NAME"
echo "    REST API:      http://localhost:8000 (systemctl status stock-signal-api)"
echo "    Ollama LLM:    http://localhost:11434 (systemctl status ollama)"
echo "    Tracker:       systemctl status stock-signal-tracker.timer"
echo ""
echo "  Логи:"
echo "    journalctl -u $SERVICE_NAME -f"
echo "    journalctl -u stock-signal-api -f"
echo ""
echo "  Тест API:"
echo "    curl http://localhost:8000/health"
echo "    curl http://localhost:8000/analyze/AAPL?fast=true"
echo ""
echo "  Тест Telegram:"
echo "    Отправьте /start боту"
echo ""
echo "  Управление:"
echo "    sudo systemctl restart $SERVICE_NAME"
echo "    sudo systemctl restart stock-signal-api"
echo "    sudo systemctl restart ollama"
echo ""
