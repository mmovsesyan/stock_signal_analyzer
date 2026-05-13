#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  Stock Signal Analyzer — Интерактивный деплой и управление
#
#  Единая точка входа для:
#  - Первичной установки (Docker или systemd)
#  - Настройки API ключей
#  - Запуска/остановки/перезапуска
#  - Мониторинга и диагностики
#  - Масштабирования
#  - Обновления
#
#  Использование:
#    chmod +x scripts/deploy.sh
#    ./scripts/deploy.sh
#
#  Или напрямую:
#    ./scripts/deploy.sh install
#    ./scripts/deploy.sh status
#    ./scripts/deploy.sh logs
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

info()    { echo -e "${BLUE}[ℹ]${NC} $1"; }
ok()      { echo -e "${GREEN}[✓]${NC} $1"; }
warn()    { echo -e "${YELLOW}[⚠]${NC} $1"; }
fail()    { echo -e "${RED}[✗]${NC} $1"; }
header()  { echo -e "\n${BOLD}${CYAN}═══ $1 ═══${NC}\n"; }

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"

# ── Утилиты ──────────────────────────────────────────────────────────

ask_input() {
    local question="$1" default="${2:-}"
    local prompt="  ${CYAN}?${NC} $question"
    if [ -n "$default" ]; then prompt="$prompt [${default}]"; fi
    read -r -p "$(echo -e "$prompt: ")" answer
    echo "${answer:-$default}"
}

# Версия ask_input которая не принимает пустой ответ
ask_input_required() {
    local question="$1" default="${2:-}"
    local prompt="  ${CYAN}?${NC} $question"
    if [ -n "$default" ]; then prompt="$prompt [${default}]"; fi
    while true; do
        read -r -p "$(echo -e "$prompt: ")" answer
        answer="${answer:-$default}"
        if [ -n "$answer" ]; then
            echo "$answer"
            return
        fi
        echo -e "    ${RED}Это поле обязательно. Введите значение.${NC}"
    done
}

ask_secret() {
    local question="$1" default="${2:-}"
    local prompt="  ${CYAN}?${NC} $question"
    if [ -n "$default" ]; then prompt="$prompt [****]"; fi
    read -r -s -p "$(echo -e "$prompt: ")" answer
    echo ""
    echo "${answer:-$default}"
}

ask_yes_no() {
    local question="$1" default="${2:-y}"
    local prompt
    if [[ "$default" == "y" ]]; then prompt="[Y/n]"; else prompt="[y/N]"; fi
    read -r -p "$(echo -e "  ${CYAN}?${NC} $question $prompt: ")" answer
    answer="${answer:-$default}"
    [[ "$answer" =~ ^[Yy] ]]
}

ask_choice() {
    local question="$1"
    shift
    local options=("$@")
    echo -e "\n  ${CYAN}?${NC} $question"
    for i in "${!options[@]}"; do
        echo "    $((i+1))) ${options[$i]}"
    done
    while true; do
        read -r -p "    Выбор [1-${#options[@]}]: " choice
        if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#options[@]} )); then
            return $((choice - 1))
            break
        fi
        echo "    Введите число от 1 до ${#options[@]}"
    done
}

check_docker() {
    command -v docker &>/dev/null && docker compose version &>/dev/null 2>&1
}

is_running() {
    source "$ENV_FILE" 2>/dev/null || true
    if echo "${DATABASE_URL:-}" | grep -q "postgres:"; then
        docker compose ps --status running 2>/dev/null | grep -q "stock-signal" 2>/dev/null
    else
        systemctl is-active --quiet "stock-signal-bot" 2>/dev/null || pgrep -f "telegram_bot.py" &>/dev/null
    fi
}

# ═══════════════════════════════════════════════════════════════════════
#  НАСТРОЙКА КЛЮЧЕЙ
# ═══════════════════════════════════════════════════════════════════════

do_configure() {
    header "Настройка API ключей и параметров"

    # Загрузить существующие
    local cur_tg="" cur_pg="" cur_fh="" cur_tk="" cur_pgpass=""
    if [ -f "$ENV_FILE" ]; then
        cur_tg=$(grep -oP '(?<=^TELEGRAM_BOT_TOKEN=).+' "$ENV_FILE" 2>/dev/null || true)
        cur_pg=$(grep -oP '(?<=^POLYGON_API_KEY=).+' "$ENV_FILE" 2>/dev/null || true)
        cur_fh=$(grep -oP '(?<=^FINNHUB_API_KEY=).+' "$ENV_FILE" 2>/dev/null || true)
        cur_tk=$(grep -oP '(?<=^TINKOFF_INVEST_TOKEN=).+' "$ENV_FILE" 2>/dev/null || true)
        cur_pgpass=$(grep -oP '(?<=^POSTGRES_PASSWORD=).+' "$ENV_FILE" 2>/dev/null || true)
    fi

    echo "  Для каждого сервиса ниже указана ссылка для регистрации."
    echo "  Enter = оставить текущее значение / пропустить."
    echo ""

    # ── 1. Telegram Bot Token (обязательно) ──
    echo -e "  ${BOLD}1. Telegram Bot Token${NC} ${RED}(обязательно)${NC}"
    echo "     Как получить:"
    echo "       1) Откройте Telegram, найдите @BotFather"
    echo "       2) Отправьте /newbot"
    echo "       3) Придумайте имя и username для бота"
    echo "       4) Скопируйте токен (формат: 123456:ABC-DEF...)"
    echo "     Ссылка: https://t.me/BotFather"
    echo ""
    local mask_tg=""
    if [ -n "$cur_tg" ]; then mask_tg="${cur_tg:0:10}..."; fi
    local new_tg
    new_tg=$(ask_input_required "Telegram Bot Token" "$mask_tg")
    if [[ "$new_tg" != "$mask_tg" ]] && [ -n "$new_tg" ]; then cur_tg="$new_tg"; fi
    if [ -z "$cur_tg" ]; then
        fail "Telegram Bot Token обязателен!"
        return 1
    fi
    echo ""

    # ── 1b. Admin Chat ID ──
    echo -e "  ${BOLD}1b. Ваш Telegram ID${NC} ${YELLOW}(для управления доступом)${NC}"
    echo "     Зачем: вы будете получать заявки от новых пользователей"
    echo "     и одобрять/отклонять доступ кнопками прямо в Telegram."
    echo "     Как узнать свой ID:"
    echo "       1) Откройте Telegram, найдите @userinfobot"
    echo "       2) Отправьте /start — бот покажет ваш ID (число)"
    echo "     Ссылка: https://t.me/userinfobot"
    echo ""
    local cur_admin=""
    if [ -f "$ENV_FILE" ]; then
        cur_admin=$(grep -oP '(?<=^ADMIN_CHAT_ID=).+' "$ENV_FILE" 2>/dev/null || true)
    fi
    local new_admin
    new_admin=$(ask_input_required "Ваш Telegram ID (число)" "$cur_admin")
    if [ -n "$new_admin" ]; then cur_admin="$new_admin"; fi
    if [ -z "$cur_admin" ]; then
        fail "Admin Chat ID обязателен!"
        return 1
    fi
    echo ""

    # ── 2. Massive (ex-Polygon.io) (рекомендуется) ──
    echo -e "  ${BOLD}2. Massive (Polygon) API Key${NC} ${YELLOW}(рекомендуется)${NC}"
    echo "     Что даёт: котировки US, исторические свечи, новости"
    echo "     Free tier: 5 запросов/мин (достаточно для старта)"
    echo "     Как получить:"
    echo "       1) Зарегистрируйтесь: https://massive.com/dashboard/signup"
    echo "       2) После входа: Dashboard → API Keys → скопируйте ключ"
    echo "     Ссылка: https://massive.com"
    echo ""
    local mask_pg=""
    if [ -n "$cur_pg" ]; then mask_pg="${cur_pg:0:8}..."; fi
    local new_pg
    new_pg=$(ask_input "Polygon API Key (Enter = пропустить)" "$mask_pg")
    if [[ "$new_pg" != "$mask_pg" ]] && [ -n "$new_pg" ]; then cur_pg="$new_pg"; fi
    echo ""

    # ── 3. Finnhub (опционально) ──
    echo -e "  ${BOLD}3. Finnhub API Key${NC} (опционально)"
    echo "     Что даёт: real-time котировки US, новости, аналитика Wall Street"
    echo "     Free tier: 60 запросов/мин"
    echo "     Как получить:"
    echo "       1) Зарегистрируйтесь: https://finnhub.io/register"
    echo "       2) После входа: Dashboard → API Key (сразу на главной)"
    echo "     Ссылка: https://finnhub.io"
    echo ""
    local mask_fh=""
    if [ -n "$cur_fh" ]; then mask_fh="${cur_fh:0:8}..."; fi
    local new_fh
    new_fh=$(ask_input "Finnhub API Key (Enter = пропустить)" "$mask_fh")
    if [[ "$new_fh" != "$mask_fh" ]] && [ -n "$new_fh" ]; then cur_fh="$new_fh"; fi
    echo ""

    # ── 4. T-Bank / Tinkoff Invest (для РФ рынка) ──
    echo -e "  ${BOLD}4. T-Bank Invest Token${NC} (для российского рынка)"
    echo "     Что даёт: real-time котировки Мосбиржи, свечи, VWAP"
    echo "     Как получить:"
    echo "       1) Откройте: https://www.tbank.ru/invest/settings/api/"
    echo "       2) Нажмите «Выпустить токен»"
    echo "       3) Выберите права: только чтение (read-only)"
    echo "       4) Скопируйте токен"
    echo "     Ссылка: https://www.tbank.ru/invest/settings/api/"
    echo "     Документация: https://developer.tbank.ru/invest/"
    echo ""
    local mask_tk=""
    if [ -n "$cur_tk" ]; then mask_tk="${cur_tk:0:10}..."; fi
    local new_tk
    new_tk=$(ask_input "T-Bank Token (Enter = пропустить)" "$mask_tk")
    if [[ "$new_tk" != "$mask_tk" ]] && [ -n "$new_tk" ]; then cur_tk="$new_tk"; fi
    echo ""

    # ── 4b. MAX мессенджер (опционально) ──
    echo -e "  ${BOLD}4b. MAX мессенджер${NC} (дублирование уведомлений)"
    echo "     Что даёт: сильные сигналы приходят и в MAX параллельно с Telegram"
    echo "     Как получить токен:"
    echo "       1) Откройте MAX, найдите @MasterBot"
    echo "       2) Отправьте /newbot, следуйте инструкциям"
    echo "       3) Скопируйте токен"
    echo "     Как узнать chat_id: отправьте /chatid боту в нужном чате"
    echo ""
    local cur_max_token="" cur_max_chat=""
    if [ -f "$ENV_FILE" ]; then
        cur_max_token=$(grep -oP '(?<=^MAX_BOT_TOKEN=).+' "$ENV_FILE" 2>/dev/null || true)
        cur_max_chat=$(grep -oP '(?<=^MAX_CHAT_ID=).+' "$ENV_FILE" 2>/dev/null || true)
    fi
    local mask_max=""
    if [ -n "$cur_max_token" ]; then mask_max="${cur_max_token:0:8}..."; fi
    local new_max_token
    new_max_token=$(ask_input "MAX Bot Token (Enter = пропустить)" "$mask_max")
    if [[ "$new_max_token" != "$mask_max" ]] && [ -n "$new_max_token" ]; then cur_max_token="$new_max_token"; fi
    local new_max_chat
    new_max_chat=$(ask_input "MAX Chat ID (Enter = пропустить)" "$cur_max_chat")
    if [ -n "$new_max_chat" ]; then cur_max_chat="$new_max_chat"; fi
    echo ""

    # ── 5. Режим деплоя ──
    echo -e "  ${BOLD}5. Режим установки${NC}"
    echo ""
    ask_choice "Как установить?" \
        "Docker (рекомендуется: всё в контейнерах)" \
        "systemd (напрямую, как сервис)" \
        "Тестовый запуск (без сервиса)"
    local deploy_choice=$?
    echo ""

    # ── 5b. PostgreSQL password ──
    echo -e "  ${BOLD}5b. Пароль PostgreSQL${NC}"
    echo "     Используется для внутренней БД (не нужна регистрация)."
    echo "     Будет сгенерирован автоматически если оставить пустым."
    echo ""
    cur_pgpass="${cur_pgpass:-ssa_$(openssl rand -hex 8 2>/dev/null || date +%s | tail -c 10)}"
    local new_pgpass
    new_pgpass=$(ask_input "Пароль PostgreSQL" "$cur_pgpass")
    if [ -n "$new_pgpass" ]; then cur_pgpass="$new_pgpass"; fi
    echo ""

    # ── 6. LLM (автоматически) ──
    echo -e "  ${BOLD}6. LLM модель для AI-анализа${NC}"
    echo "     Модель: qwen2.5:1.5b (1.5 GB RAM)"
    echo "     Устанавливается автоматически при первом запуске."
    echo "     Используется для: sentiment анализа новостей + обучения на outcomes."
    echo ""
    local ollama_model="qwen2.5:1.5b"
    local llm_enabled="1"
    ok "LLM: qwen2.5:1.5b (установится автоматически)"

    # ── 7. Интервалы ──
    echo ""
    echo -e "  ${BOLD}7. Автоматизация${NC}"
    echo "     Бот автоматически собирает сигналы по расписанию."
    echo ""
    ask_choice "Интервал автосбора сигналов" \
        "Каждые 4 часа (рекомендуется)" \
        "Каждый час (агрессивно)" \
        "Каждые 8 часов (экономно)" \
        "Отключить"
    local collect_choice=$?
    local collect_values=("14400" "3600" "28800" "0")
    local collect_sec="${collect_values[$collect_choice]}"

    # Хосты для разных режимов
    case $deploy_choice in
        0)  # Docker
            DB_HOST="postgres"
            REDIS_HOST="redis"
            OLLAMA_HOST="http://ollama:11434"
            SIGNAL_LOG="/data/signals/signals.jsonl"
            DATA_DIR="/data"
            ;;
        1)  # systemd
            DB_HOST="localhost"
            REDIS_HOST="localhost"
            OLLAMA_HOST="http://localhost:11434"
            SIGNAL_LOG="/var/lib/stock_signal_analyzer/signals.jsonl"
            DATA_DIR="/var/lib/stock_signal_analyzer"
            ;;
        *)  # test mode
            DB_HOST="localhost"
            REDIS_HOST="localhost"
            OLLAMA_HOST="http://localhost:11434"
            SIGNAL_LOG="$PROJECT_DIR/data/signals.jsonl"
            DATA_DIR="$PROJECT_DIR/data"
            ;;
    esac

    # Записать .env
    cat > "$ENV_FILE" << ENVEOF
# Stock Signal Analyzer — конфигурация
# Сгенерировано: $(date '+%Y-%m-%d %H:%M:%S')

# ── Telegram ──────────────────────────────────
TELEGRAM_BOT_TOKEN=${cur_tg}
ADMIN_CHAT_ID=${cur_admin}

# ── API ключи ─────────────────────────────────
POLYGON_API_KEY=${cur_pg}
FINNHUB_API_KEY=${cur_fh}
TINKOFF_INVEST_TOKEN=${cur_tk}

# ── MAX мессенджер ────────────────────────────
MAX_BOT_TOKEN=${cur_max_token}
MAX_CHAT_ID=${cur_max_chat}
MAX_NOTIFY=1

# ── LLM (Ollama) ─────────────────────────────
OLLAMA_HOST=${OLLAMA_HOST}
OLLAMA_MODEL=${ollama_model}
LLM_SENTIMENT=${llm_enabled}

# ── Database ──────────────────────────────────
POSTGRES_PASSWORD=${cur_pgpass}
DATABASE_URL=postgresql://ssa:${cur_pgpass}@${DB_HOST}:5432/stock_signals

# ── Redis ─────────────────────────────────────
CELERY_BROKER_URL=redis://${REDIS_HOST}:6379/0
CELERY_RESULT_BACKEND=redis://${REDIS_HOST}:6379/1

# ── Автоматизация ─────────────────────────────
COLLECT_INTERVAL_SEC=${collect_sec}
NOTIFY_INTERVAL_SEC=3600
LEARN_INTERVAL_SEC=21600
NOTIFY_MIN_TIER=A
API_RATE_LIMIT_PER_MIN=30

# ── Пути ──────────────────────────────────────
SSA_SIGNAL_LOG=${SIGNAL_LOG}
STOCK_SIGNAL_DATA=${DATA_DIR}
ENVEOF

    chmod 600 "$ENV_FILE"
    ok ".env сохранён"
}

# ═══════════════════════════════════════════════════════════════════════
#  УСТАНОВКА
# ═══════════════════════════════════════════════════════════════════════

do_install() {
    header "Полная установка"

    # Настройка ключей
    if [ ! -f "$ENV_FILE" ]; then
        do_configure
    else
        if ask_yes_no "Перенастроить ключи?" "n"; then
            do_configure
        else
            ok "Используем существующий .env"
        fi
    fi

    source "$ENV_FILE" 2>/dev/null || true
    local db_host=""
    if echo "${DATABASE_URL:-}" | grep -q "postgres:"; then
        db_host="docker"
    elif echo "${DATABASE_URL:-}" | grep -q "localhost"; then
        db_host="local"
    fi

    if [ "$db_host" = "docker" ]; then
        # ── Docker режим ──
        if ! check_docker; then
            fail "Docker не установлен"
            echo ""
            echo "  Установите Docker:"
            echo "    curl -fsSL https://get.docker.com | sh"
            echo "    sudo usermod -aG docker \$USER"
            echo "    # перелогиньтесь"
            echo ""
            if ask_yes_no "Установить Docker автоматически?" "y"; then
                curl -fsSL https://get.docker.com | sh
                sudo usermod -aG docker "$(whoami)" 2>/dev/null || true
                ok "Docker установлен. Перелогиньтесь и запустите скрипт снова."
                return
            fi
            return 1
        fi
        ok "Docker $(docker --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1)"

        header "Сборка образов"
        info "Собираю Docker образы (первый раз ~2-3 мин)..."
        docker compose build --quiet 2>/dev/null || docker compose build
        ok "Образы собраны"

        do_start_docker

        # Инициализация
        header "Инициализация"
        sleep 5

        local model="${OLLAMA_MODEL:-qwen2.5:1.5b}"
        local llm="${LLM_SENTIMENT:-1}"

        if [ "$llm" = "1" ]; then
            info "Загружаю LLM модель $model (1-3 мин)..."
            for i in $(seq 1 20); do
                if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then break; fi
                sleep 3
            done
            docker compose exec -T ollama ollama pull "$model" 2>/dev/null && \
                ok "Модель $model загружена" || warn "Модель не загрузилась (можно позже)"
        fi

        info "Инициализирую базу данных..."
        for i in $(seq 1 10); do
            if docker compose exec -T api python -c "from stock_signal_analyzer.db import init_db; init_db(); print('ok')" 2>/dev/null | grep -q "ok"; then
                ok "База данных готова"
                break
            fi
            sleep 3
        done

    elif [ "$db_host" = "local" ]; then
        # ── Local/systemd режим ──
        check_python || { fail "Python 3 не найден"; return 1; }
        ok "Python $(python3 -V 2>&1 | awk '{print $2}')"

        # venv
        local venv_dir="$PROJECT_DIR/venv"
        local venv_python="$venv_dir/bin/python"
        if [ ! -f "$venv_python" ]; then
            info "Создаю venv..."
            python3 -m venv "$venv_dir"
            ok "venv создан: $venv_dir"
        fi

        info "Устанавливаю зависимости..."
        "$venv_dir/bin/pip" install --upgrade pip setuptools wheel -q
        "$venv_dir/bin/pip" install -r "$PROJECT_DIR/requirements.txt" -q
        ok "Основные зависимости установлены"

        if [ -f "$PROJECT_DIR/requirements-scale.txt" ]; then
            if ask_yes_no "Установить scale-зависимости (PostgreSQL, Redis, Celery)?" "y"; then
                "$venv_dir/bin/pip" install -r "$PROJECT_DIR/requirements-scale.txt" -q \
                    && ok "Scale-зависимости установлены" || warn "Scale-зависимости не установились"
            fi
        fi

        if [ -f "$PROJECT_DIR/requirements-api.txt" ]; then
            "$venv_dir/bin/pip" install -r "$PROJECT_DIR/requirements-api.txt" -q \
                && ok "API-зависимости установлены" || warn "API-зависимости не установились"
        fi

        if [ -f "$PROJECT_DIR/requirements-tbank.txt" ]; then
            if ask_yes_no "Установить SDK Т-Банка?" "y"; then
                "$venv_dir/bin/pip" install -r "$PROJECT_DIR/requirements-tbank.txt" -q \
                    && ok "T-Bank SDK установлен" || warn "T-Bank SDK не установился"
            fi
        fi

        # Данные
        local data_dir="${STOCK_SIGNAL_DATA:-/var/lib/stock_signal_analyzer}"
        mkdir -p "$data_dir" 2>/dev/null || true
        ok "Директория данных: $data_dir"

        # systemd
        if [ "$(id -u)" -eq 0 ] || sudo -n true 2>/dev/null; then
            if ask_yes_no "Установить systemd сервис?" "y"; then
                do_install_systemd "$venv_python"
            fi
        fi

        # Smoke test
        info "Проверка импортов..."
        if "$venv_python" -c "from stock_signal_analyzer.engine import build_report; print('OK')" 2>/dev/null; then
            ok "Импорты работают"
        else
            warn "Ошибка импорта — проверьте зависимости"
        fi

    else
        # ── Test mode ──
        check_python || { fail "Python 3 не найден"; return 1; }
        local venv_dir="$PROJECT_DIR/venv"
        local venv_python="$venv_dir/bin/python"
        if [ ! -f "$venv_python" ]; then
            python3 -m venv "$venv_dir"
            "$venv_dir/bin/pip" install --upgrade pip setuptools wheel -q
            "$venv_dir/bin/pip" install -r "$PROJECT_DIR/requirements.txt" -q
        fi
        ok "Зависимости готовы для тестового запуска"
        info "Запустите бота вручную: $venv_python telegram_bot.py"
    fi

    header "Установка завершена!"
    if [ "$db_host" = "docker" ]; then
        do_status_short
    else
        do_status_short
    fi
}

do_install_systemd() {
    local bot_python="$1"
    header "Systemd сервис"

    local svc_name="stock-signal-bot"
    local svc_file="/etc/systemd/system/${svc_name}.service"
    local data_dir="${STOCK_SIGNAL_DATA:-/var/lib/stock_signal_analyzer}"

    cat > "$svc_file" << SVCEOF
[Unit]
Description=Stock Signal Analyzer Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
Environment="PYTHONUNBUFFERED=1"
ExecStart=$bot_python telegram_bot.py
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

    systemctl daemon-reload
    systemctl enable "$svc_name" 2>/dev/null
    ok "Сервис $svc_name установлен"

    # Outcome tracker timer
    local tracker_svc="/etc/systemd/system/stock-signal-tracker.service"
    local tracker_timer="/etc/systemd/system/stock-signal-tracker.timer"

    cat > "$tracker_svc" << TRKEOF
[Unit]
Description=Stock Signal Analyzer — Outcome Tracker
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$bot_python -m stock_signal_analyzer.outcome_tracker
TRKEOF

    cat > "$tracker_timer" << TMREOF
[Unit]
Description=Stock Signal Analyzer — Outcome Tracker Timer

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

check_python() {
    command -v python3 &>/dev/null || return 1
    return 0
}

do_start_docker() {
    header "Запуск сервисов"
    docker compose up -d
    sleep 3

    info "Жду готовности API..."
    for i in $(seq 1 20); do
        if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
            ok "API готов (http://localhost:8000)"
            break
        fi
        sleep 2
    done
    ok "Все сервисы запущены"
}

# ═══════════════════════════════════════════════════════════════════════
#  ЗАПУСК / ОСТАНОВКА
# ═══════════════════════════════════════════════════════════════════════

do_start() {
    source "$ENV_FILE" 2>/dev/null || true
    if echo "${DATABASE_URL:-}" | grep -q "postgres:"; then
        do_start_docker
    else
        header "Запуск бота"
        local venv_python="$PROJECT_DIR/venv/bin/python"
        if [ ! -f "$venv_python" ]; then
            fail "venv не найден. Запустите установку (пункт 1)."
            return 1
        fi
        local svc_name="stock-signal-bot"
        if systemctl is-enabled "$svc_name" &>/dev/null; then
            systemctl start "$svc_name"
            sleep 2
            if systemctl is-active --quiet "$svc_name"; then
                ok "Бот запущен (systemd)"
            else
                fail "Бот не запустился. journalctl -u $svc_name -e"
            fi
        else
            info "Systemd сервис не установлен. Запускаю вручную..."
            set -a; source "$ENV_FILE" 2>/dev/null; set +a
            nohup "$venv_python" telegram_bot.py > /tmp/stock-signal-bot.log 2>&1 &
            ok "Бот запущен в фоне (PID $!)"
        fi
    fi
}

do_stop() {
    source "$ENV_FILE" 2>/dev/null || true
    if echo "${DATABASE_URL:-}" | grep -q "postgres:"; then
        header "Остановка"
        docker compose down
        ok "Все сервисы остановлены"
    else
        header "Остановка бота"
        local svc_name="stock-signal-bot"
        if systemctl is-active --quiet "$svc_name" 2>/dev/null; then
            systemctl stop "$svc_name"
            ok "Бот остановлен (systemd)"
        else
            pkill -f "telegram_bot.py" 2>/dev/null && ok "Бот остановлен" || warn "Бот не запущен"
        fi
    fi
}

do_restart() {
    source "$ENV_FILE" 2>/dev/null || true
    if echo "${DATABASE_URL:-}" | grep -q "postgres:"; then
        header "Перезапуск"
        docker compose restart
        sleep 3
        ok "Перезапущено"
    else
        do_stop
        sleep 2
        do_start
    fi
}

# ═══════════════════════════════════════════════════════════════════════
#  СТАТУС И МОНИТОРИНГ
# ═══════════════════════════════════════════════════════════════════════

do_status() {
    header "Статус системы"
    do_status_short

    # Health check
    echo ""
    info "Health check:"
    local health
    health=$(curl -sf http://localhost:8000/health/detailed 2>/dev/null || echo '{"error":"API недоступен"}')
    echo "  $health" | python3 -m json.tool 2>/dev/null || echo "  $health"

    # Stats
    echo ""
    info "Статистика:"
    local stats
    stats=$(curl -sf http://localhost:8000/stats 2>/dev/null || echo '{}')
    echo "  $stats" | python3 -m json.tool 2>/dev/null || echo "  $stats"
}

do_status_short() {
    source "$ENV_FILE" 2>/dev/null || true
    echo ""
    if echo "${DATABASE_URL:-}" | grep -q "postgres:"; then
        # Docker mode
        echo -e "  ${BOLD}Сервисы (Docker):${NC}"
        docker compose ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || docker compose ps
        echo ""
        echo "  API:     http://localhost:8000"
        echo "  Ollama:  http://localhost:11434"
        echo "  PgSQL:   localhost:5432"
        echo "  Redis:   localhost:6379"
    else
        # systemd/local mode
        echo -e "  ${BOLD}Сервисы (local/systemd):${NC}"
        local svc_name="stock-signal-bot"
        if systemctl is-active --quiet "$svc_name" 2>/dev/null; then
            ok "Бот $svc_name: активен"
        elif pgrep -f "telegram_bot.py" &>/dev/null; then
            warn "Бот запущен вручную (PID: $(pgrep -f telegram_bot.py))"
        else
            fail "Бот остановлен"
        fi
        echo ""
        echo "  Python:  $PROJECT_DIR/venv/bin/python"
        echo "  Данные:  ${STOCK_SIGNAL_DATA:-$PROJECT_DIR/data}"
    fi
}

do_logs() {
    source "$ENV_FILE" 2>/dev/null || true
    if echo "${DATABASE_URL:-}" | grep -q "postgres:"; then
        # Docker mode
        ask_choice "Логи какого сервиса?" \
            "Telegram бот" \
            "REST API" \
            "Celery Worker" \
            "Ollama LLM" \
            "Все сервисы" \
            "Назад"
        local choice=$?
        case $choice in
            0) docker compose logs -f --tail 50 bot ;;
            1) docker compose logs -f --tail 50 api ;;
            2) docker compose logs -f --tail 50 worker ;;
            3) docker compose logs -f --tail 50 ollama ;;
            4) docker compose logs -f --tail 30 ;;
            5) return ;;
        esac
    else
        # systemd/local mode
        local svc_name="stock-signal-bot"
        if systemctl is-active --quiet "$svc_name" 2>/dev/null; then
            journalctl -u "$svc_name" -f --no-pager
        elif [ -f /tmp/stock-signal-bot.log ]; then
            tail -f /tmp/stock-signal-bot.log
        else
            warn "Бот не запущен, логов нет"
        fi
    fi
}

# ═══════════════════════════════════════════════════════════════════════
#  МАСШТАБИРОВАНИЕ
# ═══════════════════════════════════════════════════════════════════════

do_scale() {
    header "Масштабирование"
    echo "  Текущие workers:"
    docker compose ps worker 2>/dev/null || echo "  нет"
    echo ""

    local count
    count=$(ask_input "Количество workers (1-8)" "4")
    docker compose up -d --scale worker="$count"
    ok "Workers: $count"
}

# ═══════════════════════════════════════════════════════════════════════
#  ОБНОВЛЕНИЕ
# ═══════════════════════════════════════════════════════════════════════

do_update() {
    header "Обновление"

    source "$ENV_FILE" 2>/dev/null || true

    info "Получаю обновления..."
    git pull origin main 2>/dev/null || git pull 2>/dev/null || warn "git pull не удался"

    if echo "${DATABASE_URL:-}" | grep -q "postgres:"; then
        # Docker mode
        info "Пересобираю образы..."
        docker compose build --quiet 2>/dev/null || docker compose build
        info "Перезапускаю с новым кодом..."
        docker compose up -d
        sleep 3
        ok "Обновление завершено (Docker)"
    else
        # systemd/local mode
        local venv_python="$PROJECT_DIR/venv/bin/python"
        if [ -f "$venv_python" ]; then
            info "Обновляю зависимости..."
            "$venv_python" -m pip install --upgrade -r "$PROJECT_DIR/requirements.txt" -q
            ok "Зависимости обновлены"
        fi
        info "Перезапускаю бота..."
        do_restart
        ok "Обновление завершено (systemd/local)"
    fi
    do_status_short
}

# ═══════════════════════════════════════════════════════════════════════
#  ОБНОВЛЕНИЕ ЗАВИСИМОСТЕЙ
# ═══════════════════════════════════════════════════════════════════════

do_update_deps() {
    header "Обновление зависимостей"

    source "$ENV_FILE" 2>/dev/null || true

    if echo "${DATABASE_URL:-}" | grep -q "postgres:"; then
        # Docker mode
        info "Обновляю pip пакеты в контейнерах..."
        docker compose exec -T api pip install --upgrade pip -q
        for reqfile in requirements.txt requirements-scale.txt requirements-api.txt requirements-dev.txt; do
            if [ -f "$PROJECT_DIR/$reqfile" ]; then
                info "Обновляю $reqfile..."
                docker compose exec -T api pip install --upgrade -r "/app/$reqfile" \
                    && ok "$reqfile обновлён" || warn "$reqfile не обновился"
            fi
        done
        info "Перезапускаю контейнеры..."
        docker compose restart
        ok "Docker-зависимости обновлены"
    else
        # systemd/local mode
        local venv_python="$PROJECT_DIR/venv/bin/python"
        if [ ! -f "$venv_python" ]; then
            fail "venv не найден. Сначала выполните установку."
            return 1
        fi

        "$venv_python" -m pip install --upgrade pip setuptools wheel -q

        info "Обновляю основные зависимости..."
        "$venv_python" -m pip install --upgrade -r "$PROJECT_DIR/requirements.txt" -q \
            && ok "Основные зависимости обновлены" || warn "Не обновились"

        if [ -f "$PROJECT_DIR/requirements-scale.txt" ]; then
            if ask_yes_no "Обновить scale-зависимости (PostgreSQL, Redis, Celery)?" "n"; then
                "$venv_python" -m pip install --upgrade -r "$PROJECT_DIR/requirements-scale.txt" -q \
                    && ok "Scale обновлены" || warn "Scale не обновились"
            fi
        fi

        if [ -f "$PROJECT_DIR/requirements-api.txt" ]; then
            if ask_yes_no "Обновить API-зависимости (FastAPI, uvicorn)?" "n"; then
                "$venv_python" -m pip install --upgrade -r "$PROJECT_DIR/requirements-api.txt" -q \
                    && ok "API обновлены" || warn "API не обновились"
            fi
        fi

        if [ -f "$PROJECT_DIR/requirements-tbank.txt" ]; then
            if ask_yes_no "Обновить T-Bank SDK?" "n"; then
                "$venv_python" -m pip install --upgrade -r "$PROJECT_DIR/requirements-tbank.txt" -q \
                    && ok "T-Bank SDK обновлён" || warn "T-Bank SDK не обновился"
            fi
        fi

        if [ -f "$PROJECT_DIR/requirements-dev.txt" ]; then
            if ask_yes_no "Обновить dev-зависимости (pytest, black, flake8)?" "n"; then
                "$venv_python" -m pip install --upgrade -r "$PROJECT_DIR/requirements-dev.txt" -q \
                    && ok "Dev обновлены" || warn "Dev не обновились"
            fi
        fi

        if ask_yes_no "Перезапустить бота?" "y"; then
            do_restart
        fi
    fi
}

# ═══════════════════════════════════════════════════════════════════════
#  БЭКТЕСТ
# ═══════════════════════════════════════════════════════════════════════

do_backtest() {
    header "Бэктестирование"

    ask_choice "Режим бэктеста" \
        "v1 — по сохранённым сигналам (signals.jsonl)" \
        "v2 — candle replay (полная эмуляция)" \
        "Назад"
    local choice=$?

    case $choice in
        0)
            local log_path
            log_path=$(ask_input "Путь к signals.jsonl" "data/signals.jsonl")
            local tier
            tier=$(ask_input "Минимальный класс (A/B/C)" "B")
            docker compose exec -T api python tools/backtest.py "$log_path" --min-tier "$tier"
            ;;
        1)
            local symbols
            symbols=$(ask_input "Тикеры через пробел" "AAPL MSFT GOOGL")
            local days
            days=$(ask_input "Период (дней)" "180")
            docker compose exec -T api python tools/backtest_v2.py --symbols $symbols --days "$days"
            ;;
        2) return ;;
    esac
}

# ═══════════════════════════════════════════════════════════════════════
#  ОБУЧЕНИЕ
# ═══════════════════════════════════════════════════════════════════════

do_learning() {
    header "Обучение"

    ask_choice "Действие" \
        "Запустить обучение сейчас" \
        "Показать отчёт" \
        "Показать IC scores" \
        "Назад"
    local choice=$?

    case $choice in
        0)
            info "Запускаю цикл обучения..."
            docker compose exec -T api python -m stock_signal_analyzer.llm_learning --force
            ;;
        1)
            curl -sf http://localhost:8000/learning/report 2>/dev/null | python3 -m json.tool 2>/dev/null || \
                echo "API недоступен"
            ;;
        2)
            docker compose exec -T api python -c "
from stock_signal_analyzer.adaptive_weights import compute_adaptive_weights
aw = compute_adaptive_weights()
print(aw.detail)
if aw.ic_scores:
    for k, v in sorted(aw.ic_scores.items(), key=lambda x: -abs(x[1])):
        print(f'  {k}: IC={v:+.3f}')
" 2>/dev/null || echo "Недоступно"
            ;;
        3) return ;;
    esac
}

# ═══════════════════════════════════════════════════════════════════════
#  УДАЛЕНИЕ
# ═══════════════════════════════════════════════════════════════════════

do_uninstall() {
    header "Удаление"
    warn "Это остановит все сервисы и удалит контейнеры."

    if ! ask_yes_no "Продолжить?" "n"; then return; fi

    docker compose down -v 2>/dev/null || docker compose down
    ok "Контейнеры и volumes удалены"

    if ask_yes_no "Удалить .env?" "n"; then
        rm -f "$ENV_FILE"
        ok ".env удалён"
    fi
}

# ═══════════════════════════════════════════════════════════════════════
#  ГЛАВНОЕ МЕНЮ
# ═══════════════════════════════════════════════════════════════════════

show_banner() {
    echo -e "${BOLD}${GREEN}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║        Stock Signal Analyzer — Deploy & Manage              ║"
    echo "║        AI-powered multi-factor trading signals              ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

main_menu() {
    while true; do
        clear 2>/dev/null || true
        show_banner

        # Quick status
        if is_running 2>/dev/null; then
            echo -e "  Статус: ${GREEN}● работает${NC}"
        elif [ -f "$ENV_FILE" ]; then
            echo -e "  Статус: ${YELLOW}○ остановлен${NC}"
        else
            echo -e "  Статус: ${RED}○ не установлен${NC}"
        fi
        echo ""

        echo "  ── Установка и настройка ──────────────"
        echo "    1) 🚀 Полная установка (с нуля)"
        echo "    2) 🔑 Настроить API ключи"
        echo "    3) 📦 Обновить (git pull + rebuild)"
        echo ""
        echo "  ── Управление ─────────────────────────"
        echo "    4) ▶️  Запустить"
        echo "    5) ⏹️  Остановить"
        echo "    6) 🔄 Перезапустить"
        echo "    7) 📊 Масштабировать workers"
        echo "    8) 📦 Обновить зависимости"
        echo ""
        echo "  ── Мониторинг ─────────────────────────"
        echo "    9) 📋 Статус и health check"
        echo "   10) 📜 Логи"
        echo ""
        echo "  ── Аналитика ─────────────────────────"
        echo "   11) 🧠 Обучение (learning)"
        echo "   12) 📈 Бэктест"
        echo ""
        echo "  ── Прочее ─────────────────────────────"
        echo "   13) 🗑️  Удалить всё"
        echo "    0) Выход"
        echo ""

        read -r -p "$(echo -e "  ${CYAN}▶${NC}") Выберите: " choice

        case "$choice" in
            1)  do_install ;;
            2)  do_configure ;;
            3)  do_update ;;
            4)  do_start ;;
            5)  do_stop ;;
            6)  do_restart ;;
            7)  do_scale ;;
            8)  do_update_deps ;;
            9)  do_status ;;
            10) do_logs ;;
            11) do_learning ;;
            12) do_backtest ;;
            13) do_uninstall ;;
            0|q|exit) echo ""; ok "До встречи!"; exit 0 ;;
            *)  warn "Неизвестный пункт" ;;
        esac

        echo ""
        read -r -p "  Нажмите Enter..." _
    done
}

# ═══════════════════════════════════════════════════════════════════════
#  ТОЧКА ВХОДА
# ═══════════════════════════════════════════════════════════════════════

case "${1:-}" in
    install)    do_install ;;
    configure)  do_configure ;;
    start)      do_start ;;
    stop)       do_stop ;;
    restart)    do_restart ;;
    status)     do_status ;;
    logs)       do_logs ;;
    scale)      do_scale ;;
    update-deps) do_update_deps ;;
    update)     do_update ;;
    learning)   do_learning ;;
    backtest)   do_backtest ;;
    uninstall)  do_uninstall ;;
    help|--help|-h)
        echo "Stock Signal Analyzer — Deploy & Manage"
        echo ""
        echo "Использование: ./scripts/deploy.sh [команда]"
        echo ""
        echo "Команды:"
        echo "  install     Полная установка"
        echo "  configure   Настроить ключи"
        echo "  start       Запустить сервисы"
        echo "  stop        Остановить"
        echo "  restart     Перезапустить"
        echo "  status      Статус и health"
        echo "  logs        Логи"
        echo "  scale       Масштабировать workers"
        echo "  update-deps Обновить зависимости"
        echo "  update      Обновить код"
        echo "  learning    Управление обучением"
        echo "  backtest    Бэктестирование"
        echo "  uninstall   Удалить"
        echo ""
        echo "Без аргументов — интерактивное меню."
        ;;
    "")         main_menu ;;
    *)          fail "Неизвестная команда: $1. Используйте --help"; exit 1 ;;
esac
