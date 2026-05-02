#!/bin/bash
#
# Скрипт деплоя Stock Signal Analyzer на Ubuntu сервер
#
# Использование:
#   ./deploy.sh [user@]hostname [options]
#
# Примеры:
#   ./deploy.sh root@example.com
#   ./deploy.sh user@192.168.1.100 --install-deps
#   ./deploy.sh server.com --setup-systemd
#

set -e  # Остановиться при ошибке

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Функции для вывода
info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Проверка аргументов
if [ $# -lt 1 ]; then
    error "Не указан хост для деплоя"
    echo ""
    echo "Использование: $0 [user@]hostname [options]"
    echo ""
    echo "Опции:"
    echo "  --install-deps    Установить системные зависимости"
    echo "  --setup-systemd   Настроить systemd сервисы"
    echo "  --start-services  Запустить сервисы после установки"
    echo "  --full            Полная установка (все опции)"
    echo ""
    exit 1
fi

HOST=$1
shift

# Опции
INSTALL_DEPS=false
SETUP_SYSTEMD=false
START_SERVICES=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --install-deps)
            INSTALL_DEPS=true
            shift
            ;;
        --setup-systemd)
            SETUP_SYSTEMD=true
            shift
            ;;
        --start-services)
            START_SERVICES=true
            shift
            ;;
        --full)
            INSTALL_DEPS=true
            SETUP_SYSTEMD=true
            START_SERVICES=true
            shift
            ;;
        *)
            error "Неизвестная опция: $1"
            exit 1
            ;;
    esac
done

# Проверка SSH подключения
info "Проверка подключения к $HOST..."
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$HOST" exit 2>/dev/null; then
    error "Не удалось подключиться к $HOST"
    echo "Убедитесь, что:"
    echo "  1. SSH ключ добавлен: ssh-copy-id $HOST"
    echo "  2. Сервер доступен: ping ${HOST#*@}"
    exit 1
fi
success "Подключение установлено"

# Определить директорию на сервере
REMOTE_DIR="/opt/stock_signal_analyzer"
REMOTE_USER=$(ssh "$HOST" whoami)

info "Удаленный пользователь: $REMOTE_USER"
info "Директория установки: $REMOTE_DIR"

# Создать архив проекта
info "Создание архива проекта..."
TEMP_ARCHIVE=$(mktemp /tmp/stock-signal-XXXXXX.tar.gz)
tar czf "$TEMP_ARCHIVE" \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.git' \
    --exclude='*.log' \
    --exclude='.env' \
    --exclude='node_modules' \
    -C "$(dirname "$0")" \
    "$(basename "$(pwd)")"

success "Архив создан: $TEMP_ARCHIVE"

# Копировать на сервер
info "Копирование на сервер..."
ssh "$HOST" "sudo mkdir -p $REMOTE_DIR && sudo chown $REMOTE_USER $REMOTE_DIR"
scp "$TEMP_ARCHIVE" "$HOST:/tmp/stock-signal.tar.gz"
ssh "$HOST" "cd $REMOTE_DIR && tar xzf /tmp/stock-signal.tar.gz --strip-components=1 && rm /tmp/stock-signal.tar.gz"
rm "$TEMP_ARCHIVE"
success "Файлы скопированы"

# Установить системные зависимости
if [ "$INSTALL_DEPS" = true ]; then
    info "Установка системных зависимостей..."
    ssh "$HOST" << 'EOF'
        sudo apt-get update
        sudo apt-get install -y python3 python3-pip python3-venv git
EOF
    success "Зависимости установлены"
fi

# Установить Python зависимости
info "Установка Python зависимостей..."
ssh "$HOST" << EOF
    cd $REMOTE_DIR
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
EOF
success "Python зависимости установлены"

# Запустить интерактивную установку
info "Запуск интерактивной установки..."
echo ""
warning "Сейчас откроется интерактивное меню на сервере."
warning "Ответьте на вопросы для настройки программы."
echo ""
read -p "Нажмите Enter для продолжения..."

ssh -t "$HOST" << EOF
    cd $REMOTE_DIR
    source venv/bin/activate
    python setup.py
EOF

# Настроить systemd сервисы
if [ "$SETUP_SYSTEMD" = true ]; then
    info "Настройка systemd сервисов..."

    # Создать директорию для логов
    ssh "$HOST" "mkdir -p $REMOTE_DIR/logs"

    # Заменить плейсхолдеры в service файлах
    for service_file in deploy/*.service deploy/*.timer; do
        if [ -f "$service_file" ]; then
            service_name=$(basename "$service_file")
            info "Настройка $service_name..."

            # Копировать и заменить плейсхолдеры
            scp "$service_file" "$HOST:/tmp/$service_name"
            ssh "$HOST" << EOF
                sed -i "s|%USER%|$REMOTE_USER|g" /tmp/$service_name
                sed -i "s|%WORKING_DIR%|$REMOTE_DIR|g" /tmp/$service_name
                sudo mv /tmp/$service_name /etc/systemd/system/
                sudo chmod 644 /etc/systemd/system/$service_name
EOF
        fi
    done

    # Перезагрузить systemd
    ssh "$HOST" "sudo systemctl daemon-reload"
    success "Systemd сервисы настроены"
fi

# Запустить сервисы
if [ "$START_SERVICES" = true ]; then
    info "Запуск сервисов..."

    # Включить и запустить бота
    ssh "$HOST" << EOF
        sudo systemctl enable stock-signal-bot.service
        sudo systemctl start stock-signal-bot.service
        sudo systemctl status stock-signal-bot.service --no-pager
EOF

    # Включить и запустить таймер трекера
    ssh "$HOST" << EOF
        sudo systemctl enable stock-signal-tracker.timer
        sudo systemctl start stock-signal-tracker.timer
        sudo systemctl status stock-signal-tracker.timer --no-pager
EOF

    success "Сервисы запущены"
fi

# Итоговая информация
echo ""
echo "═══════════════════════════════════════════════════════════════"
success "Деплой завершен!"
echo "═══════════════════════════════════════════════════════════════"
echo ""
info "Сервер: $HOST"
info "Директория: $REMOTE_DIR"
echo ""
info "Полезные команды:"
echo ""
echo "  # Подключиться к серверу"
echo "  ssh $HOST"
echo ""
echo "  # Проверить статус бота"
echo "  ssh $HOST 'sudo systemctl status stock-signal-bot'"
echo ""
echo "  # Посмотреть логи бота"
echo "  ssh $HOST 'tail -f $REMOTE_DIR/logs/bot.log'"
echo ""
echo "  # Проверить статус трекера"
echo "  ssh $HOST 'sudo systemctl status stock-signal-tracker.timer'"
echo ""
echo "  # Мониторить сигналы"
echo "  ssh $HOST 'cd $REMOTE_DIR && source venv/bin/activate && python tools/monitor_signals.py'"
echo ""
echo "  # Перезапустить бота"
echo "  ssh $HOST 'sudo systemctl restart stock-signal-bot'"
echo ""
echo "  # Остановить все сервисы"
echo "  ssh $HOST 'sudo systemctl stop stock-signal-bot stock-signal-tracker.timer'"
echo ""
echo "═══════════════════════════════════════════════════════════════"
