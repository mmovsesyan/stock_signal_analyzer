#!/bin/bash
# Полная автоматическая установка Stock Signal Analyzer на сервере

set -e

echo "🚀 Stock Signal Analyzer - Полная автоматическая установка"
echo "==========================================================="
echo ""

# Проверка ОС
if [[ "$OSTYPE" != "linux-gnu"* ]]; then
    echo "❌ Этот скрипт работает только на Linux"
    exit 1
fi

# Проверка прав
if [[ $EUID -ne 0 ]]; then
   echo "❌ Этот скрипт должен запускаться с sudo"
   exit 1
fi

# Проверка Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 не установлен"
    echo "Установите: apt install python3 python3-venv python3-pip"
    exit 1
fi

echo "✓ Python 3 найден: $(python3 --version)"
echo ""

# Определить текущего пользователя (не root)
CURRENT_USER=${SUDO_USER:-$(whoami)}
if [ "$CURRENT_USER" = "root" ]; then
    CURRENT_USER="root"
fi

PROJECT_DIR=$(pwd)
echo "📁 Директория проекта: $PROJECT_DIR"
echo "👤 Пользователь: $CURRENT_USER"
echo ""

# ============================================================================
# 1. СОЗДАТЬ VENV И УСТАНОВИТЬ ЗАВИСИМОСТИ
# ============================================================================

echo "📦 Создание виртуального окружения..."
python3 -m venv venv
chown -R $CURRENT_USER:$CURRENT_USER venv

echo "✓ Активация venv..."
source venv/bin/activate

echo "📥 Установка зависимостей (это может занять 2-3 минуты)..."
pip install --upgrade pip setuptools wheel > /dev/null 2>&1
pip install -r requirements.txt > /dev/null 2>&1

echo "✅ Зависимости установлены!"
echo ""

# ============================================================================
# 2. СОЗДАТЬ ДИРЕКТОРИИ ДЛЯ ДАННЫХ
# ============================================================================

echo "📂 Создание директорий для данных..."
mkdir -p /var/lib/stock_signal_analyzer
mkdir -p /var/log/stock_signal
chown -R $CURRENT_USER:$CURRENT_USER /var/lib/stock_signal_analyzer
chown -R $CURRENT_USER:$CURRENT_USER /var/log/stock_signal
chmod 755 /var/lib/stock_signal_analyzer
chmod 755 /var/log/stock_signal

echo "✓ Директории созданы"
echo ""

# ============================================================================
# 3. ЗАПРОСИТЬ КЛЮЧИ И СОЗДАТЬ .ENV
# ============================================================================

echo "🔑 Настройка ключей и токенов"
echo "=============================="
echo ""

# Telegram Bot Token
echo "📱 Telegram Bot Token"
echo "Получить: https://t.me/BotFather"
read -p "Введите Bot Token (или Enter для пропуска): " TELEGRAM_TOKEN

# Tinkoff Token
echo ""
echo "🏦 Tinkoff/T-Bank Token"
echo "Получить: https://www.tbank.ru/invest/settings/api/"
read -p "Введите Tinkoff Token (или Enter для пропуска): " TINKOFF_TOKEN

# Finnhub API Key
echo ""
echo "📊 Finnhub API Key (для US акций)"
echo "Получить: https://finnhub.io/register"
read -p "Введите Finnhub API Key (или Enter для пропуска): " FINNHUB_KEY

echo ""

# ============================================================================
# 4. СОЗДАТЬ .ENV ФАЙЛ
# ============================================================================

echo "💾 Создание .env файла..."

cat > "$PROJECT_DIR/.env" << EOF
# Stock Signal Analyzer Configuration
# Generated: $(date)

# Telegram
TELEGRAM_BOT_TOKEN=$TELEGRAM_TOKEN

# Tinkoff / T-Bank
TINKOFF_TOKEN=$TINKOFF_TOKEN

# Finnhub (для US акций)
FINNHUB_API_KEY=$FINNHUB_KEY

# Пути
SSA_SIGNAL_LOG=/var/lib/stock_signal_analyzer/signals.jsonl
STOCK_SIGNAL_DATA=/var/lib/stock_signal_analyzer

# Автосбор (каждые 4 часа)
COLLECT_INTERVAL_SEC=14400

# Уведомления (каждый час)
NOTIFY_INTERVAL_SEC=3600
EOF

chown $CURRENT_USER:$CURRENT_USER "$PROJECT_DIR/.env"
chmod 600 "$PROJECT_DIR/.env"

echo "✓ .env файл создан"
echo ""

# ============================================================================
# 5. ДОБАВИТЬ ПЕРЕМЕННЫЕ В ~/.bashrc
# ============================================================================

echo "📝 Добавление переменных в ~/.bashrc..."

BASHRC_FILE="/root/.bashrc"
if [ "$CURRENT_USER" != "root" ]; then
    BASHRC_FILE="/home/$CURRENT_USER/.bashrc"
fi

if ! grep -q "# Stock Signal Analyzer" "$BASHRC_FILE"; then
    cat >> "$BASHRC_FILE" << 'EOF'

# Stock Signal Analyzer
export SSA_SIGNAL_LOG="/var/lib/stock_signal_analyzer/signals.jsonl"
export STOCK_SIGNAL_DATA="/var/lib/stock_signal_analyzer"
export COLLECT_INTERVAL_SEC="14400"
EOF
    echo "✓ Переменные добавлены в ~/.bashrc"
else
    echo "ℹ️  Переменные уже есть в ~/.bashrc"
fi

echo ""

# ============================================================================
# 6. НАСТРОИТЬ SYSTEMD СЕРВИС
# ============================================================================

echo "⚙️  Настройка systemd сервиса..."

# Определить путь к Python в venv
PYTHON_BIN="$PROJECT_DIR/venv/bin/python"

# Создать systemd сервис
cat > /etc/systemd/system/stock-signal-bot.service << EOF
[Unit]
Description=Stock Signal Analyzer Telegram Bot
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$PROJECT_DIR
Environment="PATH=$PROJECT_DIR/venv/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart=$PYTHON_BIN telegram_bot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "✓ Systemd сервис создан"
echo ""

# ============================================================================
# 7. ЗАПУСТИТЬ SYSTEMD СЕРВИС
# ============================================================================

echo "🚀 Запуск systemd сервиса..."

systemctl daemon-reload
systemctl enable stock-signal-bot.service
systemctl start stock-signal-bot.service

sleep 2

# Проверить статус
if systemctl is-active --quiet stock-signal-bot.service; then
    echo "✅ Бот успешно запущен!"
else
    echo "⚠️  Бот не запустился. Проверьте логи:"
    echo "   journalctl -u stock-signal-bot.service -e"
fi

echo ""

# ============================================================================
# 8. ИТОГОВАЯ ИНФОРМАЦИЯ
# ============================================================================

echo "==========================================================="
echo "✅ УСТАНОВКА ЗАВЕРШЕНА!"
echo "==========================================================="
echo ""

echo "📋 Конфигурация:"
echo "  Telegram Token: ${TELEGRAM_TOKEN:0:20}..."
echo "  Tinkoff Token: ${TINKOFF_TOKEN:0:20}..."
echo "  Finnhub Key: ${FINNHUB_KEY:0:20}..."
echo ""

echo "📂 Директории:"
echo "  Проект: $PROJECT_DIR"
echo "  Данные: /var/lib/stock_signal_analyzer"
echo "  Логи: /var/log/stock_signal"
echo ""

echo "🎮 Управление ботом:"
echo "  Статус:     sudo systemctl status stock-signal-bot.service"
echo "  Логи:       sudo journalctl -u stock-signal-bot.service -f"
echo "  Перезапуск: sudo systemctl restart stock-signal-bot.service"
echo "  Остановка:  sudo systemctl stop stock-signal-bot.service"
echo ""

echo "📚 Документация:"
echo "  BACKGROUND_RUN_TINKOFF.md - Полная инструкция"
echo "  READY_TO_RUN.md - Быстрый старт"
echo ""

echo "🎯 Что дальше:"
echo "  1. Бот работает в фоновом режиме 24/7"
echo "  2. Автоматически собирает сигналы каждые 4 часа"
echo "  3. Через 1-2 недели наберется 50+ сигналов"
echo "  4. Запустить бэктест: python tools/backtest.py \$SSA_SIGNAL_LOG"
echo ""

echo "✨ Готово к использованию!"
echo ""

