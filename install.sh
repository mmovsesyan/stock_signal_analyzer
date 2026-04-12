#!/usr/bin/env bash
###############################################################################
#  Stock Signal Analyzer — Установка (интерактив + авто)
#
#  Использование:
#    git clone <repo-url> && cd stock_signal_analyzer && bash install.sh
#    bash install.sh --auto
#    TELEGRAM_BOT_TOKEN=... TINKOFF_INVEST_TOKEN=... FINNHUB_API_KEY=... bash install.sh --auto
#
#  Что делает:
#    1. Ставит системные пакеты (python3, venv)
#    2. Создаёт venv + зависимости (PyPI + T-Bank SDK)
#    3. Запрашивает токены (Telegram, T-Bank, Finnhub)
#    4. Настраивает systemd-сервис
#    5. Запускает бота
###############################################################################
set -euo pipefail

# Не выполнять установку как root при «sudo bash install.sh»: перезапуск под исходным пользователем (venv и User= совпадут).
if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    exec sudo -u "$SUDO_USER" bash "$0" "$@"
fi

# ── Режимы и аргументы ───────────────────────────────────────────────────────
AUTO_MODE=0
MENU_CHOICE="${MENU_CHOICE:-}"
AUTO_INSTALL_TBANK="${AUTO_INSTALL_TBANK:-y}"        # y/n
AUTO_ENABLE_SERVICE="${AUTO_ENABLE_SERVICE:-y}"      # y/n
AUTO_START_SERVICE="${AUTO_START_SERVICE:-y}"        # y/n
AUTO_COLLECT_INTERVAL_SEC="${AUTO_COLLECT_INTERVAL_SEC:-14400}"
AUTO_RUN_SMOKE_TEST="${AUTO_RUN_SMOKE_TEST:-n}"      # y/n

for arg in "$@"; do
    case "$arg" in
        --auto|-a)
            AUTO_MODE=1
            MENU_CHOICE="1"
            ;;
        --check)
            MENU_CHOICE="6"
            ;;
        --help|-h)
            cat <<'USAGE'
Usage:
  bash install.sh               # interactive menu
  bash install.sh --auto        # full automatic install for VPS
  bash install.sh --check       # run health checks

Auto mode environment variables:
  TELEGRAM_BOT_TOKEN=...        required for bot runtime
  TINKOFF_INVEST_TOKEN=...      optional
  FINNHUB_API_KEY=...           optional
  AUTO_INSTALL_TBANK=y|n        default: y
  AUTO_ENABLE_SERVICE=y|n       default: y
  AUTO_START_SERVICE=y|n        default: y
  AUTO_COLLECT_INTERVAL_SEC=... default: 14400
  AUTO_RUN_SMOKE_TEST=y|n       default: n
USAGE
            exit 0
            ;;
    esac
done

# ── Цвета ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# ── Хелперы ──────────────────────────────────────────────────────────────────
info()    { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()      { echo -e "${GREEN}[  OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()    { echo -e "${RED}[FAIL]${NC} $*"; }
header()  { echo -e "\n${BOLD}═══ $* ═══${NC}\n"; }
divider() { echo -e "${DIM}─────────────────────────────────────────────────${NC}"; }

normalize_yn() {
    local value="${1:-n}"
    if [[ "$value" =~ ^[Yy]([Ee][Ss])?$ ]]; then
        echo "y"
    else
        echo "n"
    fi
}

ensure_linux_vps_prereqs() {
    if [ ! -f /etc/os-release ]; then
        fail "Нужен Linux VPS с /etc/os-release (Ubuntu/Debian)."
        exit 1
    fi

    . /etc/os-release
    info "Система: ${PRETTY_NAME:-unknown}"

    if ! command -v apt-get >/dev/null 2>&1; then
        fail "apt-get не найден. Скрипт рассчитан на Ubuntu/Debian."
        exit 1
    fi

    if ! command -v systemctl >/dev/null 2>&1; then
        fail "systemctl не найден. Нужна система с systemd."
        exit 1
    fi
}

ensure_sudo_available() {
    if ! command -v sudo &>/dev/null; then
        fail "sudo не найден. Запустите от root или установите sudo."
        exit 1
    fi
    if [ "$AUTO_MODE" -eq 1 ] && [ "$(id -u)" -ne 0 ]; then
        if ! sudo -n true 2>/dev/null; then
            fail "В --auto режиме нужен парольless sudo (или запуск от root)."
            exit 1
        fi
    fi
}

ask() {
    local prompt="$1" default="${2:-}" var_name="$3"
    if [ -n "$default" ]; then
        echo -en "${BOLD}$prompt${NC} ${DIM}[$default]${NC}: "
    else
        echo -en "${BOLD}$prompt${NC}: "
    fi
    read -r input
    eval "$var_name=\"${input:-$default}\""
}

ask_secret() {
    local prompt="$1" var_name="$2"
    echo -en "${BOLD}$prompt${NC}: "
    read -rs input
    echo ""
    eval "$var_name=\"$input\""
}

ask_yn() {
    local prompt="$1" default="${2:-y}"
    local hint="Y/n"
    [ "$default" = "n" ] && hint="y/N"
    echo -en "${BOLD}$prompt${NC} ${DIM}[$hint]${NC}: "
    read -r input
    input="${input:-$default}"
    [[ "$input" =~ ^[Yy] ]]
}

spinner() {
    local pid=$1 msg="$2"
    local chars='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    while kill -0 "$pid" 2>/dev/null; do
        for (( i=0; i<${#chars}; i++ )); do
            echo -en "\r  ${CYAN}${chars:$i:1}${NC} $msg"
            sleep 0.1
        done
    done
    wait "$pid" 2>/dev/null
    local rc=$?
    echo -en "\r"
    return $rc
}

run_with_spinner() {
    local msg="$1"; shift
    "$@" &>/tmp/ssa_install_log.txt &
    local pid=$!
    spinner "$pid" "$msg" && ok "$msg" || { fail "$msg"; echo "  Лог: /tmp/ssa_install_log.txt"; return 1; }
}

# ── Баннер ───────────────────────────────────────────────────────────────────
# В неинтерактивной среде TERM может быть не задан; не падаем на clear.
if command -v clear >/dev/null 2>&1; then
    clear 2>/dev/null || true
fi
echo -e "${BOLD}"
cat << 'BANNER'

  ╔═══════════════════════════════════════════════════════╗
  ║                                                       ║
  ║     📊  Stock Signal Analyzer                         ║
  ║     ─────────────────────────────                     ║
  ║     Институциональные алгоритмы                       ║
  ║     AQR • Bridgewater • DE Shaw • Two Sigma           ║
  ║                                                       ║
  ║     Telegram-бот + CLI + Бэктест                      ║
  ║                                                       ║
  ╚═══════════════════════════════════════════════════════╝

BANNER
echo -e "${NC}"

# ── Базовая проверка среды ───────────────────────────────────────────────────
if [ -f /etc/os-release ]; then
    . /etc/os-release
    info "Система: ${PRETTY_NAME:-unknown}"
else
    warn "Не удалось определить ОС"
fi
ensure_sudo_available

# ── Главное меню ─────────────────────────────────────────────────────────────
if [ -z "$MENU_CHOICE" ]; then
    header "Главное меню"
    echo -e "  ${BOLD}1${NC}  Полная установка (рекомендуется)"
    echo -e "  ${BOLD}2${NC}  Только обновить зависимости"
    echo -e "  ${BOLD}3${NC}  Только настроить токены (.env)"
    echo -e "  ${BOLD}4${NC}  Только настроить systemd-сервис"
    echo -e "  ${BOLD}5${NC}  Управление ботом (старт/стоп/логи)"
    echo -e "  ${BOLD}6${NC}  Проверка работоспособности"
    echo -e "  ${BOLD}0${NC}  Выход"
    echo ""
    ask "Выберите" "1" MENU_CHOICE
else
    info "Режим: AUTO (пункт меню $MENU_CHOICE)"
fi

# ── Директория проекта ───────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$SCRIPT_DIR"

install_system_deps() {
    ensure_linux_vps_prereqs
    header "Системные пакеты"
    info "Устанавливаю python3, venv, pip, git, rsync..."
    run_with_spinner "apt-get update" sudo apt-get update -qq
    run_with_spinner "Установка системных пакетов" sudo apt-get install -y -qq python3 python3-venv python3-pip git rsync
    ok "Системные пакеты готовы"
}

install_python_deps() {
    header "Python-зависимости"

    local pymaj pymin
    pymaj=$(python3 -c 'import sys; print(sys.version_info[0])')
    pymin=$(python3 -c 'import sys; print(sys.version_info[1])')
    if (( pymaj < 3 || (pymaj == 3 && pymin < 9) )); then
        fail "Нужен Python 3.9+, сейчас: $(python3 -V)"
        exit 1
    fi

    if [ ! -d "$APP_DIR/.venv" ]; then
        info "Создаю виртуальное окружение..."
        python3 -m venv "$APP_DIR/.venv"
        ok "venv создан"
    else
        ok "venv уже существует"
    fi

    run_with_spinner "Обновление pip" "$APP_DIR/.venv/bin/pip" install --upgrade pip -q
    run_with_spinner "Установка зависимостей (PyPI)" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

    divider
    local install_tbank_answer
    if [ "$AUTO_MODE" -eq 1 ]; then
        install_tbank_answer="$(normalize_yn "$AUTO_INSTALL_TBANK")"
        info "AUTO: установка T-Bank SDK = $install_tbank_answer"
    elif ask_yn "Установить T-Bank SDK (для РФ-акций)?"; then
        install_tbank_answer="y"
    else
        install_tbank_answer="n"
    fi

    if [ "$install_tbank_answer" = "y" ]; then
        run_with_spinner "Установка T-Bank SDK" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements-tbank.txt" -q \
            || warn "T-Bank SDK не установился (не критично для US-тикеров)"
    fi

    ok "Все зависимости установлены"
}

configure_tokens() {
    header "Настройка токенов"

    local tg_token="" tb_token="" fh_key=""
    local collect_sec="14400"

    if [ -f "$APP_DIR/.env" ]; then
        # Подгрузить существующие значения
        tg_token="$(grep -oP '(?<=^TELEGRAM_BOT_TOKEN=).+' "$APP_DIR/.env" 2>/dev/null || true)"
        tb_token="$(grep -oP '(?<=^TINKOFF_INVEST_TOKEN=).+' "$APP_DIR/.env" 2>/dev/null || true)"
        fh_key="$(grep -oP '(?<=^FINNHUB_API_KEY=).+' "$APP_DIR/.env" 2>/dev/null || true)"
        collect_sec="$(grep -oP '(?<=^COLLECT_INTERVAL_SEC=).+' "$APP_DIR/.env" 2>/dev/null || true)"
        collect_sec="${collect_sec:-14400}"
        info "Найден .env — текущие значения будут показаны как default"
    fi

    if [ "$AUTO_MODE" -eq 1 ]; then
        tg_token="${TELEGRAM_BOT_TOKEN:-$tg_token}"
        tb_token="${TINKOFF_INVEST_TOKEN:-$tb_token}"
        fh_key="${FINNHUB_API_KEY:-$fh_key}"
        collect_sec="${AUTO_COLLECT_INTERVAL_SEC:-$collect_sec}"

        if [ -z "$tg_token" ]; then
            fail "В --auto режиме обязателен TELEGRAM_BOT_TOKEN."
            exit 1
        fi
        info "AUTO: токены взяты из переменных окружения/существующего .env"
    else
        divider
        echo -e "${BOLD}1. Telegram Bot Token${NC} (обязательно)"
        echo -e "   ${DIM}Получить: @BotFather в Telegram → /newbot${NC}"
        if [ -n "$tg_token" ]; then
            local masked="${tg_token:0:8}...${tg_token: -4}"
            echo -e "   ${DIM}Текущий: $masked${NC}"
            if ! ask_yn "   Изменить?"; then
                : # оставить как есть
            else
                ask_secret "   Токен Telegram" tg_token
            fi
        else
            ask_secret "   Токен Telegram" tg_token
        fi

        divider
        echo -e "${BOLD}2. T-Bank (Tinkoff Invest) Token${NC} (для РФ-акций)"
        echo -e "   ${DIM}Получить: tbank.ru/invest/settings/api/${NC}"
        if [ -n "$tb_token" ]; then
            local masked="${tb_token:0:8}...${tb_token: -4}"
            echo -e "   ${DIM}Текущий: $masked${NC}"
            if ! ask_yn "   Изменить?" "n"; then
                : # оставить
            else
                ask_secret "   Токен T-Bank" tb_token
            fi
        else
            ask_secret "   Токен T-Bank (Enter — пропустить)" tb_token
        fi

        divider
        echo -e "${BOLD}3. Finnhub API Key${NC} (для US-новостей, макро-календаря)"
        echo -e "   ${DIM}Бесплатно: finnhub.io → Get Free API Key${NC}"
        if [ -n "$fh_key" ]; then
            echo -e "   ${DIM}Текущий: ${fh_key:0:6}...${NC}"
            if ! ask_yn "   Изменить?" "n"; then
                : # оставить
            else
                ask_secret "   Ключ Finnhub (Enter — пропустить)" fh_key
            fi
        else
            ask_secret "   Ключ Finnhub (Enter — пропустить)" fh_key
        fi

        divider
        echo -e "${BOLD}4. Автосбор сигналов${NC}"
        echo -e "   ${DIM}Бот будет автоматически анализировать 30+ тикеров каждые N секунд${NC}"
        echo -e "   ${DIM}14400 = каждые 4 часа, 0 = выключен${NC}"
        ask "   Интервал (сек)" "$collect_sec" collect_sec
    fi

    # Записываем .env
    mkdir -p "$APP_DIR/data"
    cat > "$APP_DIR/.env" << ENVEOF
# === Stock Signal Analyzer — Конфигурация ===
# Сгенерировано install.sh $(date '+%Y-%m-%d %H:%M')

# Telegram Bot Token (обязательно для бота)
TELEGRAM_BOT_TOKEN=${tg_token}

# T-Bank (Tinkoff Invest) API Token — для РФ-акций
TINKOFF_INVEST_TOKEN=${tb_token}

# Finnhub API Key — для US-новостей и макро-календаря
FINNHUB_API_KEY=${fh_key}

# Лог сигналов (для /collect, /export и бэктеста)
SSA_SIGNAL_LOG=${APP_DIR}/data/signals.jsonl

# Автосбор сигналов (секунды, 0 = выкл)
COLLECT_INTERVAL_SEC=${collect_sec}

# Уведомления «сильный вне списка» (секунды)
NOTIFY_INTERVAL_SEC=3600
ENVEOF

    chmod 600 "$APP_DIR/.env"
    ok "Файл .env сохранён (chmod 600)"
}

setup_systemd() {
    ensure_linux_vps_prereqs
    header "Systemd-сервис"

    local svc_name="ssa-bot"
    local svc_file="/etc/systemd/system/${svc_name}.service"

    info "Создаю $svc_file..."

    sudo tee "$svc_file" > /dev/null << SVCEOF
[Unit]
Description=Stock Signal Analyzer Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python telegram_bot.py
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal

# Безопасность
NoNewPrivileges=true
ProtectSystem=strict
# Весь каталог проекта: импорт пишет __pycache__ в stock_signal_analyzer/, данные в data/, .env
ReadWritePaths=${APP_DIR}
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SVCEOF

    sudo systemctl daemon-reload
    ok "Сервис $svc_name создан"

    local enable_answer start_answer
    if [ "$AUTO_MODE" -eq 1 ]; then
        enable_answer="$(normalize_yn "$AUTO_ENABLE_SERVICE")"
        start_answer="$(normalize_yn "$AUTO_START_SERVICE")"
        info "AUTO: enable=$enable_answer, start=$start_answer"
    else
        if ask_yn "Включить автозапуск при перезагрузке?"; then
            enable_answer="y"
        else
            enable_answer="n"
        fi
        if ask_yn "Запустить бота прямо сейчас?"; then
            start_answer="y"
        else
            start_answer="n"
        fi
    fi

    if [ "$enable_answer" = "y" ]; then
        sudo systemctl enable "$svc_name"
        ok "Автозапуск включён"
    fi

    if [ "$start_answer" = "y" ]; then
        sudo systemctl restart "$svc_name"
        sleep 2
        if sudo systemctl is-active --quiet "$svc_name"; then
            ok "Бот запущен!"
        else
            fail "Бот не запустился. Логи:"
            sudo journalctl -u "$svc_name" --no-pager -n 15
        fi
    fi
}

manage_bot() {
    ensure_linux_vps_prereqs
    header "Управление ботом"

    local svc="ssa-bot"
    local status
    status="$(sudo systemctl is-active "$svc" 2>/dev/null || echo "not-found")"

    echo -e "  Статус: ${BOLD}$status${NC}"
    echo ""
    echo -e "  ${BOLD}1${NC}  Запустить / Перезапустить"
    echo -e "  ${BOLD}2${NC}  Остановить"
    echo -e "  ${BOLD}3${NC}  Показать логи (последние 50 строк)"
    echo -e "  ${BOLD}4${NC}  Следить за логами (live)"
    echo -e "  ${BOLD}5${NC}  Статус подробный"
    echo -e "  ${BOLD}0${NC}  Назад"
    echo ""
    ask "Выберите" "1" BOT_ACTION

    case "$BOT_ACTION" in
        1) sudo systemctl restart "$svc" && sleep 2 && ok "Бот перезапущен" ;;
        2) sudo systemctl stop "$svc" && ok "Бот остановлен" ;;
        3) sudo journalctl -u "$svc" --no-pager -n 50 ;;
        4) info "Ctrl+C для выхода"; sudo journalctl -u "$svc" -f ;;
        5) sudo systemctl status "$svc" --no-pager ;;
        0) return ;;
    esac
}

run_check() {
    header "Проверка работоспособности"

    divider
    echo -n "  Python............... "
    if "$APP_DIR/.venv/bin/python" --version &>/dev/null; then
        ok "$("$APP_DIR/.venv/bin/python" --version 2>&1)"
    else
        fail "не найден"
    fi

    echo -n "  numpy+pandas......... "
    if "$APP_DIR/.venv/bin/python" -c "import numpy, pandas" &>/dev/null; then
        ok "OK"
    else
        fail "не установлены"
    fi

    echo -n "  yfinance............. "
    if "$APP_DIR/.venv/bin/python" -c "import yfinance" &>/dev/null; then
        ok "OK"
    else
        fail "не установлен"
    fi

    echo -n "  T-Bank SDK........... "
    if "$APP_DIR/.venv/bin/python" -c "from t_tech.invest import Client" &>/dev/null; then
        ok "OK (t_tech.invest)"
    elif "$APP_DIR/.venv/bin/python" -c "from tinkoff.invest import Client" &>/dev/null; then
        ok "OK (tinkoff.invest)"
    else
        warn "не установлен (РФ-тикеры без fallback)"
    fi

    echo -n "  Telegram PTB......... "
    if "$APP_DIR/.venv/bin/python" -c "from telegram.ext import Application" &>/dev/null; then
        ok "OK"
    else
        fail "не установлен"
    fi

    divider
    echo -n "  .env файл............ "
    if [ -f "$APP_DIR/.env" ]; then
        ok "найден"
    else
        fail "отсутствует (запустите: настройка токенов)"
    fi

    echo -n "  TELEGRAM_BOT_TOKEN... "
    local tg
    tg="$("$APP_DIR/.venv/bin/python" -c "
import stenv; stenv.load_project_env()
import os; t=os.environ.get('TELEGRAM_BOT_TOKEN','')
print('set' if t and t != '' else 'empty')
" 2>/dev/null || echo "error")"
    if [ "$tg" = "set" ]; then ok "задан"; else warn "не задан"; fi

    echo -n "  TINKOFF_INVEST_TOKEN. "
    local tb
    tb="$("$APP_DIR/.venv/bin/python" -c "
import stenv; stenv.load_project_env()
import os; t=os.environ.get('TINKOFF_INVEST_TOKEN','')
print('set' if t and t != '' else 'empty')
" 2>/dev/null || echo "error")"
    if [ "$tb" = "set" ]; then ok "задан"; else warn "не задан (РФ-акции не будут работать)"; fi

    echo -n "  FINNHUB_API_KEY...... "
    local fh
    fh="$("$APP_DIR/.venv/bin/python" -c "
import stenv; stenv.load_project_env()
import os; t=os.environ.get('FINNHUB_API_KEY','')
print('set' if t and t != '' else 'empty')
" 2>/dev/null || echo "error")"
    if [ "$fh" = "set" ]; then ok "задан"; else warn "не задан (макро-календарь отключён)"; fi

    divider
    echo -n "  systemd сервис....... "
    if [ -f /etc/systemd/system/ssa-bot.service ]; then
        local st
        st="$(sudo systemctl is-active ssa-bot 2>/dev/null || echo "inactive")"
        if [ "$st" = "active" ]; then ok "активен"; else warn "$st"; fi
    else
        warn "не создан"
    fi

    divider
    local smoke_answer="n"
    if [ "$AUTO_MODE" -eq 1 ]; then
        smoke_answer="$(normalize_yn "$AUTO_RUN_SMOKE_TEST")"
        info "AUTO: тестовый анализ AAPL = $smoke_answer"
    elif ask_yn "Запустить тестовый анализ (AAPL)?"; then
        smoke_answer="y"
    fi

    if [ "$smoke_answer" = "y" ]; then
        info "Анализ AAPL..."
        "$APP_DIR/.venv/bin/python" main.py AAPL 2>&1 | head -40
    fi
}

# ── Выполнение ───────────────────────────────────────────────────────────────
case "$MENU_CHOICE" in
    1)
        install_system_deps
        install_python_deps
        configure_tokens
        setup_systemd
        if [ "$AUTO_MODE" -eq 1 ]; then
            run_check
        fi

        header "Установка завершена!"
        echo -e "  ${GREEN}Бот готов к работе.${NC}"
        echo ""
        echo -e "  Команды:"
        echo -e "    ${BOLD}sudo systemctl status ssa-bot${NC}   — статус"
        echo -e "    ${BOLD}sudo journalctl -u ssa-bot -f${NC}  — логи"
        echo -e "    ${BOLD}bash install.sh${NC}                 — меню"
        echo ""
        echo -e "  Telegram-команды бота:"
        echo -e "    /signal AAPL — анализ тикера"
        echo -e "    /collect — собрать 30+ сигналов"
        echo -e "    /export — выгрузить для анализа"
        echo ""
        ;;
    2)
        install_python_deps
        ;;
    3)
        configure_tokens
        ;;
    4)
        setup_systemd
        ;;
    5)
        manage_bot
        ;;
    6)
        run_check
        ;;
    0)
        echo "Выход."
        exit 0
        ;;
    *)
        fail "Неизвестный выбор: $MENU_CHOICE"
        exit 1
        ;;
esac

echo ""
