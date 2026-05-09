#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  Установка Ollama + модель qwen2.5:1.5b на Ubuntu сервере.
#
#  Модель qwen2.5:1.5b:
#    - Размер на диске: ~1.5 GB
#    - RAM при инференсе: ~2-3 GB
#    - Хорошо справляется с классификацией sentiment
#    - Поддерживает structured JSON output
#
#  Использование:
#    chmod +x scripts/install_ollama.sh
#    sudo ./scripts/install_ollama.sh
#
#  Или с другой моделью:
#    OLLAMA_MODEL=gemma2:2b sudo ./scripts/install_ollama.sh
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${BLUE}[ℹ]${NC} $1"; }
ok()    { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[⚠]${NC} $1"; }
fail()  { echo -e "${RED}[✗]${NC} $1"; }

MODEL="${OLLAMA_MODEL:-qwen2.5:1.5b}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

echo -e "${GREEN}"
echo "╔══════════════════════════════════════════════════════════╗"
echo "║   Установка Ollama + LLM Sentiment (Ubuntu)             ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo "  Модель: $MODEL"
echo "  RAM: ~2-3 GB при инференсе"
echo "  Сервер: Ubuntu $(lsb_release -rs 2>/dev/null || echo 'unknown')"
echo ""

# ── 1. Установка Ollama ──────────────────────────────────────────────────────

if command -v ollama &>/dev/null; then
    ok "Ollama уже установлен"
else
    info "Устанавливаю Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    if command -v ollama &>/dev/null; then
        ok "Ollama установлен"
    else
        fail "Не удалось установить Ollama"
        exit 1
    fi
fi

# ── 2. Systemd сервис для Ollama ─────────────────────────────────────────────

if systemctl is-active --quiet ollama 2>/dev/null; then
    ok "Ollama сервис уже запущен"
else
    info "Запускаю Ollama сервис..."
    systemctl enable ollama 2>/dev/null || true
    systemctl start ollama 2>/dev/null || true
    sleep 3
    if systemctl is-active --quiet ollama 2>/dev/null; then
        ok "Ollama сервис запущен"
    else
        # Fallback: запуск вручную
        warn "Systemd не запустил Ollama, пробую вручную..."
        nohup ollama serve &>/var/log/ollama.log &
        sleep 3
    fi
fi

# Проверка доступности
check_ollama() {
    curl -s http://localhost:11434/api/tags >/dev/null 2>&1
}

RETRIES=10
for i in $(seq 1 $RETRIES); do
    if check_ollama; then
        ok "Ollama API доступен (http://localhost:11434)"
        break
    fi
    if [ "$i" -eq "$RETRIES" ]; then
        fail "Ollama API не отвечает после $RETRIES попыток"
        exit 1
    fi
    sleep 2
done

# ── 3. Загрузка модели ───────────────────────────────────────────────────────

MODEL_BASE="${MODEL%%:*}"
if ollama list 2>/dev/null | grep -q "$MODEL_BASE"; then
    ok "Модель $MODEL уже загружена"
else
    info "Загружаю модель $MODEL (~1.5 GB, может занять 2-5 минут)..."
    ollama pull "$MODEL"
    if [ $? -eq 0 ]; then
        ok "Модель $MODEL загружена"
    else
        fail "Не удалось загрузить модель"
        exit 1
    fi
fi

# ── 4. Тест модели ───────────────────────────────────────────────────────────

info "Тестирую модель (первый запуск может быть медленным)..."
TEST_RESPONSE=$(curl -s --max-time 60 http://localhost:11434/api/chat -d "{
  \"model\": \"$MODEL\",
  \"messages\": [{\"role\": \"user\", \"content\": \"Reply with JSON: {\\\"status\\\": \\\"ok\\\"}\"}],
  \"stream\": false,
  \"format\": \"json\",
  \"options\": {\"num_predict\": 20}
}" 2>/dev/null || echo "")

if echo "$TEST_RESPONSE" | grep -q "message"; then
    ok "Модель отвечает корректно"
else
    warn "Модель не ответила за 60с (возможно, первая загрузка в RAM). Повторите позже."
fi

# ── 5. Настройка .env ─────────────────────────────────────────────────────────

if [ -f "$ENV_FILE" ]; then
    if grep -q "^OLLAMA_MODEL=" "$ENV_FILE"; then
        ok ".env уже содержит OLLAMA_MODEL"
    else
        echo "" >> "$ENV_FILE"
        echo "# --- LLM Sentiment (Ollama) ---" >> "$ENV_FILE"
        echo "OLLAMA_HOST=http://localhost:11434" >> "$ENV_FILE"
        echo "OLLAMA_MODEL=$MODEL" >> "$ENV_FILE"
        echo "LLM_SENTIMENT=1" >> "$ENV_FILE"
        ok "Добавлено в .env"
    fi
else
    warn ".env не найден. Создайте из .env.example и добавьте:"
    echo "  OLLAMA_HOST=http://localhost:11434"
    echo "  OLLAMA_MODEL=$MODEL"
    echo "  LLM_SENTIMENT=1"
fi

# ── 6. Проверка из Python ─────────────────────────────────────────────────────

VENV_PYTHON="$PROJECT_DIR/venv/bin/python"
if [ -f "$VENV_PYTHON" ]; then
    info "Проверяю Python интеграцию..."
    "$VENV_PYTHON" -c "
import sys
sys.path.insert(0, '$PROJECT_DIR')
from stock_signal_analyzer.llm_sentiment import ollama_available, ollama_model_ready
avail = ollama_available()
ready = ollama_model_ready()
print(f'  Ollama available: {avail}')
print(f'  Model ready: {ready}')
if avail and ready:
    print('  ✓ LLM sentiment готов к работе')
else:
    print('  ⚠ Проверьте настройки')
" 2>/dev/null && ok "Python интеграция работает" || warn "Python проверка не прошла"
fi

# ── 7. Мониторинг RAM ─────────────────────────────────────────────────────────

info "Текущее использование RAM:"
free -h | head -2
echo ""

# ── Итог ──────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}═══ Установка завершена ═══${NC}"
echo ""
echo "  Ollama:    http://localhost:11434"
echo "  Модель:    $MODEL"
echo "  Сервис:    systemctl status ollama"
echo "  RAM:       ~2-3 GB при инференсе"
echo ""
echo "  Управление:"
echo "    systemctl restart ollama    # перезапуск"
echo "    ollama list                 # список моделей"
echo "    ollama rm $MODEL            # удалить модель"
echo ""
echo "  Для отключения LLM: установите LLM_SENTIMENT=0 в .env"
echo ""
echo "  Альтернативные модели (2-4 GB RAM):"
echo "    qwen2.5:1.5b  — 1.5 GB, быстрая, хороший JSON"
echo "    gemma2:2b      — 2.0 GB, Google, качественная"
echo "    phi3:mini      — 2.3 GB, Microsoft, reasoning"
echo ""
