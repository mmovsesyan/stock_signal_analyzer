#!/usr/bin/env bash
# Деплой stock_signal_analyzer на Ubuntu VPS
# Запуск: bash deploy.sh
set -euo pipefail

AUTO_ENABLE_SERVICE="${AUTO_ENABLE_SERVICE:-y}"
AUTO_START_SERVICE="${AUTO_START_SERVICE:-y}"
AUTO_COLLECT_INTERVAL_SEC="${AUTO_COLLECT_INTERVAL_SEC:-14400}"

# Избегаем venv и systemd User=root при вызове «sudo bash deploy.sh»
if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    exec sudo -u "$SUDO_USER" bash "$0" "$@"
fi

echo "=== Stock Signal Analyzer — Установка на Ubuntu ==="

# 1. Системные зависимости
echo "[1/6] Системные пакеты..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip git rsync

# 2. Директория проекта
APP_DIR="${APP_DIR:-/opt/stock-signal-analyzer}"
echo "[2/6] Директория: $APP_DIR"
sudo mkdir -p "$APP_DIR"
sudo chown "$(whoami):$(whoami)" "$APP_DIR"

# Копируем файлы (или git clone)
if [ -d ".git" ]; then
    if command -v rsync >/dev/null 2>&1; then
        echo "  Git-репо обнаружен, используем rsync..."
        rsync -av --exclude='.venv' --exclude='__pycache__' --exclude='data' --exclude='.env' . "$APP_DIR/"
    else
        echo "  ⚠ rsync не найден, используем cp (медленнее)..."
        cp -r stock_signal_analyzer main.py telegram_bot.py stenv.py requirements*.txt "$APP_DIR/"
        cp -r tools tests "$APP_DIR/" 2>/dev/null || true
    fi
else
    echo "  Копируем файлы..."
    cp -r stock_signal_analyzer main.py telegram_bot.py stenv.py requirements*.txt "$APP_DIR/"
    cp -r tools tests "$APP_DIR/" 2>/dev/null || true
fi

cd "$APP_DIR"

pymaj=$(python3 -c 'import sys; print(sys.version_info[0])')
pymin=$(python3 -c 'import sys; print(sys.version_info[1])')
if (( pymaj < 3 || (pymaj == 3 && pymin < 9) )); then
    echo "Ошибка: нужен Python 3.9+, сейчас: $(python3 -V)"
    exit 1
fi

# 3. Virtual environment
echo "[3/6] Python venv..."
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q

# 4. Зависимости
echo "[4/6] Зависимости (PyPI)..."
.venv/bin/pip install -r requirements.txt -q

echo "[4/6] Зависимости (T-Bank SDK)..."
.venv/bin/pip install -r requirements-tbank.txt -q || echo "  ⚠ T-Bank SDK не установился (не критично для US-тикеров)"

# 5. .env файл
if [ ! -f .env ]; then
    echo "[5/6] Создаём .env (заполните токены!)..."
    cat > .env << ENVEOF
# T-Bank (Tinkoff Invest) API Token — для РФ-акций
TINKOFF_INVEST_TOKEN=${TINKOFF_INVEST_TOKEN:-}

# Telegram Bot Token (от @BotFather)
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}

# Finnhub API Key (бесплатный: https://finnhub.io/)
FINNHUB_API_KEY=${FINNHUB_API_KEY:-}

# Signal log (для сбора данных и бэктеста)
SSA_SIGNAL_LOG=${APP_DIR}/data/signals.jsonl

# Автосбор каждые 4 часа (0 = выкл)
COLLECT_INTERVAL_SEC=${AUTO_COLLECT_INTERVAL_SEC}
ENVEOF
    echo "  .env создан."
else
    echo "[5/6] .env уже существует, пропускаем."
fi

mkdir -p data
chmod 600 .env || true

# 6. systemd сервис
echo "[6/6] Systemd сервис..."
sudo tee /etc/systemd/system/ssa-bot.service > /dev/null << SVCEOF
[Unit]
Description=Stock Signal Analyzer Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python telegram_bot.py
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal

# Безопасность
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$APP_DIR
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SVCEOF

sudo systemctl daemon-reload
if [[ "$AUTO_ENABLE_SERVICE" =~ ^[Yy] ]]; then
    sudo systemctl enable ssa-bot
fi
if [[ "$AUTO_START_SERVICE" =~ ^[Yy] ]]; then
    sudo systemctl restart ssa-bot
fi

echo ""
echo "=== Установка завершена ==="
echo ""
echo "Следующие шаги:"
echo "  1. Проверьте токены:   nano $APP_DIR/.env"
echo "  2. Smoke test:         cd $APP_DIR && .venv/bin/python main.py AAPL"
echo "  3. Статус сервиса:     sudo systemctl status ssa-bot"
echo "  4. Логи:               journalctl -u ssa-bot -f"
echo ""
