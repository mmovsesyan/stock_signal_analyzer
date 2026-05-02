#!/usr/bin/env python3
"""
Интерактивная установка Stock Signal Analyzer для Ubuntu сервера.

Автоматически определяет ОС и запускается только на Linux.
На macOS показывает сообщение, что установка не требуется.

Использование:
    python setup.py
"""

import os
import platform
import sys
from pathlib import Path


def detect_os():
    """Определить операционную систему."""
    system = platform.system()
    return system


def print_header(text):
    """Печать заголовка."""
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70)


def print_box(lines):
    """Печать текста в рамке."""
    width = 68
    print("\n╔" + "═" * width + "╗")
    for line in lines:
        padding = width - len(line)
        print(f"║ {line}{' ' * padding} ║")
    print("╚" + "═" * width + "╝\n")


def ask_question(question, default=None, options=None):
    """Задать вопрос пользователю."""
    if options:
        print(f"\n{question}")
        for i, option in enumerate(options, 1):
            print(f"  {i}) {option}")
        while True:
            answer = input(f"Ваш выбор [1-{len(options)}]: ").strip()
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return int(answer) - 1
            print(f"Пожалуйста, введите число от 1 до {len(options)}")
    else:
        prompt = f"\n{question}"
        if default:
            prompt += f"\n  По умолчанию: {default}"
        prompt += "\n  Ваш выбор [Enter = по умолчанию]: "
        answer = input(prompt).strip()
        return answer if answer else default


def ask_yes_no(question, default=True):
    """Задать вопрос да/нет."""
    default_str = "Y/n" if default else "y/N"
    answer = input(f"\n{question} [{default_str}]: ").strip().lower()
    if not answer:
        return default
    return answer in ['y', 'yes', 'да', 'д']


def create_directory(path):
    """Создать директорию с правильными правами."""
    path_obj = Path(path)
    try:
        path_obj.mkdir(parents=True, exist_ok=True)
        print(f"  ✓ Создана директория: {path}")
        return True
    except PermissionError:
        print(f"  ✗ Нет прав для создания: {path}")
        print(f"    Выполните: sudo mkdir -p {path} && sudo chown $USER {path}")
        return False
    except Exception as e:
        print(f"  ✗ Ошибка создания {path}: {e}")
        return False


def save_env_file(config):
    """Сохранить конфигурацию в .env файл."""
    env_file = Path.cwd() / ".env"

    try:
        with open(env_file, 'w') as f:
            f.write("# Stock Signal Analyzer Configuration\n")
            f.write(f"# Generated: {platform.node()} at {os.popen('date').read().strip()}\n\n")

            for key, value in config.items():
                if value:
                    f.write(f"{key}={value}\n")

        print(f"\n  ✓ Конфигурация сохранена: {env_file}")
        return True
    except Exception as e:
        print(f"\n  ✗ Ошибка сохранения .env: {e}")
        return False


def add_to_bashrc(config):
    """Добавить переменные в ~/.bashrc."""
    bashrc = Path.home() / ".bashrc"

    if not bashrc.exists():
        print(f"\n  ⚠️  ~/.bashrc не найден, пропускаем")
        return False

    try:
        # Проверить, есть ли уже наши переменные
        with open(bashrc, 'r') as f:
            content = f.read()

        if "# Stock Signal Analyzer" in content:
            print(f"\n  ℹ️  Переменные уже есть в ~/.bashrc")
            return True

        # Добавить переменные
        with open(bashrc, 'a') as f:
            f.write("\n\n# Stock Signal Analyzer\n")
            for key, value in config.items():
                if value:
                    f.write(f'export {key}="{value}"\n')

        print(f"\n  ✓ Переменные добавлены в ~/.bashrc")
        print(f"    Выполните: source ~/.bashrc")
        return True
    except Exception as e:
        print(f"\n  ✗ Ошибка записи в ~/.bashrc: {e}")
        return False


def verify_installation():
    """Проверить установку."""
    print_header("Проверка установки")

    # Проверка отключена - модули устанавливаются в venv
    print("  ℹ️  Проверка модулей пропущена (используйте venv)")
    print("  Создайте venv: python3 -m venv venv")
    print("  Активируйте: source venv/bin/activate")
    print("  Установите: pip install -r requirements.txt")
    return True


def run_interactive_setup():
    """Запустить интерактивную установку."""
    print_box([
        "Stock Signal Analyzer - Интерактивная установка",
        "",
        "Этот мастер поможет настроить программу для автоматического",
        "сбора торговых сигналов и оценки их прибыльности."
    ])

    config = {}

    # 1. Путь к логу сигналов
    print_header("1/6: Путь к файлу сигналов")
    print("Здесь будут храниться все сгенерированные сигналы.")
    default_signal_log = "/var/lib/stock_signal_analyzer/signals.jsonl"
    signal_log = ask_question(
        "Где хранить файл сигналов?",
        default=default_signal_log
    )
    config['SSA_SIGNAL_LOG'] = signal_log

    # 2. Директория для данных
    print_header("2/6: Директория для данных")
    print("Здесь будут храниться результаты отслеживания (outcomes.jsonl).")
    default_data_dir = "/var/lib/stock_signal_analyzer"
    data_dir = ask_question(
        "Где хранить данные?",
        default=default_data_dir
    )
    config['STOCK_SIGNAL_DATA'] = data_dir

    # 3. Интервал автосбора
    print_header("3/6: Автоматический сбор сигналов")
    print("Программа может автоматически собирать сигналы по расписанию.")
    interval_options = [
        "Каждые 4 часа (рекомендуется, ~6 сигналов/день)",
        "Каждый час (агрессивно, ~24 сигнала/день)",
        "Каждые 8 часов (консервативно, ~3 сигнала/день)",
        "Отключить автосбор (собирать вручную)"
    ]
    interval_values = ["14400", "3600", "28800", "0"]
    interval_choice = ask_question(
        "Как часто собирать сигналы?",
        options=interval_options
    )
    interval = interval_values[interval_choice]
    if interval != "0":
        config['COLLECT_INTERVAL_SEC'] = interval

    # 4. Telegram Bot Token
    print_header("4/6: Telegram Bot (опционально)")
    print("Если у вас есть Telegram бот, он может отправлять уведомления.")
    print("Получить токен: https://t.me/BotFather")
    if ask_yes_no("Настроить Telegram бота?", default=False):
        token = ask_question("Введите Bot Token:")
        if token:
            config['TELEGRAM_BOT_TOKEN'] = token

    # 5. API ключи
    print_header("5/6: API ключи (опционально)")
    print("Для получения данных по акциям.")
    if ask_yes_no("Настроить API ключи?", default=False):
        finnhub = ask_question("Finnhub API Key (для US акций, или Enter для пропуска):")
        if finnhub:
            config['FINNHUB_API_KEY'] = finnhub

        tinkoff = ask_question("Tinkoff/T-Bank Token (для RU акций, или Enter для пропуска):")
        if tinkoff:
            config['TINKOFF_TOKEN'] = tinkoff

    # 6. Создание директорий
    print_header("6/6: Создание директорий")

    dirs_to_create = set()
    if signal_log:
        dirs_to_create.add(str(Path(signal_log).parent))
    if data_dir:
        dirs_to_create.add(data_dir)

    all_created = True
    for dir_path in dirs_to_create:
        if not create_directory(dir_path):
            all_created = False

    # Сохранение конфигурации
    print_header("Сохранение конфигурации")

    save_env_file(config)

    if ask_yes_no("Добавить переменные в ~/.bashrc?", default=True):
        add_to_bashrc(config)

    # Проверка установки
    if not verify_installation():
        print("\n⚠️  Некоторые модули не установлены.")
        print("Выполните: pip install -r requirements.txt")
        return False

    # Итоговая информация
    print_header("✅ Установка завершена!")

    print("\n📋 Конфигурация:")
    for key, value in config.items():
        print(f"  {key}: {value}")

    print("\n🚀 Следующие шаги:")
    print("\n1. Перезагрузить переменные окружения:")
    print("   source ~/.bashrc")

    print("\n2. Проверить установку:")
    print("   python tools/verify_monetization.py")

    if config.get('COLLECT_INTERVAL_SEC'):
        print("\n3. Запустить автосбор сигналов:")
        print("   python telegram_bot.py")
        print("   (Будет собирать сигналы автоматически)")
    else:
        print("\n3. Собрать сигналы вручную:")
        print("   python -c \"from stock_signal_analyzer.engine import build_report; build_report('AAPL')\"")

    print("\n4. Мониторить прогресс:")
    print("   python tools/monitor_signals.py")

    print("\n5. Когда наберется 50+ сигналов, запустить бэктест:")
    print("   python tools/backtest.py $SSA_SIGNAL_LOG --min-tier A")

    print("\n📚 Документация:")
    print("   QUICK_START_MONETIZATION.md - Пошаговая инструкция")
    print("   MONETIZATION_READY.md - Статус и следующие шаги")

    # Предложить запустить автосбор
    if config.get('COLLECT_INTERVAL_SEC') and config.get('TELEGRAM_BOT_TOKEN'):
        if ask_yes_no("\n🤖 Запустить Telegram бота сейчас?", default=False):
            print("\nЗапуск бота...")
            print("Нажмите Ctrl+C для остановки")
            os.system("python telegram_bot.py")

    return True


def main():
    """Главная функция."""
    system = detect_os()

    # Проверка ОС
    if system == "Darwin":  # macOS
        print_box([
            "Stock Signal Analyzer",
            "",
            "Обнаружена macOS.",
            "",
            "Интерактивная установка предназначена только для Ubuntu сервера.",
            "На macOS все уже установлено и готово к использованию.",
            "",
            "Для работы на macOS используйте:",
            "  python tools/verify_monetization.py",
            "  python tools/monitor_signals.py",
            "",
            "Для установки на сервере запустите этот скрипт на Ubuntu."
        ])
        return 0

    elif system == "Linux":
        # Проверить, что это Ubuntu
        try:
            with open('/etc/os-release', 'r') as f:
                os_info = f.read()
            if 'Ubuntu' not in os_info and 'Debian' not in os_info:
                print(f"⚠️  Обнаружена Linux система, но не Ubuntu/Debian.")
                if not ask_yes_no("Продолжить установку?", default=True):
                    return 1
        except:
            pass

        # Запустить интерактивную установку
        try:
            success = run_interactive_setup()
            return 0 if success else 1
        except KeyboardInterrupt:
            print("\n\n⚠️  Установка прервана пользователем.")
            return 1
        except Exception as e:
            print(f"\n\n❌ Ошибка установки: {e}")
            import traceback
            traceback.print_exc()
            return 1

    else:
        print(f"❌ Неподдерживаемая ОС: {system}")
        print("Поддерживаются: Ubuntu/Debian Linux")
        return 1


if __name__ == "__main__":
    sys.exit(main())
