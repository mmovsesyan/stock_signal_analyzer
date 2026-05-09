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
    docker compose ps --status running 2>/dev/null | grep -q "stock-signal" 2>/dev/null
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
    echo ""

    # ── 2. Polygon.io (рекомендуется) ──
    echo -e "  ${BOLD}2. Polygon.io API Key${NC} ${YELLOW}(рекомендуется)${NC}"
    echo "     Что даёт: котировки US, исторические свечи, новости"
    echo "     Free tier: 5 запросов/мин (достаточно для старта)"
    echo "     Как получить:"
    echo "       1) Зарегистрируйтесь: https://polygon.io/dashboard/signup"
    echo "       2) После входа: Dashboard → API Keys → скопируйте ключ"
    echo "     Ссылка: https://polygon.io"
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

    # ── 5. PostgreSQL password ──
    echo -e "  ${BOLD}5. Пароль PostgreSQL${NC}"
    echo "     Используется для внутренней БД (не нужна регистрация)."
    echo "     Будет сгенерирован автоматически если оставить пустым."
    echo ""
    cur_pgpass="${cur_pgpass:-ssa_$(openssl rand -hex 8 2>/dev/null || date +%s | tail -c 10)}"
    local new_pgpass
    new_pgpass=$(ask_input "Пароль PostgreSQL" "$cur_pgpass")
    if [ -n "$new_pgpass" ]; then cur_pgpass="$new_pgpass"; fi
    echo ""

    # ── 6. Модель Ollama ──
    echo -e "  ${BOLD}6. LLM модель для AI-анализа${NC}"
    echo "     Ollama устанавливается автоматически (регистрация не нужна)."
    echo "     Модель скачивается при первом запуске (~1-2 мин)."
    echo "     Используется для: sentiment анализа новостей + обучения на outcomes."
    echo "     Подробнее: https://ollama.com"
    echo ""
    ask_choice "Выберите модель" \
        "qwen2.5:1.5b — 1.5 GB RAM, быстрая, хороший JSON (рекомендуется)" \
        "gemma2:2b — 2 GB RAM, Google, качественнее" \
        "phi3:mini — 2.3 GB RAM, Microsoft, лучший reasoning" \
        "Без LLM — только VADER sentiment (экономия RAM)"
    local model_choice=$?
    local models=("qwen2.5:1.5b" "gemma2:2b" "phi3:mini" "none")
    local ollama_model="${models[$model_choice]}"
    local llm_enabled="1"
    if [ "$ollama_model" = "none" ]; then llm_enabled="0"; ollama_model="qwen2.5:1.5b"; fi

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

# ── LLM (Ollama) ─────────────────────────────
OLLAMA_HOST=http://ollama:11434
OLLAMA_MODEL=${ollama_model}
LLM_SENTIMENT=${llm_enabled}

# ── Database ──────────────────────────────────
POSTGRES_PASSWORD=${cur_pgpass}
DATABASE_URL=postgresql://ssa:${cur_pgpass}@postgres:5432/stock_signals

# ── Redis ─────────────────────────────────────
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1

# ── Автоматизация ─────────────────────────────
COLLECT_INTERVAL_SEC=${collect_sec}
NOTIFY_INTERVAL_SEC=3600
LEARN_INTERVAL_SEC=21600
NOTIFY_MIN_TIER=A
API_RATE_LIMIT_PER_MIN=30

# ── Пути ──────────────────────────────────────
SSA_SIGNAL_LOG=/data/signals/signals.jsonl
STOCK_SIGNAL_DATA=/data
ENVEOF

    chmod 600 "$ENV_FILE"
    ok ".env сохранён"
}

# ═══════════════════════════════════════════════════════════════════════
#  УСТАНОВКА
# ═══════════════════════════════════════════════════════════════════════

do_install() {
    header "Полная установка"

    # Проверка Docker
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

    # Сборка
    header "Сборка образов"
    info "Собираю Docker образы (первый раз ~2-3 мин)..."
    docker compose build --quiet 2>/dev/null || docker compose build
    ok "Образы собраны"

    # Запуск
    do_start

    # Инициализация
    header "Инициализация"
    sleep 5

    # Ollama модель
    source "$ENV_FILE" 2>/dev/null || true
    local model="${OLLAMA_MODEL:-qwen2.5:1.5b}"
    local llm="${LLM_SENTIMENT:-1}"

    if [ "$llm" = "1" ]; then
        info "Загружаю LLM модель $model (1-3 мин)..."
        # Ждём Ollama
        for i in $(seq 1 20); do
            if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then break; fi
            sleep 3
        done
        docker compose exec -T ollama ollama pull "$model" 2>/dev/null && \
            ok "Модель $model загружена" || warn "Модель не загрузилась (можно позже)"
    fi

    # БД
    info "Инициализирую базу данных..."
    for i in $(seq 1 10); do
        if docker compose exec -T api python -c "from stock_signal_analyzer.db import init_db; init_db(); print('ok')" 2>/dev/null | grep -q "ok"; then
            ok "База данных готова"
            break
        fi
        sleep 3
    done

    # Миграция
    if [ -f "$PROJECT_DIR/data/signals.jsonl" ]; then
        if ask_yes_no "Мигрировать существующие данные в PostgreSQL?" "y"; then
            docker compose exec -T api python scripts/migrate_to_db.py 2>/dev/null && \
                ok "Данные мигрированы" || warn "Миграция пропущена"
        fi
    fi

    # Итог
    header "Установка завершена!"
    do_status_short
}

# ═══════════════════════════════════════════════════════════════════════
#  ЗАПУСК / ОСТАНОВКА
# ═══════════════════════════════════════════════════════════════════════

do_start() {
    header "Запуск сервисов"
    docker compose up -d
    sleep 3

    # Ждём API
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

do_stop() {
    header "Остановка"
    docker compose down
    ok "Все сервисы остановлены"
}

do_restart() {
    header "Перезапуск"
    docker compose restart
    sleep 3
    ok "Перезапущено"
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
    echo ""
    echo -e "  ${BOLD}Сервисы:${NC}"
    docker compose ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || docker compose ps
    echo ""
    echo "  API:     http://localhost:8000"
    echo "  Ollama:  http://localhost:11434"
    echo "  PgSQL:   localhost:5432"
    echo "  Redis:   localhost:6379"
}

do_logs() {
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

    info "Получаю обновления..."
    git pull origin main 2>/dev/null || git pull 2>/dev/null || warn "git pull не удался"

    info "Пересобираю образы..."
    docker compose build --quiet 2>/dev/null || docker compose build

    info "Перезапускаю с новым кодом..."
    docker compose up -d
    sleep 3

    ok "Обновление завершено"
    do_status_short
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
        echo ""
        echo "  ── Мониторинг ─────────────────────────"
        echo "    8) 📋 Статус и health check"
        echo "    9) 📜 Логи"
        echo ""
        echo "  ── Аналитика ─────────────────────────"
        echo "   10) 🧠 Обучение (learning)"
        echo "   11) 📈 Бэктест"
        echo ""
        echo "  ── Прочее ─────────────────────────────"
        echo "   12) 🗑️  Удалить всё"
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
            8)  do_status ;;
            9)  do_logs ;;
            10) do_learning ;;
            11) do_backtest ;;
            12) do_uninstall ;;
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
