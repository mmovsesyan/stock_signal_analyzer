#!/bin/bash
# Запуск бота вручную (для отладки)

cd "$(dirname "$0")"

echo "🚀 Запуск Stock Signal Analyzer..."
echo ""

# Проверить venv
if [ ! -d "venv" ]; then
    echo "❌ venv не найден. Запустите сначала: sudo ./install.sh"
    exit 1
fi

# Активировать venv
source venv/bin/activate

# Проверить .env
if [ ! -f ".env" ]; then
    echo "❌ .env не найден. Запустите сначала: sudo ./install.sh"
    exit 1
fi

# Запустить бота
echo "✓ Запуск бота..."
echo "Нажмите Ctrl+C для остановки"
echo ""

python telegram_bot.py
