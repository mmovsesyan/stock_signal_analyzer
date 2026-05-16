#!/usr/bin/env python3
"""
Telegram-бот: котировки, полный отчёт, свод по списку с сегментами (РФ / иностр. / дивиденды),
уведомления о сильных сигналах вне списка (JobQueue).

Переменные окружения:
  TELEGRAM_BOT_TOKEN — токен от @BotFather (обязательно)
  FINNHUB_API_KEY    — опционально
  STOCK_SIGNAL_DATA  — каталог для telegram_users.json (опционально)
  NOTIFY_INTERVAL_SEC — период проверки «сильных вне списка» (по умолчанию 3600)
  OUTSIDE_SCAN_MAX — сколько тикеров из рыночного пула анализировать за раз (по умолчанию весь пул ~120)
  SSA_SIGNAL_LOG — путь к JSONL: лог сигналов для бэктеста (опционально)
  NOTIFY_MIN_TIER — если A, уведомления «вне списка» только при классе качества A
  COLLECT_INTERVAL_SEC — автосбор сигналов каждые N секунд (0 = выключен, рекомендуется 14400 = 4 часа)

Запуск:
  export TELEGRAM_BOT_TOKEN="..."
  python telegram_bot.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import stenv

stenv.load_project_env()

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from stock_signal_analyzer.dashboard import build_dashboard
from stock_signal_analyzer.engine import build_report
from stock_signal_analyzer.market_data import fetch_snapshot_with_meta
from stock_signal_analyzer.market_segments import DIVIDEND_UNIVERSE
from stock_signal_analyzer.outside_signals import scan_strong_outside_watchlist
from stock_signal_analyzer.signal_log import log_path_from_env
from stock_signal_analyzer.outcome_tracker import OutcomeTracker
from stock_signal_analyzer.live_price import fetch_live_price
from stock_signal_analyzer.config_validator import validate_symbol, validate_telegram_config
from stock_signal_analyzer.adaptive_weights import compute_adaptive_weights
from stock_signal_analyzer.telegram_format import (
    esc_html as _esc,
    format_dashboard_bundle,
    format_outside_notification,
    format_quick_quote,
    format_signal_report,
    parse_dash_args,
    sanitize_command_args,
    split_telegram_html,
)
from stock_signal_analyzer.user_store import (
    all_user_ids,
    can_notify_again,
    mark_notified,
    normalize_symbol,
)
from stock_signal_analyzer.user_settings import (
    ensure_user_exists,
    load_prefs,
    save_prefs,
)
from stock_signal_analyzer.subscriptions import (
    check_feature_access,
    get_user_tier,
    get_tier_limits,
)
from stock_signal_analyzer.universe import RU_BLUE_CHIPS, US_BLUE_CHIPS

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("telegram_bot")
_PENDING_ACTION_KEY = "pending_action"

# ── Admin access control ─────────────────────────────────────────────────────
# ADMIN_CHAT_ID — Telegram ID администратора. Получает уведомления о новых юзерах,
# управляет доступом. Задать в .env.
_ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "").strip()
_ADMIN_CONTACT_INFO = os.environ.get("ADMIN_CONTACT_INFO", "").strip()
# ALLOW_ALL_USERS — если "1" или "true", доступ всем (для дебага/локальной разработки)
_ALLOW_ALL = os.environ.get("ALLOW_ALL_USERS", "").strip().lower() in ("1", "true", "yes")
# Множество одобренных user_id (загружается из файла)
_APPROVED_USERS_FILE = os.path.join(
    os.environ.get("STOCK_SIGNAL_DATA", "data"), "approved_users.json"
)


def _load_approved_users() -> set[int]:
    """Загрузить список одобренных пользователей."""
    try:
        import json as _json
        if os.path.exists(_APPROVED_USERS_FILE):
            with open(_APPROVED_USERS_FILE, encoding="utf-8") as f:
                data = _json.load(f)
            return set(int(uid) for uid in data.get("approved", []))
    except Exception:
        pass
    return set()


def _save_approved_users(approved: set[int]) -> None:
    """Сохранить список одобренных пользователей."""
    import json as _json
    dirn = os.path.dirname(_APPROVED_USERS_FILE)
    if dirn:
        os.makedirs(dirn, exist_ok=True)
    with open(_APPROVED_USERS_FILE, "w", encoding="utf-8") as f:
        _json.dump({"approved": list(approved)}, f)


def _is_admin(user_id: int) -> bool:
    """Проверить, является ли пользователь админом."""
    return _ADMIN_CHAT_ID and str(user_id) == _ADMIN_CHAT_ID


def _is_approved(user_id: int) -> bool:
    """Проверить, одобрен ли пользователь (или он админ).

    Приоритет:
    1. ALLOW_ALL_USERS — доступ всем (для дебага)
    2. Админ — всегда доступ
    3. БД: is_active=True
    4. Legacy: approved_users.json
    5. Без ADMIN_CHAT_ID — доступ закрыт
    """
    if _ALLOW_ALL:
        return True
    if _is_admin(user_id):
        return True
    # Проверка БД (primary source)
    try:
        from stock_signal_analyzer.db import db_available, get_session, User as DbUser
        if db_available():
            with get_session(read_only=True) as session:
                user = session.query(DbUser).filter_by(telegram_id=user_id).first()
                if user and user.is_active:
                    return True
    except Exception:
        pass
    # Fallback на JSON
    approved = _load_approved_users()
    if user_id in approved:
        return True
    # Без ADMIN_CHAT_ID доступ закрыт
    if not _ADMIN_CHAT_ID:
        return False
    return False


def _normalize_menu_symbols(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        s = normalize_symbol(raw)
        if not s:
            continue
        if s == "BRK.B":
            s = "BRK-B"
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


_RU_BLUE_LIST = _normalize_menu_symbols([f"{x}.ME" for x in sorted(RU_BLUE_CHIPS)])
_US_BLUE_LIST = _normalize_menu_symbols(list(sorted(US_BLUE_CHIPS)))
_DIVIDEND_LIST = _normalize_menu_symbols(list(sorted(DIVIDEND_UNIVERSE)))
_SECTOR_MAP: dict[str, list[str]] = {
    "tech": _normalize_menu_symbols(
        ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "ORCL", "CRM", "QCOM", "INTC", "YNDX.ME"]
    ),
    "finance": _normalize_menu_symbols(
        ["JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP", "SBER.ME", "VTBR.ME", "MOEX.ME"]
    ),
    "energy": _normalize_menu_symbols(
        ["XOM", "CVX", "COP", "SLB", "ROSN.ME", "GAZP.ME", "LKOH.ME", "NVTK.ME", "TATN.ME"]
    ),
}
_PICK_GROUPS: dict[str, tuple[str, list[str]]] = {
    "ru": ("🇷🇺 Голубые фишки РФ", _RU_BLUE_LIST),
    "us": ("🌐 Голубые фишки США", _US_BLUE_LIST),
    "div": ("💰 Дивидендные", _DIVIDEND_LIST),
    "tech": ("🖥️ Отрасль: Технологии", _SECTOR_MAP["tech"]),
    "fin": ("🏦 Отрасль: Финансы", _SECTOR_MAP["finance"]),
    "en": ("🛢️ Отрасль: Энергия", _SECTOR_MAP["energy"]),
}
_PICK_PAGE_SIZE = 10
_PICK_GROUP_DESCRIPTIONS: dict[str, str] = {
    "ru": "Крупные ликвидные акции Мосбиржи.",
    "us": "Крупнейшие международные компании США.",
    "div": "Компании с устойчивой дивидендной историей.",
    "tech": "Лидеры IT и полупроводников.",
    "fin": "Банки, биржи и платёжные системы.",
    "en": "Нефть, газ и энергетика.",
}
_SYMBOL_TITLES: dict[str, str] = {
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "GOOGL": "Alphabet",
    "AMZN": "Amazon",
    "META": "Meta",
    "NVDA": "NVIDIA",
    "JPM": "JPMorgan",
    "BAC": "Bank of America",
    "WFC": "Wells Fargo",
    "GS": "Goldman Sachs",
    "MS": "Morgan Stanley",
    "V": "Visa",
    "MA": "Mastercard",
    "AXP": "American Express",
    "XOM": "Exxon Mobil",
    "CVX": "Chevron",
    "COP": "ConocoPhillips",
    "SLB": "Schlumberger",
    "ORCL": "Oracle",
    "CRM": "Salesforce",
    "QCOM": "Qualcomm",
    "INTC": "Intel",
    "SBER.ME": "Сбер",
    "GAZP.ME": "Газпром",
    "LKOH.ME": "Лукойл",
    "GMKN.ME": "Норникель",
    "NVTK.ME": "Новатэк",
    "ROSN.ME": "Роснефть",
    "TATN.ME": "Татнефть",
    "MOEX.ME": "Мосбиржа",
    "MGNT.ME": "Магнит",
    "MTSS.ME": "МТС",
    "CHMF.ME": "Северсталь",
    "NLMK.ME": "НЛМК",
    "YNDX.ME": "Яндекс",
    "PLZL.ME": "Полюс",
    "VTBR.ME": "ВТБ",
}


def _main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("📈 Аналитика"), KeyboardButton("📚 Списки и подбор")],
            [KeyboardButton("🗂️ Сбор и экспорт"), KeyboardButton("⚙️ Настройки")],
            [KeyboardButton("🏠 Главное меню"), KeyboardButton("❓ Помощь")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=False,
    )


def _analysis_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("📊 Анализ тикера"), KeyboardButton("💲 Цена тикера")],
            [KeyboardButton("🧾 Свод по рынку")],
            [KeyboardButton("⬅️ Назад в разделы"), KeyboardButton("🏠 Главное меню")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=False,
    )


def _lists_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("⭐ Мой watchlist"), KeyboardButton("🧭 Подбор тикеров")],
            [KeyboardButton("⬅️ Назад в разделы"), KeyboardButton("🏠 Главное меню")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=False,
    )


def _collect_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("🗂️ Сбор сигналов"), KeyboardButton("📈 Статус сбора")],
            [KeyboardButton("📤 Выгрузить лог")],
            [KeyboardButton("⬅️ Назад в разделы"), KeyboardButton("🏠 Главное меню")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=False,
    )


def _settings_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("⚙️ Интерактивные настройки")],
            [KeyboardButton("🤖 Настройка автосбора"), KeyboardButton("🔔 Уведомления")],
            [KeyboardButton("🧠 Обучение")],
            [KeyboardButton("⬅️ Назад в разделы"), KeyboardButton("🏠 Главное меню")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=False,
    )


def _learning_menu_keyboard(uid: int) -> ReplyKeyboardMarkup:
    prefs = load_prefs(uid)
    report_status = "✅" if prefs.receive_learning_report else "❌"
    kb = [
        [KeyboardButton("📊 Показать отчёт"), KeyboardButton("📈 Статистика исходов")],
        [KeyboardButton("🧪 Бэктест"), KeyboardButton(f"{report_status} Получать learning report")],
    ]
    if _is_admin(uid):
        kb.append([KeyboardButton("🔄 Принудительное обучение")])
    kb.append([KeyboardButton("⬅️ Назад в настройки"), KeyboardButton("🏠 Главное меню")])
    return ReplyKeyboardMarkup(
        keyboard=kb,
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=False,
    )


def _notify_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("🔔 Уведомления ВКЛ"), KeyboardButton("🔕 Уведомления ВЫКЛ")],
            [KeyboardButton("⬅️ Назад в настройки"), KeyboardButton("🏠 Главное меню")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=False,
    )


def _autocollect_menu_keyboard(uid: int) -> ReplyKeyboardMarkup:
    prefs = load_prefs(uid)
    default_status = "✅" if prefs.use_default_tickers else "❌"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(f"{default_status} Дефолтные тикеры (30)")],
            [KeyboardButton("➕ Добавить свои тикеры"), KeyboardButton("📋 Показать текущие")],
            [KeyboardButton("🗑️ Очистить свои тикеры")],
            [KeyboardButton("⬅️ Назад в настройки"), KeyboardButton("🏠 Главное меню")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=False,
    )


async def _show_root_sections_menu(message) -> None:
    if not message:
        return
    await message.reply_text(
        "Выберите раздел:",
        reply_markup=_main_menu_keyboard(),
    )


async def _show_analysis_menu(message) -> None:
    if not message:
        return
    await message.reply_text(
        "📈 <b>Аналитика</b>\n"
        "• Анализ тикера\n"
        "• Быстрая цена\n"
        "• Свод по рынку",
        parse_mode=ParseMode.HTML,
        reply_markup=_analysis_menu_keyboard(),
    )


async def _show_lists_menu(message) -> None:
    if not message:
        return
    await message.reply_text(
        "📚 <b>Списки и подбор</b>\n"
        "• Ваш watchlist\n"
        "• Подбор тикеров по категориям и отраслям",
        parse_mode=ParseMode.HTML,
        reply_markup=_lists_menu_keyboard(),
    )


async def _show_collect_menu(message) -> None:
    if not message:
        return
    await message.reply_text(
        "🗂️ <b>Сбор и экспорт</b>\n"
        "• Запуск массового сбора\n"
        "• Статус накопления\n"
        "• Выгрузка JSONL",
        parse_mode=ParseMode.HTML,
        reply_markup=_collect_menu_keyboard(),
    )


async def _show_settings_menu(message, uid: int) -> None:
    if not message:
        return
    await message.reply_text(
        "⚙️ <b>Настройки</b>\n"
        "• Настройка автосбора сигналов\n"
        "• Управление уведомлениями\n"
        "• 🧠 Обучение и статистика",
        parse_mode=ParseMode.HTML,
        reply_markup=_settings_menu_keyboard(),
    )


async def _show_learning_menu(message, uid: int) -> None:
    if not message:
        return
    prefs = load_prefs(uid)
    report_status = "включены" if prefs.receive_learning_report else "выключены"
    text = (
        "🧠 <b>Обучение и статистика</b>\n\n"
        "Система анализирует результаты сигналов:\n"
        "• Числовой IC — корреляция компонентов с PnL\n"
        "• LLM-анализ паттернов через Ollama\n"
        "• Адаптивные веса корректируются автоматически\n\n"
        f"Learning report: <b>{report_status}</b>"
    )
    await message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=_learning_menu_keyboard(uid),
    )


async def _show_notify_menu(message) -> None:
    if not message:
        return
    await message.reply_text(
        "🔔 <b>Уведомления</b>\n"
        "Включение/выключение уведомлений о сильных сигналах вне watchlist.",
        parse_mode=ParseMode.HTML,
        reply_markup=_notify_menu_keyboard(),
    )


async def _show_autocollect_menu(message, uid: int) -> None:
    if not message:
        return
    prefs = load_prefs(uid)
    default_status = "включены" if prefs.use_default_tickers else "выключены"
    custom_count = len(prefs.autocollect_tickers)

    text = (
        "🤖 <b>Настройка автосбора</b>\n\n"
        f"Дефолтные тикеры (30): <b>{default_status}</b>\n"
        f"Ваши тикеры: <b>{custom_count}</b>\n\n"
        "Автосбор использует:\n"
        "• Дефолтные 30 тикеров (если включены)\n"
        "• Ваши добавленные тикеры\n"
        "• Ваш watchlist"
    )
    await message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=_autocollect_menu_keyboard(uid),
    )


def _keyboard_for_section(section: str, uid: int = 0) -> ReplyKeyboardMarkup:
    if section == "analysis":
        return _analysis_menu_keyboard()
    if section == "lists":
        return _lists_menu_keyboard()
    if section == "collect":
        return _collect_menu_keyboard()
    if section == "settings":
        return _settings_menu_keyboard()
    if section == "notify":
        return _notify_menu_keyboard()
    if section == "autocollect" and uid:
        return _autocollect_menu_keyboard(uid)
    return _main_menu_keyboard()


def _section_for_action(action: str) -> str:
    if action in ("signal", "price", "dashboard"):
        return "analysis"
    if action in ("pick", "watchlist"):
        return "lists"
    if action in ("collect", "status", "export"):
        return "collect"
    if action in ("settings",):
        return "settings"
    if action in ("notify_on", "notify_off"):
        return "notify"
    if action in ("autocollect", "toggle_default", "add_custom", "show_custom", "clear_custom"):
        return "autocollect"
    if action in ("learning", "show_learning", "show_report", "show_stats", "toggle_learn_report", "force_learn"):
        return "learning"
    return "root"


def _reply_markup_for_action(action: str, uid: int = 0) -> ReplyKeyboardMarkup:
    return _keyboard_for_section(_section_for_action(action), uid)


def _set_pending_action(context: ContextTypes.DEFAULT_TYPE, action: str) -> None:
    context.user_data[_PENDING_ACTION_KEY] = action


def _get_pending_action(context: ContextTypes.DEFAULT_TYPE) -> str:
    val = context.user_data.get(_PENDING_ACTION_KEY, "")
    return val if isinstance(val, str) else ""


def _clear_pending_action(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(_PENDING_ACTION_KEY, None)


def _uid(update: Update) -> int:
    if update.effective_user is None:
        return 0
    return int(update.effective_user.id)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user = update.effective_user
    uid = user.id if user else 0
    if not uid:
        return

    # Убедиться что пользователь есть в БД
    ensure_user_exists(uid, username=user.username if user else None)

    # Если пользователь уже одобрен — показать главное меню
    if _is_approved(uid):
        text = (
            "Привет. Это <b>Stock Signal Analyzer</b>.\n\n"
            "Для удобства функции разложены по разделам:\n"
            "• 📈 Аналитика\n"
            "• 📚 Списки и подбор\n"
            "• 🗂️ Сбор и экспорт\n"
            "• ⚙️ Настройки\n\n"
            "Выберите раздел кнопками ниже."
        )
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=_main_menu_keyboard(),
        )
        return

    # ── Новый пользователь — показать выбор плана ──
    plan_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🆓 Free — базовый доступ", callback_data="plan|free")],
        [InlineKeyboardButton("⭐ Pro — полный анализ US+RU", callback_data="plan|pro")],
        [InlineKeyboardButton("💎 Premium — всё + AI обучение", callback_data="plan|premium")],
    ])

    welcome_text = (
        "👋 Добро пожаловать в <b>Stock Signal Analyzer</b>!\n\n"
        "Система AI-анализа торговых сигналов:\n"
        "• 7+ факторов анализа\n"
        "• Готовые торговые планы\n"
        "• Самообучение на результатах\n\n"
        "Выберите план для начала работы:"
    )
    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.HTML,
        reply_markup=plan_keyboard,
    )


async def _on_plan_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка выбора плана новым пользователем."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    parts = query.data.split("|")
    if len(parts) != 2 or parts[0] != "plan":
        return

    plan = parts[1]  # free, pro, premium
    user = update.effective_user
    if not user:
        return

    plan_names = {"free": "🆓 Free", "pro": "⭐ Pro", "premium": "💎 Premium"}
    plan_label = plan_names.get(plan, plan)

    # Подтверждение пользователю
    contact_line = ""
    if _ADMIN_CONTACT_INFO:
        contact_line = (
            f"\n\n📞 Для связи с администратором:\n"
            f"  <b>{_esc(_ADMIN_CONTACT_INFO)}</b>\n"
        )
    await query.edit_message_text(
        f"✅ Вы выбрали план: <b>{plan_label}</b>\n\n"
        "Ваша заявка отправлена администратору.\n"
        "Вы получите уведомление когда доступ будет активирован.\n\n"
        "⏳ Обычно это занимает несколько минут."
        f"{contact_line}",
        parse_mode=ParseMode.HTML,
    )

    # ── Уведомление админу ──
    if _ADMIN_CHAT_ID:
        username = user.username or "нет"
        first_name = user.first_name or ""
        last_name = user.last_name or ""
        full_name = f"{first_name} {last_name}".strip() or "Без имени"
        lang = user.language_code or "?"

        admin_text = (
            "🆕 <b>Новый пользователь запрашивает доступ</b>\n\n"
            f"👤 <b>Имя:</b> {_esc(full_name)}\n"
            f"📛 <b>Username:</b> @{_esc(username)}\n"
            f"🆔 <b>ID:</b> <code>{user.id}</code>\n"
            f"🌐 <b>Язык:</b> {_esc(lang)}\n"
            f"📋 <b>Выбранный план:</b> {plan_label}\n\n"
            f"Для одобрения отправьте:\n"
            f"<code>/approve {user.id}</code>\n\n"
            f"Для одобрения с конкретным планом:\n"
            f"<code>/approve {user.id} {plan}</code>\n\n"
            f"Для отклонения:\n"
            f"<code>/deny {user.id}</code>"
        )

        admin_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"✅ Одобрить ({plan_label})", callback_data=f"adm|approve|{user.id}|{plan}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"adm|deny|{user.id}"),
            ],
            [
                InlineKeyboardButton("🆓 Дать Free", callback_data=f"adm|approve|{user.id}|free"),
                InlineKeyboardButton("⭐ Дать Pro", callback_data=f"adm|approve|{user.id}|pro"),
                InlineKeyboardButton("💎 Дать Premium", callback_data=f"adm|approve|{user.id}|premium"),
            ],
        ])

        try:
            await context.bot.send_message(
                chat_id=int(_ADMIN_CHAT_ID),
                text=admin_text,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_keyboard,
            )
        except Exception as e:
            log.warning("Failed to notify admin: %s", e)

    # Дублируем уведомление в MAX
    try:
        from stock_signal_analyzer.max_notify import send_new_user_alert_max, max_available
        if max_available():
            await send_new_user_alert_max(
                full_name=full_name,
                username=username,
                user_id=user.id,
                plan=plan_label,
            )
    except Exception:
        pass


async def _on_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка кнопок одобрения/отклонения от админа."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    # Проверить что это админ
    if not _is_admin(query.from_user.id if query.from_user else 0):
        return

    parts = query.data.split("|")
    if len(parts) < 3 or parts[0] != "adm":
        return

    action = parts[1]  # approve, deny
    target_uid = int(parts[2])
    plan = parts[3] if len(parts) > 3 else "free"

    if action == "approve":
        # Добавить в approved
        approved = _load_approved_users()
        approved.add(target_uid)
        _save_approved_users(approved)

        # Активировать в БД
        try:
            from stock_signal_analyzer.db import db_available, get_session, User as DbUser
            if db_available():
                with get_session() as session:
                    user = session.query(DbUser).filter_by(telegram_id=target_uid).first()
                    if user:
                        user.is_active = True
                        user.tier = plan
        except Exception:
            pass

        # Сохранить тариф
        prefs = load_prefs(target_uid)
        prefs.tier = plan
        save_prefs(target_uid, prefs)

        plan_names = {"free": "🆓 Free", "pro": "⭐ Pro", "premium": "💎 Premium"}
        plan_label = plan_names.get(plan, plan)

        # Уведомить пользователя
        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text=(
                    f"✅ <b>Доступ активирован!</b>\n\n"
                    f"Ваш план: <b>{plan_label}</b>\n\n"
                    "Отправьте /start чтобы начать работу."
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        await query.edit_message_text(
            query.message.text + f"\n\n✅ <b>ОДОБРЕНО</b> — план {plan_label}",
            parse_mode=ParseMode.HTML,
        )

    elif action == "deny":
        # Деактивировать в БД
        try:
            from stock_signal_analyzer.db import db_available, get_session, User as DbUser
            if db_available():
                with get_session() as session:
                    user = session.query(DbUser).filter_by(telegram_id=target_uid).first()
                    if user:
                        user.is_active = False
        except Exception:
            pass

        # Уведомить пользователя
        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text=(
                    "❌ К сожалению, ваша заявка отклонена.\n"
                    "Свяжитесь с администратором для уточнения."
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        await query.edit_message_text(
            query.message.text + "\n\n❌ <b>ОТКЛОНЕНО</b>",
            parse_mode=ParseMode.HTML,
        )


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /approve <user_id> [plan] — одобрить пользователя."""
    if not update.message:
        return
    if not _is_admin(_uid(update)):
        await update.message.reply_text("⛔ Только для администратора.")
        return

    args = context.args or []
    if not args:
        await update.message.reply_text("Использование: /approve <user_id> [free|pro|premium]")
        return

    try:
        target_uid = int(args[0])
    except ValueError:
        await update.message.reply_text("Неверный user_id")
        return

    plan = args[1] if len(args) > 1 else "free"

    approved = _load_approved_users()
    approved.add(target_uid)
    _save_approved_users(approved)

    # Активировать в БД
    try:
        from stock_signal_analyzer.db import db_available, get_session, User as DbUser
        if db_available():
            with get_session() as session:
                user = session.query(DbUser).filter_by(telegram_id=target_uid).first()
                if user:
                    user.is_active = True
                    user.tier = plan
    except Exception:
        pass

    # Сохранить тариф
    prefs = load_prefs(target_uid)
    prefs.tier = plan
    save_prefs(target_uid, prefs)

    # Уведомить пользователя
    plan_names = {"free": "🆓 Free", "pro": "⭐ Pro", "premium": "💎 Premium"}
    plan_label = plan_names.get(plan, plan)
    try:
        await context.bot.send_message(
            chat_id=target_uid,
            text=f"✅ Доступ активирован! План: <b>{plan_label}</b>\nОтправьте /start",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass

    await update.message.reply_text(f"✅ Пользователь {target_uid} одобрен (план: {plan_label})")


async def cmd_deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /deny <user_id> — отклонить/заблокировать пользователя."""
    if not update.message:
        return
    if not _is_admin(_uid(update)):
        await update.message.reply_text("⛔ Только для администратора.")
        return

    args = context.args or []
    if not args:
        await update.message.reply_text("Использование: /deny <user_id>")
        return

    try:
        target_uid = int(args[0])
    except ValueError:
        await update.message.reply_text("Неверный user_id")
        return

    # Удалить из approved
    approved = _load_approved_users()
    approved.discard(target_uid)
    _save_approved_users(approved)

    # Деактивировать в БД
    try:
        from stock_signal_analyzer.db import db_available, get_session, User as DbUser
        if db_available():
            with get_session() as session:
                user = session.query(DbUser).filter_by(telegram_id=target_uid).first()
                if user:
                    user.is_active = False
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=target_uid,
            text="❌ Ваш доступ отозван. Свяжитесь с администратором.",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass

    await update.message.reply_text(f"❌ Пользователь {target_uid} заблокирован")


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /users — список одобренных пользователей (только для админа)."""
    if not update.message:
        return
    if not _is_admin(_uid(update)):
        await update.message.reply_text("⛔ Только для администратора.")
        return

    approved = _load_approved_users()
    if not approved:
        await update.message.reply_text("Нет одобренных пользователей.")
        return

    lines = [f"👥 <b>Одобренные пользователи ({len(approved)}):</b>\n"]
    for uid in sorted(approved):
        lines.append(f"  • <code>{uid}</code>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Помощь — полная справка по боту."""
    if not update.message:
        return
    text = (
        "<b>📈 Stock Signal Analyzer — справка</b>\n\n"
        "<b>Что это?</b>\n"
        "AI-советчик по трейдингу. Анализирует акции по 7+ факторам "
        "(техника, импульс, новости, объём, макро, квант-модели, режим рынка) "
        "и выдаёт готовый торговый план с entry, stop, targets.\n\n"
        "<b>Как читать сигнал?</b>\n"
        "• <b>Score</b> — сила сигнала (от -1 до +1). Чем выше |score|, тем сильнее.\n"
        "• <b>Tier</b> — класс сигнала:\n"
        "  <b>A</b> — сильный (score ≥ 0.46, ADX ≥ 20, R:R ≥ 1.5)\n"
        "  <b>B</b> — средний (score ≥ 0.30, ADX ≥ 20, R:R ≥ 1.5)\n"
        "  <b>C</b> — слабый / нет сигнала\n"
        "• <b>R:R</b> — соотношение риска к прибыли (всегда ≥ 1.5)\n\n"
        "<b>Что делать с сигналом?</b>\n"
        "<b>Класс A (long/short)</b> — рассмотреть вход. Стоп-лосс обязателен.\n"
        "<b>Класс B</b> — можно входить, но размер позиции уменьшить вдвое.\n"
        "<b>Класс C</b> — не торговать, ждать лучших условий.\n\n"
        "<b>Команды бота:</b>\n"
        "/signal &lt;тикер&gt; — полный анализ (AAPL, SBER.ME)\n"
        "/price &lt;тикер&gt; — быстрая цена\n"
        "/dashboard — свод по watchlist\n"
        "/watchlist — управление списком отслеживания\n"
        "/settings — интерактивные настройки\n"
        "/learning — отчёт обучения\n"
        "/status — статус системы\n"
        "/help — эта справка\n\n"
        "<b>Настройки:</b>\n"
        "Фильтр сигналов (conservative/balanced/aggressive), уведомления вне watchlist, "
        "learning report, автосбор, просадки, дайджест — всё настраивается через /settings.\n\n"
        "<b>Рынки:</b>\n"
        "US — Polygon + Finnhub + Yahoo\n"
        "RU — Т-Банк (Мосбиржа) + Yahoo fallback\n\n"
        "<b>⚠️ Важно:</b>\n"
        "Это только информационный инструмент, НЕ финансовая рекомендация. "
        "Всегда проводите собственный анализ. Торговля связана с риском потери капитала."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=_main_menu_keyboard())


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Интерактивные настройки пользователя."""
    uid = _uid(update)
    if not _is_approved(uid):
        if update.message:
            await update.message.reply_text("⛔ Доступ не активирован. Отправьте /start")
        return
    await _show_settings_inline(update, uid)


async def _show_settings_inline(update: Update, uid: int) -> None:
    """Показать inline-меню настроек с учётом тарифа."""
    prefs = load_prefs(uid)
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if not msg:
        return

    tier = get_user_tier(uid)
    limits = get_tier_limits(tier)

    filter_icon = {"conservative": "🛡️", "balanced": "⚖️", "aggressive": "🚀"}
    filter_label = {"conservative": "Консервативный", "balanced": "Сбалансированный", "aggressive": "Агрессивный"}
    fi = filter_icon.get(prefs.signal_filter_type, "⚖️")
    fl = filter_label.get(prefs.signal_filter_type, "Сбалансированный")

    def _feat(val: bool, available: bool) -> str:
        if not available:
            return "🔒"
        return "✅" if val else "❌"

    text = f"""⚙️ <b>Настройки</b>
📋 Тариф: <b>{limits.name}</b>

📊 <b>Фильтр сигналов:</b> {fi} {fl}
   Определяет, какие сигналы показывают.

🔔 <b>Уведомления вне списка:</b> {'✅' if prefs.notify_strong_outside else '❌'}
   Порог: |score| ≥ {prefs.strong_threshold:.2f}

📈 <b>Learning report:</b> {_feat(prefs.receive_learning_report, limits.learning_report)}

⚡ <b>Автосбор:</b> {_feat(prefs.auto_collect, limits.autocollect)}

🛡️ <b>Уведомления о просадках:</b> {_feat(prefs.notify_drawdown, limits.drawdown_notify)}

📰 <b>Ежедневный дайджест:</b> {_feat(prefs.daily_digest, limits.daily_digest)}"""

    keyboard: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("🛡️ Консервативный", callback_data=f"set|filter|conservative|{uid}"),
            InlineKeyboardButton("⚖️ Сбалансированный", callback_data=f"set|filter|balanced|{uid}"),
            InlineKeyboardButton("🚀 Агрессивный", callback_data=f"set|filter|aggressive|{uid}"),
        ],
        [
            InlineKeyboardButton("🔔 Уведомления: ON" if prefs.notify_strong_outside else "🔕 Уведомления: OFF",
                               callback_data=f"set|notify|{('off' if prefs.notify_strong_outside else 'on')}|{uid}"),
        ],
    ]

    if limits.learning_report:
        keyboard.append([
            InlineKeyboardButton("📈 Learning: ON" if prefs.receive_learning_report else "📈 Learning: OFF",
                               callback_data=f"set|learning|{('off' if prefs.receive_learning_report else 'on')}|{uid}"),
        ])
    else:
        keyboard.append([InlineKeyboardButton("🔒 Learning — требуется Pro", callback_data=f"set|upgrade|learning|{uid}")])

    if limits.autocollect:
        keyboard.append([
            InlineKeyboardButton("⚡ Автосбор: ON" if prefs.auto_collect else "⚡ Автосбор: OFF",
                               callback_data=f"set|autocollect|{('off' if prefs.auto_collect else 'on')}|{uid}"),
        ])
    else:
        keyboard.append([InlineKeyboardButton("🔒 Автосбор — требуется Pro", callback_data=f"set|upgrade|autocollect|{uid}")])

    if limits.drawdown_notify:
        keyboard.append([
            InlineKeyboardButton("🛡️ Просадки: ON" if prefs.notify_drawdown else "🛡️ Просадки: OFF",
                               callback_data=f"set|drawdown|{('off' if prefs.notify_drawdown else 'on')}|{uid}"),
        ])
    else:
        keyboard.append([InlineKeyboardButton("🔒 Просадки — требуется Pro", callback_data=f"set|upgrade|drawdown|{uid}")])

    if limits.daily_digest:
        keyboard.append([
            InlineKeyboardButton("📰 Дайджест: ON" if prefs.daily_digest else "📰 Дайджест: OFF",
                               callback_data=f"set|digest|{('off' if prefs.daily_digest else 'on')}|{uid}"),
        ])
    else:
        keyboard.append([InlineKeyboardButton("🔒 Дайджест — требуется Pro", callback_data=f"set|upgrade|digest|{uid}")])

    await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))


async def _on_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка inline-кнопок настроек с проверкой тарифа."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    parts = query.data.split("|")
    if len(parts) < 4 or parts[0] != "set":
        return

    _, key, value, uid_str = parts
    try:
        uid = int(uid_str)
    except ValueError:
        return

    # Проверить, что пользователь меняет свои настройки
    if _uid(update) != uid and not _is_admin(_uid(update)):
        await query.edit_message_text("⛔ Нельзя менять чужие настройки.", parse_mode=ParseMode.HTML)
        return

    tier = get_user_tier(uid)
    limits = get_tier_limits(tier)

    # Обработка запроса апгрейда
    if key == "upgrade":
        feature_names = {
            "learning": "Learning report",
            "autocollect": "Автосбор",
            "drawdown": "Уведомления о просадках",
            "digest": "Ежедневный дайджест",
        }
        name = feature_names.get(value, value)
        await query.answer(f"{name} доступен с тарифа Pro. Обратитесь к администратору.", show_alert=True)
        return

    prefs = load_prefs(uid)

    if key == "filter":
        prefs.signal_filter_type = value
    elif key == "notify":
        prefs.notify_strong_outside = (value == "on")
    elif key == "learning":
        if not limits.learning_report:
            await query.answer("Learning report доступен с тарифа Pro", show_alert=True)
            return
        prefs.receive_learning_report = (value == "on")
    elif key == "autocollect":
        if not limits.autocollect:
            await query.answer("Автосбор доступен с тарифа Pro", show_alert=True)
            return
        prefs.auto_collect = (value == "on")
    elif key == "drawdown":
        if not limits.drawdown_notify:
            await query.answer("Уведомления о просадках доступны с тарифа Pro", show_alert=True)
            return
        prefs.notify_drawdown = (value == "on")
    elif key == "digest":
        if not limits.daily_digest:
            await query.answer("Ежедневный дайджест доступен с тарифа Pro", show_alert=True)
            return
        prefs.daily_digest = (value == "on")

    save_prefs(uid, prefs)

    # Обновить сообщение
    filter_icon = {"conservative": "🛡️", "balanced": "⚖️", "aggressive": "🚀"}
    filter_label = {"conservative": "Консервативный", "balanced": "Сбалансированный", "aggressive": "Агрессивный"}
    fi = filter_icon.get(prefs.signal_filter_type, "⚖️")
    fl = filter_label.get(prefs.signal_filter_type, "Сбалансированный")

    def _feat(val: bool, available: bool) -> str:
        if not available:
            return "🔒"
        return "✅" if val else "❌"

    text = f"""
⚙️ <b>Настройки обновлены ✅</b>

📋 Тариф: <b>{limits.name}</b>


📊 <b>Фильтр сигналов:</b> {fi} {fl}

   Определяет, какие сигналы показывают.


🔔 <b>Уведомления вне списка:</b> {'✅' if prefs.notify_strong_outside else '❌'}

   Порог: |score| ≥ {prefs.strong_threshold:.2f}


📈 <b>Learning report:</b> {_feat(prefs.receive_learning_report, limits.learning_report)}


⚡ <b>Автосбор:</b> {_feat(prefs.auto_collect, limits.autocollect)}


🛡️ <b>Уведомления о просадках:</b> {_feat(prefs.notify_drawdown, limits.drawdown_notify)}


📰 <b>Ежедневный дайджест:</b> {_feat(prefs.daily_digest, limits.daily_digest)}
"""

    keyboard: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("🛡️ Консервативный", callback_data=f"set|filter|conservative|{uid}"),
            InlineKeyboardButton("⚖️ Сбалансированный", callback_data=f"set|filter|balanced|{uid}"),
            InlineKeyboardButton("🚀 Агрессивный", callback_data=f"set|filter|aggressive|{uid}"),
        ],
        [
            InlineKeyboardButton("🔔 Уведомления: ON" if prefs.notify_strong_outside else "🔕 Уведомления: OFF",
                               callback_data=f"set|notify|{('off' if prefs.notify_strong_outside else 'on')}|{uid}"),
        ],
    ]

    if limits.learning_report:
        keyboard.append([
            InlineKeyboardButton("📈 Learning: ON" if prefs.receive_learning_report else "📈 Learning: OFF",
                               callback_data=f"set|learning|{('off' if prefs.receive_learning_report else 'on')}|{uid}"),
        ])
    else:
        keyboard.append([InlineKeyboardButton("🔒 Learning — требуется Pro", callback_data=f"set|upgrade|learning|{uid}")])

    if limits.autocollect:
        keyboard.append([
            InlineKeyboardButton("⚡ Автосбор: ON" if prefs.auto_collect else "⚡ Автосбор: OFF",
                               callback_data=f"set|autocollect|{('off' if prefs.auto_collect else 'on')}|{uid}"),
        ])
    else:
        keyboard.append([InlineKeyboardButton("🔒 Автосбор — требуется Pro", callback_data=f"set|upgrade|autocollect|{uid}")])

    if limits.drawdown_notify:
        keyboard.append([
            InlineKeyboardButton("🛡️ Просадки: ON" if prefs.notify_drawdown else "🛡️ Просадки: OFF",
                               callback_data=f"set|drawdown|{('off' if prefs.notify_drawdown else 'on')}|{uid}"),
        ])
    else:
        keyboard.append([InlineKeyboardButton("🔒 Просадки — требуется Pro", callback_data=f"set|upgrade|drawdown|{uid}")])

    if limits.daily_digest:
        keyboard.append([
            InlineKeyboardButton("📰 Дайджест: ON" if prefs.daily_digest else "📰 Дайджест: OFF",
                               callback_data=f"set|digest|{('off' if prefs.daily_digest else 'on')}|{uid}"),
        ])
    else:
        keyboard.append([InlineKeyboardButton("🔒 Дайджест — требуется Pro", callback_data=f"set|upgrade|digest|{uid}")])

    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))


async def _send_price_for_symbol(message, sym: str) -> None:
    if not message:
        return
    try:
        sym = validate_symbol(sym)
    except ValueError as exc:
        await message.reply_text(f"⚠️ {exc}", parse_mode=ParseMode.HTML)
        return
    loop = asyncio.get_running_loop()
    try:
        snap, _info, profile = await loop.run_in_executor(
            None,
            lambda: fetch_snapshot_with_meta(sym),
        )
    except Exception as e:
        log.exception("price")
        await message.reply_text(_esc(f"Ошибка: {e}"), parse_mode=ParseMode.HTML)
        return

    # Получить real-time цену
    live_price = None
    try:
        live_price = await loop.run_in_executor(None, lambda: _fetch_live_price(snap.symbol))
    except Exception:
        pass

    body = format_quick_quote(
        snap.symbol,
        snap.company_name,
        snap.last_close,
        snap.currency,
        profile.label,
        live_price=live_price,
    )
    await message.reply_text(body, parse_mode=ParseMode.HTML)


def _fetch_live_price(symbol: str) -> float | None:
    """Получить актуальную цену из real-time источников."""
    from stock_signal_analyzer.live_price import fetch_live_price
    return fetch_live_price(symbol)


def _symbol_button_text(sym: str) -> str:
    title = _SYMBOL_TITLES.get(sym, "")
    if not title:
        base = sym.replace(".ME", "")
        title = _SYMBOL_TITLES.get(base, "")
    if not title:
        return sym
    text = f"{sym} · {title}"
    return text if len(text) <= 34 else f"{sym} · {title[:20]}…"


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not _is_approved(_uid(update)):
        await update.message.reply_text("⛔ Доступ не активирован. Отправьте /start")
        return
    args = context.args or []
    sym, _, _, bad = sanitize_command_args(args)
    if bad or not sym:
        _set_pending_action(context, "price")
        await update.message.reply_text(
            "Укажите тикер: /price AAPL или /price SBER.ME\n"
            "Я жду ваш ответ следующим сообщением (или напишите: отмена).",
            parse_mode=ParseMode.HTML,
            reply_markup=_reply_markup_for_action("price"),
        )
        return
    from stock_signal_analyzer.universe import resolve_symbol_market
    sym = resolve_symbol_market(sym)
    _clear_pending_action(context)
    await _send_price_for_symbol(update.message, sym)


async def _cmd_signal_message_with_args(message, args: list[str], user_id: int | None = None) -> None:
    if not message:
        return
    sym, tape, ws, bad = sanitize_command_args(args)
    if bad or not sym:
        await message.reply_text(
            "Пример: /signal AAPL или /signal SBER.ME tape ws",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        sym = validate_symbol(sym)
    except ValueError as exc:
        await message.reply_text(f"⚠️ {exc}", parse_mode=ParseMode.HTML)
        return

    # Автодетекция рынка: добавить .ME для российских тикеров
    from stock_signal_analyzer.universe import (
        BOND_ETFS_AND_FUNDS,
        RU_BLUE_CHIPS,
        US_BLUE_CHIPS,
        resolve_symbol_market,
    )
    sym = resolve_symbol_market(sym)
    base = sym.replace(".ME", "").upper()
    if "." not in sym and "-" not in sym and base not in US_BLUE_CHIPS and base not in BOND_ETFS_AND_FUNDS:
        # Неизвестный тикер без суффикса — предложить варианты
        suggestions = [f"<code>{base}.ME</code> (Мосбиржа)", f"<code>{base}</code> (US)"]
        await message.reply_text(
            f"🔍 Тикер <code>{_esc(base)}</code> не найден в списках.\n"
            f"Попробуйте: {' или '.join(suggestions)}",
            parse_mode=ParseMode.HTML,
        )
        return

    status_msg = await message.reply_text(
        f"⏳ Анализирую <b>{_esc(sym)}</b>… Это займёт 10-30 секунд.",
        parse_mode=ParseMode.HTML,
    )
    await message.reply_chat_action(action=ChatAction.TYPING)

    uid = user_id if user_id is not None else (int(message.from_user.id) if message.from_user else 0)
    prefs = load_prefs(uid)

    loop = asyncio.get_running_loop()
    try:
        report = await loop.run_in_executor(
            None,
            lambda: build_report(
                sym,
                volume_tape_ws=tape,
                use_finnhub_ws=ws,
                ws_seconds=8.0,
                filter_type=prefs.signal_filter_type,
                user_id=uid,
            ),
        )
    except ValueError as e:
        # Тикер не найден — быстрый ответ
        try:
            await status_msg.delete()
        except Exception:
            pass
        await message.reply_text(
            f"❌ {_esc(str(e))}",
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception as e:
        log.exception("signal")
        try:
            await status_msg.delete()
        except Exception:
            pass
        await message.reply_text(_esc(f"Ошибка: {e}"), parse_mode=ParseMode.HTML)
        return
    try:
        await status_msg.delete()
    except Exception:
        pass
    html_text = format_signal_report(report)
    for chunk in split_telegram_html(html_text):
        await message.reply_text(chunk, parse_mode=ParseMode.HTML)


def _pick_categories_markup() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🇷🇺 Голубые РФ (ликвидные)", callback_data="pk|c|ru|0"),
            InlineKeyboardButton("🌐 Голубые США (крупные)", callback_data="pk|c|us|0"),
        ],
        [
            InlineKeyboardButton("💰 Дивидендные (стабильные)", callback_data="pk|c|div|0"),
            InlineKeyboardButton("🖥️ Отрасль: Технологии", callback_data="pk|c|tech|0"),
        ],
        [
            InlineKeyboardButton("🏦 Отрасль: Финансы", callback_data="pk|c|fin|0"),
            InlineKeyboardButton("🛢️ Отрасль: Энергия", callback_data="pk|c|en|0"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def _pick_tickers_markup(group_id: str, page: int) -> InlineKeyboardMarkup:
    _title, symbols = _PICK_GROUPS[group_id]
    total = len(symbols)
    max_page = max(0, (total - 1) // _PICK_PAGE_SIZE)
    page = max(0, min(page, max_page))

    start = page * _PICK_PAGE_SIZE
    batch = symbols[start:start + _PICK_PAGE_SIZE]
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(batch), 2):
        pair = batch[i:i + 2]
        row = [
            InlineKeyboardButton(_symbol_button_text(s), callback_data=f"pk|t|{s}|{group_id}|{page}")
            for s in pair
        ]
        rows.append(row)

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"pk|c|{group_id}|{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{max_page + 1}", callback_data="pk|noop"))
    if page < max_page:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"pk|c|{group_id}|{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 Категории", callback_data="pk|home")])
    return InlineKeyboardMarkup(rows)


def _pick_actions_markup(sym: str, group_id: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Анализ (полный отчет)", callback_data=f"pk|a|sig|{sym}|{group_id}|{page}"),
                InlineKeyboardButton("💲 Цена (быстро)", callback_data=f"pk|a|price|{sym}|{group_id}|{page}"),
            ],
            [
                InlineKeyboardButton("⭐ Добавить в watchlist", callback_data=f"pk|a|watch|{sym}|{group_id}|{page}"),
                InlineKeyboardButton("🧾 Добавить в dashboard", callback_data=f"pk|a|dash|{sym}|{group_id}|{page}"),
            ],
            [InlineKeyboardButton("🔙 К списку", callback_data=f"pk|c|{group_id}|{page}")],
        ]
    )


async def cmd_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not _is_approved(_uid(update)):
        await update.message.reply_text("⛔ Доступ не активирован. Отправьте /start")
        return
    await update.message.reply_text(
        "Выберите категорию тикеров.\n"
        "Дальше можно выбрать бумагу и действие: анализ, цена, добавление в watchlist/dashboard.",
        reply_markup=_pick_categories_markup(),
    )


async def on_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    parts = query.data.split("|")
    if len(parts) < 2 or parts[0] != "pk":
        return
    action = parts[1]

    if action == "noop":
        return
    if action == "home":
        await query.edit_message_text(
            "Выберите категорию тикеров.\n"
            "Дальше можно выбрать бумагу и действие: анализ, цена, добавление в watchlist/dashboard.",
            reply_markup=_pick_categories_markup(),
        )
        return
    if action == "c" and len(parts) >= 4:
        group_id = parts[2]
        page = int(parts[3]) if parts[3].isdigit() else 0
        title, symbols = _PICK_GROUPS.get(group_id, ("Категория", []))
        if not symbols:
            await query.edit_message_text("Список пуст.")
            return
        group_desc = _PICK_GROUP_DESCRIPTIONS.get(group_id, "")
        desc_line = f"\n{group_desc}" if group_desc else ""
        await query.edit_message_text(
            f"{title}{desc_line}\nВыберите тикер:",
            reply_markup=_pick_tickers_markup(group_id, page),
        )
        return
    if action == "t" and len(parts) >= 5:
        sym = parts[2]
        group_id = parts[3]
        page = int(parts[4]) if parts[4].isdigit() else 0
        sym_title = _SYMBOL_TITLES.get(sym, _SYMBOL_TITLES.get(sym.replace(".ME", ""), ""))
        label = f"{_esc(sym)} — {_esc(sym_title)}" if sym_title else _esc(sym)
        await query.edit_message_text(
            f"Тикер: <b>{label}</b>\n"
            "Что сделать с этим тикером?",
            parse_mode=ParseMode.HTML,
            reply_markup=_pick_actions_markup(sym, group_id, page),
        )
        return
    if action == "a" and len(parts) >= 6:
        mode = parts[2]
        sym = parts[3]
        group_id = parts[4]
        page = int(parts[5]) if parts[5].isdigit() else 0
        msg = query.message
        if msg is None:
            return
        if mode == "sig":
            await msg.reply_chat_action(action=ChatAction.TYPING)
            await _cmd_signal_message_with_args(msg, [sym], user_id=_uid(update))
        elif mode == "price":
            await msg.reply_chat_action(action=ChatAction.TYPING)
            await _send_price_for_symbol(msg, sym)
        elif mode == "watch":
            uid = _uid(update)
            if uid:
                prefs = load_prefs(uid)
                n = normalize_symbol(sym)
                if n and n not in prefs.watchlist:
                    prefs.watchlist.append(n)
                    save_prefs(uid, prefs)
                await msg.reply_text(
                    f"Добавил <b>{_esc(sym)}</b> в watchlist.",
                    parse_mode=ParseMode.HTML,
                )
        elif mode == "dash":
            uid = _uid(update)
            await _cmd_dashboard_message_with_args(msg, uid, [sym])

        try:
            await query.edit_message_reply_markup(reply_markup=_pick_actions_markup(sym, group_id, page))
        except Exception:
            pass
        return


async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_approved(_uid(update)):
        if update.message:
            await update.message.reply_text("⛔ Доступ не активирован. Отправьте /start для подачи заявки.")
        return
    if not (context.args or []):
        _set_pending_action(context, "signal")
        if update.message:
            await update.message.reply_text(
                "Введите тикер для анализа (можно с опциями):\n"
                "<code>AAPL</code> или <code>SBER.ME tape ws</code>\n"
                "Я жду ваш ответ следующим сообщением (или напишите: отмена).",
                parse_mode=ParseMode.HTML,
                reply_markup=_reply_markup_for_action("signal"),
            )
        return
    _clear_pending_action(context)
    await _cmd_signal_message_with_args(update.message, context.args or [])


def _args_from_text_command(update: Update) -> list[str]:
    if not update.message or not update.message.text:
        return []
    parts = update.message.text.strip().split()
    return parts[1:] if len(parts) > 1 else []


async def cmd_signal_ru(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _cmd_signal_message_with_args(update.message, _args_from_text_command(update))


async def _cmd_dashboard_message_with_args(message, uid: int, args: list[str]) -> None:
    if not message:
        return
    if not uid:
        return
    args_syms, tape, ws = parse_dash_args(args)
    prefs = load_prefs(uid)
    merged: list[str] = []
    seen: set[str] = set()
    for s in list(args_syms) + list(prefs.watchlist):
        n = normalize_symbol(s)
        if not n or n in seen:
            continue
        seen.add(n)
        merged.append(n)
    max_symbols_raw = os.environ.get("DASHBOARD_MAX_SYMBOLS", "12")
    try:
        max_symbols = max(1, int(max_symbols_raw))
    except ValueError:
        max_symbols = 12
    if len(merged) > max_symbols:
        omitted = len(merged) - max_symbols
        merged = merged[:max_symbols]
        await message.reply_text(
            f"ℹ️ Для скорости показаны первые {max_symbols} тикеров "
            f"(остальные {omitted} пропущены в этом запросе).",
            reply_markup=_analysis_menu_keyboard(),
        )
    if not merged:
        await message.reply_text(
            "Нет тикеров: добавьте <code>/watchlist add SBER.ME AAPL</code> "
            "или укажите: <code>/dashboard AAPL MSFT</code>.\n"
            "Также можно открыть «🧭 Подбор тикеров» и добавить бумаги кнопками.",
            parse_mode=ParseMode.HTML,
            reply_markup=_analysis_menu_keyboard(),
        )
        return
    await message.reply_text(
        "🧾 Формирую свод по рынку, это может занять до ~1 минуты…",
        reply_markup=_analysis_menu_keyboard(),
    )
    await message.reply_chat_action(action=ChatAction.TYPING)
    loop = asyncio.get_running_loop()

    try:
        bundle = await loop.run_in_executor(
            None,
            lambda: build_dashboard(
                merged,
                volume_tape_ws=tape,
                use_finnhub_ws=ws,
                ws_seconds=8.0,
            ),
        )
    except Exception as e:
        log.exception("dashboard")
        await message.reply_text(_esc(f"Ошибка: {e}"), parse_mode=ParseMode.HTML)
        return
    html_text = format_dashboard_bundle(bundle, [])
    for chunk in split_telegram_html(html_text):
        await message.reply_text(chunk, parse_mode=ParseMode.HTML)

    # Блок «вне списка» считаем отдельно и с жёстким таймаутом, чтобы не задерживать основной ответ.
    outside_limit_raw = os.environ.get("DASHBOARD_OUTSIDE_MAX", "16")
    outside_timeout_raw = os.environ.get("DASHBOARD_OUTSIDE_TIMEOUT_SEC", "8")
    try:
        outside_limit = max(1, int(outside_limit_raw))
    except ValueError:
        outside_limit = 16
    try:
        outside_timeout = max(1.0, float(outside_timeout_raw))
    except ValueError:
        outside_timeout = 8.0

    try:
        outside_task = loop.run_in_executor(
            None,
            lambda: scan_strong_outside_watchlist(
                merged,
                prefs.strong_threshold,
                max_symbols=outside_limit,
                max_workers=2,
            ),
        )
        outside = await asyncio.wait_for(outside_task, timeout=outside_timeout)
        mset = {normalize_symbol(x) for x in merged}
        outside = [(s, r) for s, r in outside if normalize_symbol(s) not in mset]
        if outside:
            preview = ", ".join(f"{s} ({r.score:+.2f})" for s, r in outside[:3])
            await message.reply_text(
                f"⚡ Сильные сигналы вне списка: {preview}",
                reply_markup=_analysis_menu_keyboard(),
            )
    except asyncio.TimeoutError:
        log.info("dashboard outside scan timeout: %.1fs", outside_timeout)
    except Exception:
        log.exception("dashboard outside scan")


async def cmd_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_approved(_uid(update)):
        if update.message:
            await update.message.reply_text("⛔ Доступ не активирован. Отправьте /start")
        return
    # /dashboard без аргументов работает по watchlist, поэтому pending не включаем.
    _clear_pending_action(context)
    await _cmd_dashboard_message_with_args(update.message, _uid(update), context.args or [])


async def cmd_dashboard_ru(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _cmd_dashboard_message_with_args(update.message, _uid(update), _args_from_text_command(update))


async def on_menu_section_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_pending_action(context)
    await _show_analysis_menu(update.message)


async def on_menu_section_lists(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_pending_action(context)
    await _show_lists_menu(update.message)


async def on_menu_section_collect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_pending_action(context)
    await _show_collect_menu(update.message)


async def on_menu_section_notify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_pending_action(context)
    await _show_notify_menu(update.message)


async def on_menu_section_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_pending_action(context)
    await _show_settings_menu(update.message, _uid(update))


async def on_menu_settings_inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Открыть интерактивные inline-настройки."""
    _clear_pending_action(context)
    uid = _uid(update)
    if not _is_approved(uid):
        if update.message:
            await update.message.reply_text("⛔ Доступ не активирован. Отправьте /start")
        return
    await _show_settings_inline(update, uid)


async def on_menu_autocollect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_pending_action(context)
    await _show_autocollect_menu(update.message, _uid(update))


async def on_menu_back_sections(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_pending_action(context)
    await _show_root_sections_menu(update.message)


async def on_menu_signal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _set_pending_action(context, "signal")
    if update.message:
        await update.message.reply_text(
            "📊 <b>Анализ тикера</b>\n"
            "Введите тикер: <code>AAPL</code> или <code>SBER.ME</code>\n"
            "Опционально добавьте: <code>tape ws</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=_analysis_menu_keyboard(),
        )


async def on_menu_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _set_pending_action(context, "price")
    if update.message:
        await update.message.reply_text(
            "💲 <b>Быстрая котировка</b>\n"
            "Введите тикер: <code>AAPL</code> или <code>SBER.ME</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=_analysis_menu_keyboard(),
        )


async def on_menu_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_pending_action(context)
    await _cmd_dashboard_message_with_args(update.message, _uid(update), [])


async def on_menu_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_pending_action(context)
    if not update.message:
        return
    uid = _uid(update)
    if not uid:
        return
    prefs = load_prefs(uid)
    if not prefs.watchlist:
        await update.message.reply_text(
            "⭐ <b>Ваш watchlist пуст</b>\n"
            "Добавьте тикеры через /pick (кнопка «Добавить в watchlist»)\n"
            "или командой: <code>/watchlist add SBER.ME AAPL</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=_lists_menu_keyboard(),
        )
        return
    body = "<b>⭐ Ваш watchlist</b>\n" + "\n".join(_esc(s) for s in prefs.watchlist)
    await update.message.reply_text(
        body,
        parse_mode=ParseMode.HTML,
        reply_markup=_lists_menu_keyboard(),
    )


async def on_menu_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_pending_action(context)
    await cmd_pick(update, context)


async def on_menu_collect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_pending_action(context)
    await _cmd_collect_with_args(update, [])


async def on_menu_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_pending_action(context)
    await cmd_collect_status(update, context)


async def on_menu_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_pending_action(context)
    await cmd_export(update, context)


async def on_menu_notify_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_pending_action(context)
    if not update.message:
        return
    uid = _uid(update)
    if not uid:
        return
    prefs = load_prefs(uid)
    prefs.notify_strong_outside = True
    save_prefs(uid, prefs)
    await update.message.reply_text(
        "🔔 Уведомления о сильных сигналах: <b>включены</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=_notify_menu_keyboard(),
    )


async def on_menu_notify_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_pending_action(context)
    if not update.message:
        return
    uid = _uid(update)
    if not uid:
        return
    prefs = load_prefs(uid)
    prefs.notify_strong_outside = False
    save_prefs(uid, prefs)
    await update.message.reply_text(
        "🔕 Уведомления о сильных сигналах: <b>выключены</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=_notify_menu_keyboard(),
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_pending_action(context)
    if update.message:
        await update.message.reply_text(
            "Ок, ожидание ввода отменено.",
            reply_markup=_main_menu_keyboard(),
        )


async def on_pending_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    pending = _get_pending_action(context)
    if not pending:
        return

    text = update.message.text.strip()
    if not text:
        return
    lower = text.lower()

    # Любая кнопка меню или команда — сбросить pending и пропустить
    menu_keywords = (
        "отмена", "cancel", "меню", "menu", "главное меню",
        "назад", "помощь", "аналитика", "списки", "сбор",
        "настройки", "уведомления",
    )
    for kw in menu_keywords:
        if kw in lower:
            _clear_pending_action(context)
            return  # Пусть другие handlers обработают кнопку

    args = text.split()
    if pending == "price":
        sym, _, _, bad = sanitize_command_args(args)
        if bad or not sym:
            _clear_pending_action(context)
            await update.message.reply_text(
                "Не понял тикер. Пример: <code>AAPL</code> или <code>SBER.ME</code>\n"
                "Попробуйте ещё раз через меню.",
                parse_mode=ParseMode.HTML,
                reply_markup=_analysis_menu_keyboard(),
            )
            return
        _clear_pending_action(context)
        from stock_signal_analyzer.universe import resolve_symbol_market
        sym = resolve_symbol_market(sym)
        await _send_price_for_symbol(update.message, sym)
        return

    if pending == "signal":
        sym, _, _, bad = sanitize_command_args(args)
        if bad or not sym:
            _clear_pending_action(context)
            await update.message.reply_text(
                "Не понял тикер. Пример: <code>AAPL</code> или <code>SBER.ME</code>\n"
                "Попробуйте ещё раз через меню.",
                parse_mode=ParseMode.HTML,
                reply_markup=_analysis_menu_keyboard(),
            )
            return
        _clear_pending_action(context)
        from stock_signal_analyzer.universe import resolve_symbol_market
        sym = resolve_symbol_market(sym)
        await _cmd_signal_message_with_args(update.message, [sym])
        return

    if pending == "add_custom":
        uid = _uid(update)
        if not uid:
            return

        # Нормализуем и добавляем тикеры (автодетекция рынка)
        prefs = load_prefs(uid)
        added = []
        from stock_signal_analyzer.universe import resolve_symbol_market
        for arg in args:
            normalized = resolve_symbol_market(normalize_symbol(arg) or arg)
            if normalized and normalized not in prefs.autocollect_tickers:
                prefs.autocollect_tickers.append(normalized)
                added.append(normalized)

        save_prefs(uid, prefs)
        _clear_pending_action(context)

        if added:
            await update.message.reply_text(
                f"✅ Добавлено тикеров: {len(added)}\n"
                f"{', '.join(_esc(t) for t in added)}\n\n"
                f"Всего ваших тикеров: {len(prefs.autocollect_tickers)}",
                parse_mode=ParseMode.HTML,
                reply_markup=_autocollect_menu_keyboard(uid),
            )
        else:
            await update.message.reply_text(
                "Все указанные тикеры уже есть в списке.",
                parse_mode=ParseMode.HTML,
                reply_markup=_autocollect_menu_keyboard(uid),
            )
        return


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    uid = _uid(update)
    if not uid:
        return
    args = context.args or []
    prefs = load_prefs(uid)
    if not args:
        if not prefs.watchlist:
            await update.message.reply_text(
                "Список пуст. Пример: /watchlist add SBER.ME AAPL",
                parse_mode=ParseMode.HTML,
            )
            return
        body = "<b>Ваш список</b>\n" + "\n".join(_esc(s) for s in prefs.watchlist)
        await update.message.reply_text(body, parse_mode=ParseMode.HTML)
        return
    sub = args[0].lower()
    if sub == "add":
        for s in args[1:]:
            n = normalize_symbol(s)
            if n and n not in prefs.watchlist:
                prefs.watchlist.append(n)
        save_prefs(uid, prefs)
        await update.message.reply_text(
            "Список обновлён: " + ", ".join(_esc(s) for s in prefs.watchlist),
            parse_mode=ParseMode.HTML,
        )
        return
    if sub == "remove":
        rm = {normalize_symbol(x) for x in args[1:]}
        prefs.watchlist = [x for x in prefs.watchlist if normalize_symbol(x) not in rm]
        save_prefs(uid, prefs)
        await update.message.reply_text("Удалено. Текущий список: " + ", ".join(prefs.watchlist))
        return
    if sub == "clear":
        prefs.watchlist = []
        save_prefs(uid, prefs)
        await update.message.reply_text("Список очищен.")
        return
    for s in args:
        n = normalize_symbol(s)
        if n and n not in prefs.watchlist:
            prefs.watchlist.append(n)
    save_prefs(uid, prefs)
    await update.message.reply_text(
        "Добавлено (короткая форма). Список: " + ", ".join(prefs.watchlist)
    )


async def cmd_notify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    uid = _uid(update)
    if not uid:
        return
    args = context.args or []
    prefs = load_prefs(uid)
    if not args:
        await update.message.reply_text(
            f"Уведомления «сильный сигнал вне списка»: "
            f"{'вкл' if prefs.notify_strong_outside else 'выкл'} "
            f"(порог |score| ≥ {prefs.strong_threshold})"
        )
        return
    v = args[0].lower()
    if v in ("on", "1", "yes", "да"):
        prefs.notify_strong_outside = True
    elif v in ("off", "0", "no", "нет"):
        prefs.notify_strong_outside = False
    else:
        await update.message.reply_text("Используйте: /notify on или /notify off")
        return
    save_prefs(uid, prefs)
    await update.message.reply_text(
        f"Готово. Уведомления: {'вкл' if prefs.notify_strong_outside else 'выкл'}"
    )


# ── Сбор сигналов ────────────────────────────────────────────────────────────
# Тикеры по умолчанию для /collect (если пользователь не указал свои).
# Покрытие: РФ голубые, US голубые, дивидендные — хорошая диверсификация.
_DEFAULT_COLLECT_TICKERS: list[str] = [
    # РФ голубые
    "SBER.ME", "GAZP.ME", "LKOH.ME", "GMKN.ME", "ROSN.ME",
    "NVTK.ME", "TATN.ME", "MOEX.ME", "MGNT.ME", "CHMF.ME",
    # US голубые
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
    "META", "TSLA", "JPM", "JNJ", "V",
    # Дивидендные / разное
    "KO", "PEP", "XOM", "PFE", "T",
    "VZ", "INTC", "BAC", "WMT", "DIS",
]


def _collect_tickers_for_user(uid: int) -> list[str]:
    """Тикеры для сбора: watchlist + autocollect_tickers + default (если включены), уникальные."""
    prefs = load_prefs(uid)
    seen: set[str] = set()
    result: list[str] = []

    # Добавляем watchlist
    for s in prefs.watchlist:
        n = normalize_symbol(s)
        if n and n not in seen:
            seen.add(n)
            result.append(n)

    # Добавляем пользовательские тикеры для автосбора
    for s in prefs.autocollect_tickers:
        n = normalize_symbol(s)
        if n and n not in seen:
            seen.add(n)
            result.append(n)

    # Добавляем дефолтные, если включены
    if prefs.use_default_tickers:
        for s in _DEFAULT_COLLECT_TICKERS:
            n = normalize_symbol(s)
            if n and n not in seen:
                seen.add(n)
                result.append(n)

    return result


# ── Кэш последних сигналов для дедупликации и отслеживания изменений ──────
import threading as _thr
from dataclasses import dataclass as _dc

@_dc
class _CachedSignal:
    score: float
    tier: str
    direction: str
    ts: float  # time.time()

_signal_cache: dict[str, _CachedSignal] = {}
_signal_cache_lock = _thr.Lock()

# Пороги для записи нового сигнала (дедупликация)
_SCORE_CHANGE_THRESHOLD = 0.08   # записать если score изменился на ±0.08
_TIER_CHANGE_ALWAYS = True       # всегда записать при смене класса

# Множество уже увиденных алертов для дедупликации уведомлений
_seen_alerts: set[str] = set()


def _check_alert_and_record(sym: str, score: float, tier: str, direction: str) -> tuple[bool, str | None]:
    """
    Атомарная проверка алерта И запись в кэш под одним локом.
    Возвращает (should_record, alert_text_or_None).
    Устраняет TOCTOU race condition между _detect_alert и _should_record_signal.
    """
    import time
    with _signal_cache_lock:
        prev = _signal_cache.get(sym)
        now = time.time()
        alert = None

        if prev is not None:
            tier_rank = {"C": 0, "B": 1, "A": 2}
            old_rank = tier_rank.get(prev.tier, 0)
            new_rank = tier_rank.get(tier, 0)
            if new_rank > old_rank:
                alert_text = f"⬆️ {sym}: класс {prev.tier} → {tier} (score {prev.score:+.2f} → {score:+.2f})"
                alert_key = f"upgrade:{sym}:{tier}"
                if alert_key not in _seen_alerts:
                    _seen_alerts.add(alert_key)
                    alert = alert_text
            elif new_rank < old_rank:
                alert_text = f"⬇️ {sym}: класс {prev.tier} → {tier} (score {prev.score:+.2f} → {score:+.2f})"
                alert_key = f"downgrade:{sym}:{tier}"
                if alert_key not in _seen_alerts:
                    _seen_alerts.add(alert_key)
                    alert = alert_text
            else:
                delta = score - prev.score
                if abs(delta) >= 0.15:
                    arrow = "📈" if delta > 0 else "📉"
                    alert_text = f"{arrow} {sym}: score {prev.score:+.2f} → {score:+.2f} (Δ{delta:+.2f})"
                    alert_key = f"delta:{sym}:{score:.2f}"
                if alert_key not in _seen_alerts:
                    _seen_alerts.add(alert_key)
                    # Prune old entries to prevent memory leak
                    if len(_seen_alerts) > 1000:
                        _seen_alerts.clear()
                    alert = alert_text

        # Решаем, нужно ли записать сигнал
        should_record = False
        if prev is None:
            should_record = True
        elif _TIER_CHANGE_ALWAYS and prev.tier != tier:
            should_record = True
        elif prev.direction != direction:
            should_record = True
        elif abs(score - prev.score) >= _SCORE_CHANGE_THRESHOLD:
            should_record = True
        elif now - prev.ts > 14400:
            should_record = True

        if should_record:
            _signal_cache[sym] = _CachedSignal(score=score, tier=tier, direction=direction, ts=now)

        return should_record, alert


def _collect_signals_smart(tickers: list[str]) -> tuple[int, int, list[str], list[str]]:
    """
    Умный сбор: анализирует тикеры, записывает только при значимых изменениях.
    Атомарная проверка алерта + запись под одним локом (без TOCTOU race).
    Возвращает (ok_count, err_count, errors, alerts).
    """
    ok = 0
    errors: list[str] = []
    alerts: list[str] = []
    for sym in tickers:
        try:
            report = build_report(sym, fast_mode=True)
            direction = "long" if report.score > 0.05 else ("short" if report.score < -0.05 else "neutral")
            should_record, alert = _check_alert_and_record(sym, report.score, report.signal_tier, direction)
            if alert:
                alerts.append(alert)
            if should_record:
                ok += 1
        except Exception as e:
            errors.append(f"{sym}: {e}")
    return ok, len(errors), errors, alerts


_SIGNAL_COLLECT_TIMEOUT_SEC = int(os.environ.get("SIGNAL_COLLECT_TIMEOUT_SEC", "60"))


def _collect_signals_sync(tickers: list[str], filter_type: str = "balanced") -> tuple[int, int, list[str]]:
    """
    Анализирует каждый тикер через build_report (который сам пишет в SSA_SIGNAL_LOG).
    Каждый тикер имеет таймаут — зависший не блокирует остальные.
    Возвращает (ok_count, err_count, errors_list).
    """
    import concurrent.futures
    ok = 0
    errors: list[str] = []
    for sym in tickers:
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(build_report, sym, fast_mode=True, filter_type=filter_type)
                future.result(timeout=_SIGNAL_COLLECT_TIMEOUT_SEC)
            ok += 1
        except concurrent.futures.TimeoutError:
            errors.append(f"{sym}: timeout ({_SIGNAL_COLLECT_TIMEOUT_SEC}s)")
        except Exception as e:
            errors.append(f"{sym}: {e}")
    return ok, len(errors), errors


async def _cmd_collect_with_args(update: Update, args: list[str]) -> None:
    """
    /collect [TICKER ...] — прогнать анализ по тикерам и записать в SSA_SIGNAL_LOG.
    Без аргументов: watchlist + 30 дефолтных тикеров.
    """
    if not update.message:
        return

    log_p = log_path_from_env()
    if not log_p:
        await update.message.reply_text(
            "⚠️ SSA_SIGNAL_LOG не задан. Задайте переменную окружения:\n"
            "<code>SSA_SIGNAL_LOG=/path/to/signals.jsonl</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    uid = _uid(update)
    if args:
        tickers = [normalize_symbol(a) for a in args if normalize_symbol(a)]
    else:
        tickers = _collect_tickers_for_user(uid)

    count = len(tickers)
    await update.message.reply_text(
        f"📊 Запускаю сбор сигналов: {count} тикеров.\n"
        f"Это займёт ~{count * 5}–{count * 10} секунд…",
        parse_mode=ParseMode.HTML,
    )
    await update.message.reply_chat_action(action=ChatAction.TYPING)

    prefs = load_prefs(uid)

    loop = asyncio.get_running_loop()
    ok, errs, err_list = await loop.run_in_executor(
        None, lambda: _collect_signals_sync(tickers, filter_type=prefs.signal_filter_type),
    )

    lines = [f"✅ Сбор завершён: {ok} сигналов записано, {errs} ошибок."]
    lines.append(f"Файл: <code>{_esc(log_p)}</code>")

    # Подсчитаем общее количество записей в файле
    try:
        with open(log_p, encoding="utf-8") as f:
            total_lines = sum(1 for line in f if line.strip())
        lines.append(f"Всего записей в логе: <b>{total_lines}</b>")
        if total_lines >= 50:
            lines.append("🎯 Набралось 50+ сигналов — можно выгрузить: /export")
        else:
            lines.append(f"До 50 сигналов осталось: {50 - total_lines}")
    except OSError:
        pass

    if err_list:
        lines.append("\nОшибки:")
        for e in err_list[:10]:
            lines.append(f"  {_esc(e)}")
        if len(err_list) > 10:
            lines.append(f"  …и ещё {len(err_list) - 10}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_collect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_approved(_uid(update)):
        if update.message:
            await update.message.reply_text("⛔ Доступ не активирован. Отправьте /start")
        return
    await _cmd_collect_with_args(update, context.args or [])


async def cmd_collect_ru(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _cmd_collect_with_args(update, _args_from_text_command(update))


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/export — отправить файл SSA_SIGNAL_LOG как документ."""
    if not update.message:
        return

    log_p = log_path_from_env()
    if not log_p:
        await update.message.reply_text(
            "⚠️ SSA_SIGNAL_LOG не задан.",
            parse_mode=ParseMode.HTML,
        )
        return

    if not os.path.exists(log_p):
        await update.message.reply_text(
            "Файл лога ещё не создан. Сначала запустите /collect",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        with open(log_p, encoding="utf-8") as f:
            total = sum(1 for line in f if line.strip())
    except OSError:
        total = 0

    await update.message.reply_text(
        f"📤 Отправляю файл с {total} сигналами…",
        parse_mode=ParseMode.HTML,
    )

    try:
        with open(log_p, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename="signals.jsonl",
                caption=f"SSA Signal Log — {total} записей",
            )
    except Exception as e:
        await update.message.reply_text(
            f"Ошибка при отправке: {_esc(str(e))}",
            parse_mode=ParseMode.HTML,
        )


async def cmd_export_ru(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_export(update, context)


async def cmd_collect_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status — сколько сигналов собрано."""
    if not update.message:
        return
    log_p = log_path_from_env()
    if not log_p or not os.path.exists(log_p):
        await update.message.reply_text("Лог пуст или SSA_SIGNAL_LOG не задан.")
        return
    try:
        import json as _json
        tiers: dict[str, int] = {"A": 0, "B": 0, "C": 0}
        total = 0
        symbols: set[str] = set()
        with open(log_p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    row = _json.loads(line)
                    tier = row.get("signal_tier", "?")
                    tiers[tier] = tiers.get(tier, 0) + 1
                    symbols.add(row.get("symbol", "?"))
                except Exception:
                    pass
        lines = [
            f"📊 <b>Статус сбора</b>",
            f"Всего сигналов: <b>{total}</b>",
            f"Уникальных тикеров: {len(symbols)}",
            f"Класс A: {tiers.get('A', 0)} | B: {tiers.get('B', 0)} | C: {tiers.get('C', 0)}",
        ]
        if total >= 50:
            lines.append("✅ Достаточно для анализа. Выгрузить: /export")
        else:
            lines.append(f"До 50 сигналов осталось: {50 - total}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {_esc(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_collect_status_ru(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_collect_status(update, context)


async def cmd_learning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/learning — показать отчёт обучения."""
    if not update.message:
        return
    uid = _uid(update)
    try:
        from stock_signal_analyzer.llm_learning import load_learning_state, format_learning_report
        state = load_learning_state()
        if not state:
            await update.message.reply_text(
                "🧠 <b>Обучение</b>\n\n"
                "Обучение ещё не проводилось или недостаточно данных.\n"
                "Система начнёт обучение после 20+ сигналов с результатами.",
                parse_mode=ParseMode.HTML,
            )
            return
        report = format_learning_report()
        await update.message.reply_text(report, parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка: {_esc(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stats — статистика исходов сигналов."""
    if not update.message:
        return
    try:
        from stock_signal_analyzer.outcome_tracker import OutcomeTracker
        tracker = OutcomeTracker()
        stats = tracker.get_statistics()
        if not stats:
            await update.message.reply_text(
                "📈 <b>Статистика исходов</b>\n\n"
                "Нет данных. Результаты появятся после проверки сигналов outcome tracker'ом.",
                parse_mode=ParseMode.HTML,
            )
            return
        wr = stats['win_rate'] * 100
        pf = stats['profit_factor']
        aw = stats['avg_win_pct']
        al = stats['avg_loss_pct']
        tpnl = stats['total_pnl_pct']
        total = stats['total_signals']
        wins = stats['winning_trades']
        losses = stats['losing_trades']
        body = (
            f"📈 <b>Статистика исходов</b>\n\n"
            f"Всего исходов: <b>{total}</b>\n"
            f"Выигрыши: {wins} | Проигрыши: {losses}\n"
            f"Win rate: <b>{wr:.1f}%</b>\n"
            f"Profit factor: <b>{pf:.2f}</b>\n"
            f"Avg win: {aw:+.2f}% | Avg loss: {al:+.2f}%\n"
            f"Total PnL: <b>{tpnl:+.2f}%</b>"
        )
        await update.message.reply_text(body, parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка: {_esc(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_force_learn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/force_learn — запустить обучение сейчас (только админ)."""
    if not update.message:
        return
    uid = _uid(update)
    if not _is_admin(uid):
        await update.message.reply_text("⛔ Только для администратора.")
        return
    try:
        msg = await update.message.reply_text("🔄 Запускаю обучение…", parse_mode=ParseMode.HTML)
        from stock_signal_analyzer.outcome_tracker import OutcomeTracker
        from stock_signal_analyzer.llm_learning import run_learning_cycle, format_learning_report
        tracker = OutcomeTracker()
        tracker.check_all_outcomes()
        state = run_learning_cycle(force=True)
        if state:
            report = format_learning_report()
            await msg.edit_text(report if report else "✅ Обучение завершено.", parse_mode=ParseMode.HTML)
        else:
            await msg.edit_text("✅ Обучение завершено (недостаточно данных).", parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка: {_esc(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/backtest — показать отчёт бэктеста и валидацию сигналов."""
    if not update.message:
        return
    try:
        from stock_signal_analyzer.backtest_validator import (
            BacktestValidator, format_backtest_report, format_validation_result, get_validator
        )
        validator = BacktestValidator()
        report = validator.generate_report()
        if not report:
            await update.message.reply_text(
                "🧪 <b>Бэктест</b>\n\n"
                "Недостаточно данных. Бэктест появится после 15+ закрытых сигналов.",
                parse_mode=ParseMode.HTML,
            )
            return

        body = format_backtest_report(report)
        # body — строка, отправляем целиком (или чанками по 4096 если длинная)
        for i in range(0, max(1, len(body)), 4096):
            chunk = body[i:i + 4096].strip()
            if chunk:
                await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка: {_esc(str(e))}", parse_mode=ParseMode.HTML)


async def on_menu_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_backtest(update, context)


async def on_menu_learning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    if update.message:
        await _show_learning_menu(update.message, uid)


async def on_menu_show_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_learning(update, context)


async def on_menu_show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_stats(update, context)


async def on_menu_toggle_learn_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    uid = _uid(update)
    prefs = load_prefs(uid)
    prefs.receive_learning_report = not prefs.receive_learning_report
    save_prefs(uid, prefs)
    icon = "✅" if prefs.receive_learning_report else "❌"
    await update.message.reply_text(f"🧠 Learning report {icon}", reply_markup=_learning_menu_keyboard(uid))


async def on_menu_show_custom_tickers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик кнопки '📋 Показать текущие'."""
    if not update.message:
        return
    uid = _uid(update)
    if not uid:
        return

    prefs = load_prefs(uid)

    lines = ["📋 <b>Текущая конфигурация автосбора</b>\n"]

    # Дефолтные тикеры
    default_status = "✅ включены" if prefs.use_default_tickers else "❌ выключены"
    lines.append(f"<b>Дефолтные тикеры (30):</b> {default_status}")

    # Пользовательские тикеры
    if prefs.autocollect_tickers:
        lines.append(f"\n<b>Ваши тикеры ({len(prefs.autocollect_tickers)}):</b>")
        lines.append(", ".join(_esc(t) for t in prefs.autocollect_tickers))
    else:
        lines.append("\n<b>Ваши тикеры:</b> нет")

    # Watchlist
    if prefs.watchlist:
        lines.append(f"\n<b>Watchlist ({len(prefs.watchlist)}):</b>")
        lines.append(", ".join(_esc(t) for t in prefs.watchlist))
    else:
        lines.append("\n<b>Watchlist:</b> пуст")

    # Итого
    total_tickers = _collect_tickers_for_user(uid)
    lines.append(f"\n<b>Итого тикеров для автосбора: {len(total_tickers)}</b>")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=_autocollect_menu_keyboard(uid),
    )


async def on_menu_clear_custom_tickers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик кнопки '🗑️ Очистить свои тикеры'."""
    if not update.message:
        return
    uid = _uid(update)
    if not uid:
        return

    prefs = load_prefs(uid)
    prefs.autocollect_tickers = []
    save_prefs(uid, prefs)

    await update.message.reply_text(
        "🗑️ Ваши тикеры для автосбора очищены.",
        parse_mode=ParseMode.HTML,
        reply_markup=_autocollect_menu_keyboard(uid),
    )


async def on_menu_add_custom_tickers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик кнопки 'Добавить свои тикеры'."""
    if not update.message:
        return
    uid = _uid(update)
    if not uid:
        return
    _set_pending_action(context, "add_custom")
    await update.message.reply_text(
        "Введите тикеры через пробел (например: AAPL MSFT TSLA):",
        parse_mode=ParseMode.HTML,
        reply_markup=_autocollect_menu_keyboard(uid),
    )


async def on_menu_toggle_default_tickers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик кнопки переключения дефолтных тикеров."""
    if not update.message:
        return
    uid = _uid(update)
    if not uid:
        return
    prefs = load_prefs(uid)
    prefs.use_default_tickers = not prefs.use_default_tickers
    save_prefs(uid, prefs)
    status = "включены" if prefs.use_default_tickers else "выключены"
    await update.message.reply_text(
        f"✅ Дефолтные тикеры <b>{status}</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=_autocollect_menu_keyboard(uid),
    )


async def on_menu_force_learn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик кнопки 'Принудительное обучение'."""
    if not update.message:
        return
    await cmd_force_learn(update, context)


async def on_menu_back_to_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик кнопки '⬅️ Назад в настройки'."""
    if not update.message:
        return
    uid = _uid(update)
    await _show_settings_menu(update.message, uid)


async def on_menu_back_to_learning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик кнопки '⬅️ Назад в обучение'."""
    if not update.message:
        return
    uid = _uid(update)
    await _show_learning_menu(update.message, uid)


async def notify_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Периодически: сильные сигналы по watchlist + вне списка."""
    bot = context.application.bot
    loop = asyncio.get_running_loop()
    min_tier = (os.environ.get("NOTIFY_MIN_TIER") or "").strip().upper()

    for uid in all_user_ids():
        prefs = load_prefs(uid)
        if not prefs.notify_strong_outside:
            continue
        # Не пропускаем пользователя если watchlist пуст —
        # уведомления вне watchlist всё равно должны работать.

        changed = False

        # ── 1. Уведомления по WATCHLIST (сильные сигналы по вашим тикерам) ──
        for sym in list(prefs.watchlist):
            if not can_notify_again(prefs, sym):
                continue
            try:
                rep = await loop.run_in_executor(
                    None,
                    lambda s=sym, ft=prefs.signal_filter_type: build_report(s, fast_mode=True, filter_type=ft),
                )
            except Exception:
                continue
            tier = getattr(rep, "signal_tier", "C")
            # Уведомляем по tier A и B (не только A)
            if tier not in ("A", "B"):
                continue
            if abs(rep.score) < prefs.strong_threshold:
                continue
            # Отправить уведомление
            text = (
                f"⭐ <b>Сигнал по вашему watchlist</b>\n"
                f"{_esc(sym)} — класс <b>{tier}</b> | score {rep.score:+.3f}\n\n"
            ) + format_signal_report(rep)
            try:
                for chunk in split_telegram_html(text):
                    await bot.send_message(chat_id=uid, text=chunk, parse_mode=ParseMode.HTML)
                mark_notified(prefs, sym)
                changed = True
                try:
                    from stock_signal_analyzer.max_notify import send_signal_to_max, max_available
                    if max_available():
                        await send_signal_to_max(sym, tier, rep.score,
                            rep.trade_plan.direction if rep.trade_plan else "neutral", rep.verdict)
                except Exception:
                    pass
            except Exception:
                log.exception("watchlist notify uid=%s sym=%s", uid, sym)

        # ── 2. Уведомления ВНЕ watchlist (как раньше) ──
        try:
            strong = await loop.run_in_executor(
                None,
                lambda wl=list(prefs.watchlist), thr=prefs.strong_threshold: scan_strong_outside_watchlist(wl, thr),
            )
        except Exception:
            log.exception("scan outside uid=%s", uid)
            strong = []

        for sym, rep in strong:
            if not can_notify_again(prefs, sym):
                continue
            if min_tier == "A" and getattr(rep, "signal_tier", "") != "A":
                continue
            text = format_outside_notification(sym, rep)
            try:
                for chunk in split_telegram_html(text):
                    await bot.send_message(chat_id=uid, text=chunk, parse_mode=ParseMode.HTML)
                mark_notified(prefs, sym)
                changed = True
                try:
                    from stock_signal_analyzer.max_notify import send_signal_to_max, max_available
                    if max_available():
                        await send_signal_to_max(sym, getattr(rep, "signal_tier", "?"), rep.score,
                            rep.trade_plan.direction if rep.trade_plan else "neutral", rep.verdict)
                except Exception:
                    pass
            except Exception:
                log.exception("send notify uid=%s sym=%s", uid, sym)

        if changed:
            save_prefs(uid, prefs)


async def autocollect_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Периодический автосбор сигналов."""
    try:
        from stock_signal_analyzer.scheduler import run_signal_collection
        run_signal_collection()
    except Exception:
        log.exception("autocollect_job failed")


async def learning_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Периодический цикл самообучения."""
    try:
        from stock_signal_analyzer.scheduler import run_learning_cycle
        run_learning_cycle()
    except Exception:
        log.exception("learning_job failed")


async def post_init(application: Application) -> None:
    # Регистрация команд в меню Telegram (появляются при нажатии /)
    from telegram import BotCommand
    commands = [
        BotCommand("start", "Главное меню"),
        BotCommand("signal", "Полный анализ тикера — /signal AAPL"),
        BotCommand("price", "Актуальная цена — /price SBER.ME"),
        BotCommand("dashboard", "Свод по watchlist"),
        BotCommand("watchlist", "Управление watchlist"),
        BotCommand("pick", "Подбор тикеров по категориям"),
        BotCommand("collect", "Запустить сбор сигналов"),
        BotCommand("status", "Статус сбора"),
        BotCommand("export", "Выгрузить лог сигналов"),
        BotCommand("learning", "Отчёт обучения и LLM"),
        BotCommand("stats", "Статистика исходов"),
        BotCommand("backtest", "Бэктест и валидация сигналов"),
        BotCommand("help", "Помощь"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        log.info("Команды бота зарегистрированы (%d шт.)", len(commands))
    except Exception as e:
        log.warning("set_my_commands failed: %s", e)

    jq = application.job_queue
    if jq is None:
        log.warning(
            "JobQueue недоступен — установите: pip install apscheduler 'python-telegram-bot[job-queue]' "
            "(без этого уведомления по расписанию отключены; команды /signal и /dashboard работают)."
        )
        return
    sec = int(os.environ.get("NOTIFY_INTERVAL_SEC", "3600"))
    jq.run_repeating(notify_job, interval=sec, first=90, name="outside_signals")
    log.info("JobQueue: проверка сильных сигналов вне списка каждые %s с", sec)

    collect_sec = int(os.environ.get("COLLECT_INTERVAL_SEC", "900"))
    if collect_sec > 0:
        jq.run_repeating(autocollect_job, interval=collect_sec, first=120, name="autocollect")
        log.info("JobQueue: мониторинг сигналов каждые %s с", collect_sec)

    # Самообучение: проверка исходов + пересчёт весов каждые 6 часов
    learn_sec = int(os.environ.get("LEARN_INTERVAL_SEC", "21600"))
    if learn_sec > 0:
        jq.run_repeating(learning_job, interval=learn_sec, first=300, name="learning")
        log.info("JobQueue: самообучение каждые %s с", learn_sec)


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler: log all unhandled exceptions and notify the user."""
    log.error("Telegram update caused error: %s (update: %s)", context.error, update)
    if update and isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Произошла ошибка при обработке запроса. Команда не выполнена."
            )
        except Exception:
            pass


def main() -> int:
    import signal as _sig

    validate_telegram_config()

    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
    if not token:
        log.error("Задайте TELEGRAM_BOT_TOKEN (или BOT_TOKEN)")
        return 1

    # Поддержка Cloudflare Workers прокси для обхода блокировки Telegram API
    base_url = os.environ.get("TELEGRAM_BASE_URL")
    proxy_url = os.environ.get("TELEGRAM_PROXY")
    builder = Application.builder().token(token).post_init(post_init).concurrent_updates(4)
    if base_url:
        base_url = base_url.rstrip("/")
        builder = builder.base_url(base_url).base_file_url(base_url + "/file/bot")
        log.info("Telegram API через base_url прокси: %s", base_url)
    if proxy_url:
        from telegram.request import HTTPXRequest
        builder = builder.request(HTTPXRequest(proxy=proxy_url, connect_timeout=20.0, read_timeout=30.0))
        builder = builder.get_updates_request(HTTPXRequest(proxy=proxy_url, connect_timeout=20.0, read_timeout=30.0, pool_timeout=10.0))
        log.info("Telegram API через SOCKS5 прокси: %s", proxy_url.split("@")[-1] if "@" in proxy_url else "***")

    app = builder.build()
    app.add_error_handler(_error_handler)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("menu", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    # Admin commands
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("deny", cmd_deny))
    app.add_handler(CommandHandler("users", cmd_users))
    # Plan selection & admin actions (callbacks)
    app.add_handler(CallbackQueryHandler(_on_plan_selected, pattern=r"^plan\|"))
    app.add_handler(CallbackQueryHandler(_on_admin_action, pattern=r"^adm\|"))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("quote", cmd_price))
    app.add_handler(CommandHandler("signal", cmd_signal))
    app.add_handler(CommandHandler("pick", cmd_pick))
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("notify", cmd_notify))
    app.add_handler(CommandHandler("collect", cmd_collect))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("status", cmd_collect_status))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("learning", cmd_learning))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("force_learn", cmd_force_learn))
    app.add_handler(CommandHandler("backtest", cmd_backtest))
    # Settings callbacks
    app.add_handler(CallbackQueryHandler(_on_settings_callback, pattern=r"^set\|"))
    app.add_handler(
        MessageHandler(filters.Regex(r"^/анализ(?:@\w+)?(?:\s+.*)?$"), cmd_signal_ru)
    )
    app.add_handler(
        MessageHandler(filters.Regex(r"^/свод(?:@\w+)?(?:\s+.*)?$"), cmd_dashboard_ru)
    )
    app.add_handler(
        MessageHandler(filters.Regex(r"^/сбор(?:@\w+)?(?:\s+.*)?$"), cmd_collect_ru)
    )
    app.add_handler(
        MessageHandler(filters.Regex(r"^/выгрузка(?:@\w+)?(?:\s+.*)?$"), cmd_export_ru)
    )
    app.add_handler(
        MessageHandler(filters.Regex(r"^/статус(?:@\w+)?(?:\s+.*)?$"), cmd_collect_status_ru)
    )
    app.add_handler(
        MessageHandler(filters.Regex(r"^(?i:меню|menu)$"), cmd_start)
    )
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Аналитика$"), on_menu_section_analysis))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Списки и подбор$"), on_menu_section_lists))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Сбор и экспорт$"), on_menu_section_collect))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Настройки$"), on_menu_section_settings))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Уведомления$"), on_menu_section_notify))
    app.add_handler(MessageHandler(filters.Regex(r"(?:[^\w]+\s*)?Интерактивные настройки"), on_menu_settings_inline))
    app.add_handler(MessageHandler(filters.Regex(r"(?:[^\w]+\s*)?Настройка автосбора"), on_menu_autocollect))
    app.add_handler(MessageHandler(filters.Regex(r"(?:[^\w]+\s*)?Обучение$"), on_menu_learning))
    app.add_handler(MessageHandler(filters.Regex(r"(?:[^\w]+\s*)?Показать отчёт"), on_menu_show_report))
    app.add_handler(MessageHandler(filters.Regex(r"(?:[^\w]+\s*)?Статистика исходов"), on_menu_show_stats))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Получать learning report$"), on_menu_toggle_learn_report))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Принудительное обучение$"), on_menu_force_learn))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Бэктест$"), on_menu_backtest))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Назад в обучение$"), on_menu_back_to_learning))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Назад в разделы$"), on_menu_back_sections))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Назад в настройки$"), on_menu_back_to_settings))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Главное меню$"), cmd_start))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Помощь$"), cmd_help))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Анализ тикера$"), on_menu_signal))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Цена тикера$"), on_menu_price))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Свод по рынку$"), on_menu_dashboard))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Мой watchlist$"), on_menu_watchlist))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Подбор тикеров$"), on_menu_pick))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Сбор сигналов$"), on_menu_collect))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Статус сбора$"), on_menu_status))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Выгрузить лог$"), on_menu_export))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Уведомления ВКЛ$"), on_menu_notify_on))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Уведомления ВЫКЛ$"), on_menu_notify_off))
    app.add_handler(MessageHandler(filters.Regex(r"^[✅❌]\s*Дефолтные тикеры"), on_menu_toggle_default_tickers))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Добавить свои тикеры$"), on_menu_add_custom_tickers))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Показать текущие$"), on_menu_show_custom_tickers))
    app.add_handler(MessageHandler(filters.Regex(r"^(?:[^\w]+\s*)?Очистить свои тикеры$"), on_menu_clear_custom_tickers))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_pending_text)
    )
    app.add_handler(CallbackQueryHandler(on_pick_callback, pattern=r"^pk\|"))

    def _graceful(signum, _frame):
        name = _sig.Signals(signum).name if hasattr(_sig, "Signals") else str(signum)
        log.info("Получен %s — корректная остановка…", name)
        app.stop_running()

    for s in (_sig.SIGTERM, _sig.SIGINT):
        _sig.signal(s, _graceful)

    log.info("Бот запущен (polling)")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    log.info("Бот остановлен")
    return 0


if __name__ == "__main__":
    sys.exit(main())
