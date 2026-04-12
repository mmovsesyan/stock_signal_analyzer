#!/usr/bin/env bash
# Интерактивная установка stock_signal_analyzer на Linux.
# Запуск:  chmod +x scripts/install_linux.sh && ./scripts/install_linux.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "=========================================="
echo "  Stock Signal Analyzer — установка (Linux)"
echo "  Каталог проекта: $ROOT"
echo "=========================================="
echo

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Ошибка: не найдено «$1». Установите и повторите."; exit 1; }
}

need_cmd python3
need_cmd pip3

PYMAJ=$(python3 -c 'import sys; print(sys.version_info[0])')
PYMIN=$(python3 -c 'import sys; print(sys.version_info[1])')
if (( PYMAJ < 3 || (PYMAJ == 3 && PYMIN < 9) )); then
  echo "Нужен Python 3.9+, сейчас: $(python3 -V)"
  exit 1
fi
echo "Python: $(python3 -V) — ок."

PIP="pip3"

read -r -p "Создать виртуальное окружение .venv здесь? [Y/n]: " ans
ans=${ans:-Y}
if [[ "$ans" =~ ^[Yy]$ || "$ans" == "" ]]; then
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -U pip setuptools wheel
  PIP="pip"
  echo "Виртуальное окружение: $ROOT/.venv"
  echo "Активация в новой сессии:  source $ROOT/.venv/bin/activate"
else
  echo "Используем системный pip3 (без venv)."
fi

echo
echo "Устанавливаю зависимости из requirements.txt ..."
"$PIP" install -r "$ROOT/requirements.txt"

echo
read -r -p "Установить SDK Т-Инвестиции (Т-Банк, requirements-tbank.txt) для котировок .ME? [y/N]: " tink
tink=${tink:-N}
if [[ "$tink" =~ ^[Yy]$ ]]; then
  echo "Пробуем официальный SDK Т-Банка (requirements-tbank.txt) ..."
  if "$PIP" install -r "$ROOT/requirements-tbank.txt"; then
    echo "Готово (t-tech-investments). Токен: $ROOT/docs/TINKOFF.md"
  else
    echo "Установка с индекса Т-Банка не удалась — ставим tinkoff-investments с PyPI ..."
    "$PIP" install 'tinkoff-investments>=0.2.0b118' || true
    echo "См. также: https://developer.tbank.ru/invest/sdk/python_sdk/faq_python/"
  fi
else
  echo "Пропуск. Позже: pip install -r requirements-tbank.txt или pip install tinkoff-investments"
fi

echo
if [[ ! -f .env ]]; then
  read -r -p "Создать файл .env из .env.example? [Y/n]: " envc
  envc=${envc:-Y}
  if [[ "$envc" =~ ^[Yy]$ || "$envc" == "" ]]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
    echo "Создан $ROOT/.env — отредактируйте и укажите токены."
    if command -v "${EDITOR:-nano}" >/dev/null 2>&1; then
      "${EDITOR:-nano}" "$ROOT/.env" || true
    fi
  fi
else
  echo "Файл .env уже существует — не перезаписываю."
fi

echo
echo "=========================================="
echo "  Готово."
echo "=========================================="
echo
echo "Проверка:"
echo "  cd \"$ROOT\""
if [[ -f .venv/bin/activate ]]; then
  echo "  source .venv/bin/activate"
fi
echo "  python3 main.py SPY"
echo
echo "Telegram-бот:"
echo "  Задайте TELEGRAM_BOT_TOKEN в .env или: export TELEGRAM_BOT_TOKEN=\"...\""
echo "  python3 telegram_bot.py"
echo
echo "Тинькофф (токен для котировок через API, не торговые сигналы брокера):"
echo "  less \"$ROOT/docs/TINKOFF.md\""
echo
