#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  Stock Signal Analyzer — Интерактивное управление (Ubuntu VPS)
#  Единая точка входа: установка, настройка, запуск, обновление.
#
#  Использование:
#    chmod +x manage.sh && ./manage.sh
#    sudo ./manage.sh          # для systemd и создания директорий
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

# ── Цвета ────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── Пути ─────────────────────────────────────────────────────────────
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/venv"
VENV_PYTHON="$VENV_DIR/bin/python"
ENV_FILE="$PROJECT_DIR/.env"
DATA_DIR="/var/lib/stock_signal_analyzer"
LOG_DIR="/var/log/stock_signal"
SERVICE_NAME="stock-signal-bot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# Определить пользователя (даже при sudo)
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    RUN_USER="$SUDO_USER"
else
    RUN_USER="$(whoami)"
fi

# ── Утилиты вывода ───────────────────────────────────────────────────
info()    { echo -e "${BLUE}[ℹ]${NC} $1"; }
ok()      { echo -e "${GREEN}[✓]${NC} $1"; }
warn()    { echo -e "${YELLOW}[⚠]${NC} $1"; }
fail()    { echo -e "${RED}[✗]${NC} $1"; }
header()  { echo -e "\n${BOLD}${CYAN}═══ $1 ═══${NC}\n"; }

# ── Проверки окружения ───────────────────────────────────────────────
check_os() {
    if [[ "$(uname -s)" != "Linux" ]]; then
        fail "Этот скрипт предназначен для Linux (Ubuntu/Debian)."
        fail "Текущая ОС: $(uname -s)"
        exit 1
    fi
}

check_python() {
    if ! command -v python3 &>/dev/null; then
        fail "Python 3 не найден."
        echo "  Установите: sudo apt install python3 python3-venv python3-pip"
        return 1
    fi
    local pymaj pymin
    pymaj=$(python3 -c 'import sys; print(sys.version_info[0])')
    pymin=$(python3 -c 'import sys; print(sys.version_info[1])')
    if (( pymaj < 3 || (pymaj == 3 && pymin < 9) )); then
        fail "Нужен Python 3.9+, сейчас: $(python3 -V)"
        return 1
    fi
    ok "Python $(python3 -V 2>&1 | awk '{print $2}')"
    return 0
}

check_venv() {
    if [ -f "$VENV_PYTHON" ]; then
        ok "venv найден: $VENV_DIR"
        return 0
    else
        warn "venv не найден"
        return 1
    fi
}

check_env() {
    if [ -f "$ENV_FILE" ]; then
        ok ".env найден"
        return 0
    else
        warn ".env не найден"
        return 1
    fi
}

check_service() {
    if [ -f "$SERVICE_FILE" ]; then
        if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
            ok "Сервис $SERVICE_NAME: активен"
            return 0
        else
            warn "Сервис $SERVICE_NAME: остановлен"
            return 1
        fi
    else
        warn "Systemd сервис не установлен"
        return 2
    fi
}

# ── Вспомогательные функции ──────────────────────────────────────────
ask_yes_no() {
    local question="$1" default="${2:-y}"
    local prompt
    if [[ "$default" == "y" ]]; then
        prompt="[Y/n]"
    else
        prompt="[y/N]"
    fi
    read -r -p "$(echo -e "${CYAN}?${NC}") $question $prompt: " answer
    answer="${answer:-$default}"
    [[ "$answer" =~ ^[Yy] ]]
}

ask_input() {
    local question="$1" default="${2:-}"
    local prompt="$(echo -e "${CYAN}?${NC}") $question"
    if [ -n "$default" ]; then
        prompt="$prompt [${default}]"
    fi
    read -r -p "$prompt: " answer
    echo "${answer:-$default}"
}

ask_choice() {
    local question="$1"
    shift
    local options=("$@")
    echo -e "\n${CYAN}?${NC} $question"
    for i in "${!options[@]}"; do
        echo "  $((i+1))) ${options[$i]}"
    done
    while true; do
        read -r -p "  Выбор [1-${#options[@]}]: " choice
        if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#options[@]} )); then
            return $((choice - 1))
        fi
        echo "  Введите число от 1 до ${#options[@]}"
    done
}

need_sudo() {
    if [ "$(id -u)" -ne 0 ]; then
        fail "Эта операция требует sudo. Перезапустите: sudo ./manage.sh"
        return 1
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
#  1. ПОЛНАЯ УСТАНОВКА
# ═══════════════════════════════════════════════════════════════════════
do_full_install() {
    header "Полная установка Stock Signal Analyzer"

    # --- Системные пакеты ---
    header "1/6  Системные пакеты"
    if need_sudo; then
        apt-get update -qq
        apt-get install -y -qq python3 python3-venv python3-pip git curl
        ok "Системные пакеты установлены"
    fi

    check_python || exit 1

    # --- venv ---
    header "2/6  Виртуальное окружение"
    if [ ! -f "$VENV_PYTHON" ]; then
        python3 -m venv "$VENV_DIR"
        ok "venv создан"
    else
        ok "venv уже существует"
    fi
    chown -R "$RUN_USER":"$RUN_USER" "$VENV_DIR" 2>/dev/null || true

    "$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel -q
    "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt" -q
    ok "Основные зависимости установлены"

    # Dev-зависимости
    if [ -f "$PROJECT_DIR/requirements-dev.txt" ]; then
        if ask_yes_no "Установить dev-зависимости (pytest, black, flake8)?" "n"; then
            "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements-dev.txt" -q \
                && ok "Dev-зависимости установлены" \
                || warn "Dev-зависимости не установились"
        fi
    fi

    # Scale-зависимости (PostgreSQL, Redis, Celery)
    if [ -f "$PROJECT_DIR/requirements-scale.txt" ]; then
        if ask_yes_no "Установить scale-зависимости (PostgreSQL, Redis, Celery)?" "y"; then
            "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements-scale.txt" -q \
                && ok "Scale-зависимости установлены" \
                || warn "Scale-зависимости не установились (не критично для базового режима)"
        fi
    fi

    # API-зависимости (FastAPI, uvicorn)
    if [ -f "$PROJECT_DIR/requirements-api.txt" ]; then
        if ask_yes_no "Установить API-зависимости (FastAPI, uvicorn)?" "y"; then
            "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements-api.txt" -q \
                && ok "API-зависимости установлены" \
                || warn "API-зависимости не установились (не критично)"
        fi
    fi

    # T-Bank SDK
    if [ -f "$PROJECT_DIR/requirements-tbank.txt" ]; then
        if ask_yes_no "Установить SDK Т-Банка (для котировок .ME)?" "y"; then
            "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements-tbank.txt" -q \
                && ok "T-Bank SDK установлен" \
                || warn "T-Bank SDK не установился (не критично для US-тикеров)"
        fi
    fi

    # --- Директории ---
    header "3/6  Директории для данных"
    mkdir -p "$DATA_DIR" "$LOG_DIR"
    chown -R "$RUN_USER":"$RUN_USER" "$DATA_DIR" "$LOG_DIR" 2>/dev/null || true
    chmod 755 "$DATA_DIR" "$LOG_DIR"
    ok "Директории: $DATA_DIR, $LOG_DIR"

    # --- .env ---
    header "4/6  Конфигурация (.env)"
    do_configure_env

    # --- systemd ---
    header "5/6  Systemd сервис"
    do_install_service

    # --- Проверка ---
    header "6/6  Проверка"
    do_status

    header "Установка завершена!"
    echo ""
    echo "  Следующие шаги:"
    echo "    1. Проверить .env:  nano $ENV_FILE"
    echo "    2. Smoke test:     sudo -u $RUN_USER $VENV_PYTHON main.py AAPL"
    echo "    3. Запустить бота: sudo systemctl start $SERVICE_NAME"
    echo "    4. Логи:           journalctl -u $SERVICE_NAME -f"
    echo ""
}

# ═══════════════════════════════════════════════════════════════════════
#  2. НАСТРОЙКА .ENV
# ═══════════════════════════════════════════════════════════════════════
do_configure_env() {
    # Загрузить существующие значения если .env есть
    local cur_tg="" cur_finnhub="" cur_tinkoff="" cur_signal_log="" cur_data="" cur_collect=""
    if [ -f "$ENV_FILE" ]; then
        cur_tg=$(grep -oP '(?<=^TELEGRAM_BOT_TOKEN=).+' "$ENV_FILE" 2>/dev/null || true)
        cur_finnhub=$(grep -oP '(?<=^FINNHUB_API_KEY=).+' "$ENV_FILE" 2>/dev/null || true)
        cur_tinkoff=$(grep -oP '(?<=^TINKOFF_INVEST_TOKEN=).+' "$ENV_FILE" 2>/dev/null || true)
        if [ -z "$cur_tinkoff" ]; then
            cur_tinkoff=$(grep -oP '(?<=^TINKOFF_TOKEN=).+' "$ENV_FILE" 2>/dev/null || true)
        fi
        cur_signal_log=$(grep -oP '(?<=^SSA_SIGNAL_LOG=).+' "$ENV_FILE" 2>/dev/null || true)
        cur_data=$(grep -oP '(?<=^STOCK_SIGNAL_DATA=).+' "$ENV_FILE" 2>/dev/null || true)
        cur_collect=$(grep -oP '(?<=^COLLECT_INTERVAL_SEC=).+' "$ENV_FILE" 2>/dev/null || true)
    fi

    echo "Настройка токенов и путей. Enter = оставить текущее значение."
    echo ""

    # Telegram
    local mask_tg=""
    if [ -n "$cur_tg" ]; then mask_tg="${cur_tg:0:10}..."; fi
    local new_tg
    new_tg=$(ask_input "Telegram Bot Token (от @BotFather)" "$mask_tg")
    if [[ "$new_tg" != "$mask_tg" ]] && [ -n "$new_tg" ]; then
        cur_tg="$new_tg"
    fi

    # Finnhub
    local mask_fh=""
    if [ -n "$cur_finnhub" ]; then mask_fh="${cur_finnhub:0:8}..."; fi
    local new_fh
    new_fh=$(ask_input "Finnhub API Key (US акции, опционально)" "$mask_fh")
    if [[ "$new_fh" != "$mask_fh" ]] && [ -n "$new_fh" ]; then
        cur_finnhub="$new_fh"
    fi

    # Tinkoff
    local mask_tk=""
    if [ -n "$cur_tinkoff" ]; then mask_tk="${cur_tinkoff:0:10}..."; fi
    local new_tk
    new_tk=$(ask_input "Tinkoff/T-Bank Token (RU акции, опционально)" "$mask_tk")
    if [[ "$new_tk" != "$mask_tk" ]] && [ -n "$new_tk" ]; then
        cur_tinkoff="$new_tk"
    fi

    # Пути
    cur_signal_log=$(ask_input "Путь к логу сигналов" "${cur_signal_log:-$DATA_DIR/signals.jsonl}")
    cur_data=$(ask_input "Директория данных" "${cur_data:-$DATA_DIR}")

    # Автосбор
    ask_choice "Интервал автосбора сигналов" \
        "Каждые 4 часа (рекомендуется)" \
        "Каждый час (агрессивно)" \
        "Каждые 8 часов (консервативно)" \
        "Отключить автосбор"
    local collect_choice=$?
    local collect_values=("14400" "3600" "28800" "0")
    cur_collect="${collect_values[$collect_choice]}"

    # Записать .env
    cat > "$ENV_FILE" << ENVEOF
# Stock Signal Analyzer — конфигурация
# Сгенерировано: $(date '+%Y-%m-%d %H:%M:%S')

# ── Telegram ──────────────────────────────────
TELEGRAM_BOT_TOKEN=${cur_tg}

# ── API ключи ─────────────────────────────────
FINNHUB_API_KEY=${cur_finnhub}
TINKOFF_INVEST_TOKEN=${cur_tinkoff}

# ── Пути ──────────────────────────────────────
SSA_SIGNAL_LOG=${cur_signal_log}
STOCK_SIGNAL_DATA=${cur_data}

# ── Автосбор и уведомления ────────────────────
COLLECT_INTERVAL_SEC=${cur_collect}
NOTIFY_INTERVAL_SEC=3600
NOTIFY_MIN_TIER=
ENVEOF

    chmod 600 "$ENV_FILE"
    chown "$RUN_USER":"$RUN_USER" "$ENV_FILE" 2>/dev/null || true
    ok ".env сохранён: $ENV_FILE"
}

# ═══════════════════════════════════════════════════════════════════════
#  3. УСТАНОВКА / ОБНОВЛЕНИЕ SYSTEMD СЕРВИСА
# ═══════════════════════════════════════════════════════════════════════
do_install_service() {
    need_sudo || return 1

    cat > "$SERVICE_FILE" << SVCEOF
[Unit]
Description=Stock Signal Analyzer Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
Environment="PYTHONUNBUFFERED=1"
ExecStart=$VENV_PYTHON telegram_bot.py
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal

# Безопасность
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$PROJECT_DIR $DATA_DIR $LOG_DIR
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SVCEOF

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME" 2>/dev/null
    ok "Сервис $SERVICE_NAME установлен и включён"

    # Outcome tracker (таймер)
    local tracker_svc="/etc/systemd/system/stock-signal-tracker.service"
    local tracker_timer="/etc/systemd/system/stock-signal-tracker.timer"

    cat > "$tracker_svc" << TRKEOF
[Unit]
Description=Stock Signal Analyzer — Outcome Tracker
After=network-online.target

[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
Environment="PYTHONUNBUFFERED=1"
ExecStart=$VENV_PYTHON -m stock_signal_analyzer.outcome_tracker
StandardOutput=journal
StandardError=journal
TRKEOF

    cat > "$tracker_timer" << TMREOF
[Unit]
Description=Stock Signal Analyzer — Outcome Tracker Timer
Requires=stock-signal-tracker.service

[Timer]
OnBootSec=5min
OnUnitActiveSec=1h
Unit=stock-signal-tracker.service

[Install]
WantedBy=timers.target
TMREOF

    systemctl daemon-reload
    systemctl enable stock-signal-tracker.timer 2>/dev/null
    ok "Таймер outcome-tracker установлен (каждый час)"
}

# ═══════════════════════════════════════════════════════════════════════
#  4. ЗАПУСК / ОСТАНОВКА / ПЕРЕЗАПУСК
# ═══════════════════════════════════════════════════════════════════════
do_start() {
    need_sudo || return 1
    if [ ! -f "$SERVICE_FILE" ]; then
        fail "Сервис не установлен. Сначала выполните установку."
        return 1
    fi
    systemctl start "$SERVICE_NAME"
    systemctl start stock-signal-tracker.timer 2>/dev/null || true
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        ok "Бот запущен"
    else
        fail "Бот не запустился. Логи: journalctl -u $SERVICE_NAME -e"
    fi
}

do_stop() {
    need_sudo || return 1
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl stop stock-signal-tracker.timer 2>/dev/null || true
    ok "Бот и таймер остановлены"
}

do_restart() {
    need_sudo || return 1
    systemctl restart "$SERVICE_NAME"
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        ok "Бот перезапущен"
    else
        fail "Бот не запустился. Логи: journalctl -u $SERVICE_NAME -e"
    fi
}

# ═══════════════════════════════════════════════════════════════════════
#  5. СТАТУС
# ═══════════════════════════════════════════════════════════════════════
do_status() {
    header "Статус системы"

    check_python  || true
    check_venv    || true
    check_env     || true

    # Проверить токены
    if [ -f "$ENV_FILE" ]; then
        local tg_set fh_set tk_set
        tg_set=$(grep -cP '^TELEGRAM_BOT_TOKEN=.+' "$ENV_FILE" 2>/dev/null || echo 0)
        fh_set=$(grep -cP '^FINNHUB_API_KEY=.+' "$ENV_FILE" 2>/dev/null || echo 0)
        tk_set=$(grep -cP '^TINKOFF_INVEST_TOKEN=.+' "$ENV_FILE" 2>/dev/null || echo 0)
        [ "$tg_set" -gt 0 ] && ok "Telegram Token: задан" || warn "Telegram Token: не задан"
        [ "$fh_set" -gt 0 ] && ok "Finnhub Key: задан"    || info "Finnhub Key: не задан (опционально)"
        [ "$tk_set" -gt 0 ] && ok "Tinkoff Token: задан"   || info "Tinkoff Token: не задан (опционально)"
    fi

    # Systemd
    if [ -f "$SERVICE_FILE" ]; then
        echo ""
        if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
            ok "Сервис $SERVICE_NAME: ${GREEN}активен${NC}"
            local uptime
            uptime=$(systemctl show "$SERVICE_NAME" --property=ActiveEnterTimestamp --value 2>/dev/null || true)
            [ -n "$uptime" ] && info "Запущен: $uptime"
        else
            warn "Сервис $SERVICE_NAME: остановлен"
        fi
        if systemctl is-active --quiet stock-signal-tracker.timer 2>/dev/null; then
            ok "Таймер tracker: активен"
        else
            warn "Таймер tracker: остановлен"
        fi
    else
        warn "Systemd сервис не установлен"
    fi

    # Лог сигналов
    echo ""
    local sig_log
    sig_log=$(grep -oP '(?<=^SSA_SIGNAL_LOG=).+' "$ENV_FILE" 2>/dev/null || true)
    if [ -n "$sig_log" ] && [ -f "$sig_log" ]; then
        local count
        count=$(wc -l < "$sig_log")
        ok "Лог сигналов: $sig_log ($count записей)"
    elif [ -n "$sig_log" ]; then
        info "Лог сигналов: $sig_log (файл пока не создан)"
    fi
}

# ═══════════════════════════════════════════════════════════════════════
#  6. ЛОГИ
# ═══════════════════════════════════════════════════════════════════════
do_logs() {
    ask_choice "Какие логи показать?" \
        "Бот (последние 50 строк)" \
        "Бот (следить в реальном времени)" \
        "Outcome Tracker" \
        "Назад"
    local choice=$?
    case $choice in
        0) journalctl -u "$SERVICE_NAME" -n 50 --no-pager ;;
        1) journalctl -u "$SERVICE_NAME" -f ;;
        2) journalctl -u stock-signal-tracker -n 50 --no-pager ;;
        3) return ;;
    esac
}

# ═══════════════════════════════════════════════════════════════════════
#  7. ОБНОВЛЕНИЕ ЗАВИСИМОСТЕЙ
# ═══════════════════════════════════════════════════════════════════════
do_update_deps() {
    header "Обновление зависимостей"
    if [ ! -f "$VENV_PYTHON" ]; then
        fail "venv не найден. Сначала выполните установку."
        return 1
    fi
    "$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel -q
    "$VENV_DIR/bin/pip" install --upgrade -r "$PROJECT_DIR/requirements.txt" -q
    ok "Основные зависимости обновлены"

    # Dev requirements (для тестов и линтинга)
    if [ -f "$PROJECT_DIR/requirements-dev.txt" ]; then
        if ask_yes_no "Установить dev-зависимости (pytest, black, flake8…)?" "n"; then
            "$VENV_DIR/bin/pip" install --upgrade -r "$PROJECT_DIR/requirements-dev.txt" -q \
                && ok "Dev-зависимости обновлены" \
                || warn "Dev-зависимости не обновились"
        fi
    fi

    # Scale requirements (PostgreSQL, Redis, Celery)
    if [ -f "$PROJECT_DIR/requirements-scale.txt" ]; then
        if ask_yes_no "Установить scale-зависимости (PostgreSQL, Redis, Celery)?" "n"; then
            "$VENV_DIR/bin/pip" install --upgrade -r "$PROJECT_DIR/requirements-scale.txt" -q \
                && ok "Scale-зависимости обновлены" \
                || warn "Scale-зависимости не обновились"
        fi
    fi

    # API requirements (FastAPI, uvicorn)
    if [ -f "$PROJECT_DIR/requirements-api.txt" ]; then
        if ask_yes_no "Установить API-зависимости (FastAPI, uvicorn)?" "n"; then
            "$VENV_DIR/bin/pip" install --upgrade -r "$PROJECT_DIR/requirements-api.txt" -q \
                && ok "API-зависимости обновлены" \
                || warn "API-зависимости не обновились"
        fi
    fi

    # T-Bank SDK
    if [ -f "$PROJECT_DIR/requirements-tbank.txt" ]; then
        if ask_yes_no "Обновить T-Bank SDK?" "n"; then
            "$VENV_DIR/bin/pip" install --upgrade -r "$PROJECT_DIR/requirements-tbank.txt" -q \
                && ok "T-Bank SDK обновлён" \
                || warn "T-Bank SDK не обновился"
        fi
    fi

    if [ -f "$SERVICE_FILE" ] && systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        if ask_yes_no "Перезапустить бота с новыми зависимостями?" "y"; then
            do_restart
        fi
    fi
}

# ═══════════════════════════════════════════════════════════════════════
#  8. SMOKE TEST
# ═══════════════════════════════════════════════════════════════════════
do_smoke_test() {
    header "Smoke Test"

    # Используем venv если есть, иначе системный python3
    local py="$VENV_PYTHON"
    if [ ! -f "$VENV_PYTHON" ]; then
        if command -v python3 &>/dev/null; then
            warn "venv не найден, используем системный python3"
            py="python3"
        else
            fail "Ни venv, ни python3 не найдены."
            return 1
        fi
    fi

    info "Проверка импортов..."
    if $py -c "from stock_signal_analyzer.engine import build_report; print('OK')" 2>/dev/null; then
        ok "Импорты работают"
    else
        fail "Ошибка импорта. Проверьте зависимости."
        return 1
    fi

    local ticker
    ticker=$(ask_input "Тикер для теста" "AAPL")
    info "Генерация сигнала для $ticker (может занять 10-30 сек)..."

    if [ -f "$ENV_FILE" ]; then
        set -a
        source "$ENV_FILE" 2>/dev/null || true
        set +a
    fi

    $py main.py "$ticker" && ok "Тест пройден" || fail "Ошибка при генерации сигнала"
}

# ═══════════════════════════════════════════════════════════════════════
#  9. МОНИТОРИНГ СИГНАЛОВ
# ═══════════════════════════════════════════════════════════════════════
do_monitor() {
    local py="$VENV_PYTHON"
    if [ ! -f "$VENV_PYTHON" ]; then
        if command -v python3 &>/dev/null; then
            warn "venv не найден, используем системный python3"
            py="python3"
        else
            fail "Ни venv, ни python3 не найдены."
            return 1
        fi
    fi
    if [ -f "$ENV_FILE" ]; then
        set -a
        source "$ENV_FILE" 2>/dev/null || true
        set +a
    fi
    $py tools/monitor_signals.py
}

# ═══════════════════════════════════════════════════════════════════════
#  10. УДАЛЕНИЕ
# ═══════════════════════════════════════════════════════════════════════
do_uninstall() {
    header "Удаление"
    warn "Это остановит сервисы и удалит systemd-конфигурацию."
    warn "Код проекта и данные НЕ будут удалены."
    if ! ask_yes_no "Продолжить?" "n"; then
        return
    fi
    need_sudo || return 1

    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl stop stock-signal-tracker.timer 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable stock-signal-tracker.timer 2>/dev/null || true
    rm -f "$SERVICE_FILE"
    rm -f /etc/systemd/system/stock-signal-tracker.service
    rm -f /etc/systemd/system/stock-signal-tracker.timer
    systemctl daemon-reload
    ok "Systemd сервисы удалены"

    if ask_yes_no "Удалить venv ($VENV_DIR)?" "n"; then
        rm -rf "$VENV_DIR"
        ok "venv удалён"
    fi
}

# ═══════════════════════════════════════════════════════════════════════
#  ГЛАВНОЕ МЕНЮ
# ═══════════════════════════════════════════════════════════════════════
show_banner() {
    echo -e "${BOLD}${CYAN}"
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║       Stock Signal Analyzer — Управление (VPS)         ║"
    echo "╚══════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    echo "  Проект:      $PROJECT_DIR"
    echo "  Пользователь: $RUN_USER"
    echo ""
}

main_menu() {
    while true; do
        show_banner

        # Быстрый статус в одну строку
        local svc_status="не установлен"
        if [ -f "$SERVICE_FILE" ]; then
            if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
                svc_status="${GREEN}работает${NC}"
            else
                svc_status="${YELLOW}остановлен${NC}"
            fi
        fi
        echo -e "  Бот: $svc_status"
        echo ""

        echo "  ── Установка ──────────────────────────"
        echo "    1) Полная установка (всё с нуля)"
        echo "    2) Настроить .env (токены, пути)"
        echo "    3) Установить/обновить systemd сервис"
        echo "    4) Обновить зависимости (pip)"
        echo ""
        echo "  ── Управление ─────────────────────────"
        echo "    5) Запустить бота"
        echo "    6) Остановить бота"
        echo "    7) Перезапустить бота"
        echo ""
        echo "  ── Диагностика ────────────────────────"
        echo "    8) Статус системы"
        echo "    9) Логи"
        echo "   10) Smoke test (проверка тикера)"
        echo "   11) Мониторинг сигналов"
        echo ""
        echo "  ── Прочее ─────────────────────────────"
        echo "   12) Удалить сервисы"
        echo "    0) Выход"
        echo ""

        read -r -p "$(echo -e "${CYAN}▶${NC}") Выберите действие: " choice

        case "$choice" in
            1)  do_full_install ;;
            2)  do_configure_env ;;
            3)  do_install_service ;;
            4)  do_update_deps ;;
            5)  do_start ;;
            6)  do_stop ;;
            7)  do_restart ;;
            8)  do_status ;;
            9)  do_logs ;;
            10) do_smoke_test ;;
            11) do_monitor ;;
            12) do_uninstall ;;
            0|q|exit) echo ""; ok "До встречи!"; exit 0 ;;
            *)  warn "Неизвестный пункт: $choice" ;;
        esac

        echo ""
        read -r -p "Нажмите Enter для возврата в меню..." _
    done
}

# ═══════════════════════════════════════════════════════════════════════
#  ТОЧКА ВХОДА
# ═══════════════════════════════════════════════════════════════════════
check_os

# Поддержка прямого вызова команд: ./manage.sh install | start | stop | ...
case "${1:-}" in
    install)    do_full_install ;;
    configure)  do_configure_env ;;
    service)    do_install_service ;;
    start)      do_start ;;
    stop)       do_stop ;;
    restart)    do_restart ;;
    status)     do_status ;;
    logs)       do_logs ;;
    test)       do_smoke_test ;;
    monitor)    do_monitor ;;
    update)     do_update_deps ;;
    uninstall)  do_uninstall ;;
    help|--help|-h)
        echo "Использование: ./manage.sh [команда]"
        echo ""
        echo "Команды:"
        echo "  install     Полная установка"
        echo "  configure   Настроить .env"
        echo "  service     Установить systemd сервис"
        echo "  start       Запустить бота"
        echo "  stop        Остановить бота"
        echo "  restart     Перезапустить бота"
        echo "  status      Показать статус"
        echo "  logs        Показать логи"
        echo "  test        Smoke test"
        echo "  monitor     Мониторинг сигналов"
        echo "  update      Обновить зависимости"
        echo "  uninstall   Удалить сервисы"
        echo ""
        echo "Без аргументов — интерактивное меню."
        ;;
    "")         main_menu ;;
    *)          fail "Неизвестная команда: $1. Используйте --help"; exit 1 ;;
esac
