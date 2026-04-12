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
    load_prefs,
    mark_notified,
    normalize_symbol,
    save_prefs,
)
from stock_signal_analyzer.universe import RU_BLUE_CHIPS, US_BLUE_CHIPS

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("telegram_bot")


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


def _main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("/signal"), KeyboardButton("/dashboard")],
            [KeyboardButton("/price"), KeyboardButton("/watchlist")],
            [KeyboardButton("/pick"), KeyboardButton("меню")],
            [KeyboardButton("/collect"), KeyboardButton("/status")],
            [KeyboardButton("/export"), KeyboardButton("/notify on"), KeyboardButton("/notify off")],
            [KeyboardButton("/help")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=False,
    )


def _uid(update: Update) -> int:
    if update.effective_user is None:
        return 0
    return int(update.effective_user.id)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    text = (
        "Привет. <b>Полный анализ</b>: техника, импульс, новости, объём, онлайн, макро.\n\n"
        "<b>Котировки</b>\n"
        "/price TICKER — быстрая цена\n\n"
        "<b>Один тикер</b>\n"
        "/signal TICKER [tape] [ws]\n\n"
        "<b>Свод по одному или нескольким тикерам + секции рынка</b>\n"
        "/dashboard [TICKER …] [tape] [ws]\n"
        "— объединяет ваш <b>список</b> и указанные тикеры; секции: "
        "🇷🇺 голубые РФ, 🌐 иностранные голубые, 💰 дивидендные, прочие.\n"
        "Если есть <b>сильный сигнал</b> по бумаге <b>не из вашего списка</b> — показывается отдельным блоком.\n\n"
        "<b>Список пользователя</b> (для свода и уведомлений «вне списка»)\n"
        "/watchlist — показать\n"
        "/watchlist add TICKER …\n"
        "/watchlist remove TICKER\n"
        "/watchlist clear\n\n"
        "/notify on|off — уведомления о сильных сигналах вне списка\n\n"
        "<b>Сбор сигналов для анализа</b>\n"
        "/collect [TICKER …] — прогнать анализ и записать в лог\n"
        "/status — сколько сигналов собрано\n"
        "/export — выгрузить файл лога (JSONL)\n\n"
        "/pick — выбрать тикер из готовых списков и отраслей\n"
        "/help — это сообщение\n\n"
        "⬇️ Используйте кнопки меню внизу для быстрого выбора команды."
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=_main_menu_keyboard(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def _send_price_for_symbol(message, sym: str) -> None:
    if not message:
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
    body = format_quick_quote(
        snap.symbol,
        snap.company_name,
        snap.last_close,
        snap.currency,
        profile.label,
    )
    await message.reply_text(body, parse_mode=ParseMode.HTML)


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    args = context.args or []
    sym, _, _, bad = sanitize_command_args(args)
    if bad or not sym:
        await update.message.reply_text(
            "Укажите тикер: /price AAPL или /price SBER.ME",
            parse_mode=ParseMode.HTML,
        )
        return
    await _send_price_for_symbol(update.message, sym)


async def _cmd_signal_message_with_args(message, args: list[str]) -> None:
    if not message:
        return
    sym, tape, ws, bad = sanitize_command_args(args)
    if bad or not sym:
        await message.reply_text(
            "Пример: /signal AAPL или /signal SBER.ME tape ws",
            parse_mode=ParseMode.HTML,
        )
        return
    await message.reply_chat_action(action=ChatAction.TYPING)
    loop = asyncio.get_running_loop()
    try:
        report = await loop.run_in_executor(
            None,
            lambda: build_report(
                sym,
                volume_tape_ws=tape,
                use_finnhub_ws=ws,
                ws_seconds=8.0,
            ),
        )
    except Exception as e:
        log.exception("signal")
        await message.reply_text(_esc(f"Ошибка: {e}"), parse_mode=ParseMode.HTML)
        return
    html_text = format_signal_report(report)
    for chunk in split_telegram_html(html_text):
        await message.reply_text(chunk, parse_mode=ParseMode.HTML)


def _pick_categories_markup() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🇷🇺 Голубые РФ", callback_data="pk|c|ru|0"),
            InlineKeyboardButton("🌐 Голубые США", callback_data="pk|c|us|0"),
        ],
        [
            InlineKeyboardButton("💰 Дивидендные", callback_data="pk|c|div|0"),
            InlineKeyboardButton("🖥️ Технологии", callback_data="pk|c|tech|0"),
        ],
        [
            InlineKeyboardButton("🏦 Финансы", callback_data="pk|c|fin|0"),
            InlineKeyboardButton("🛢️ Энергия", callback_data="pk|c|en|0"),
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
        row = [InlineKeyboardButton(s, callback_data=f"pk|t|{s}|{group_id}|{page}") for s in pair]
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
                InlineKeyboardButton("📊 Анализ", callback_data=f"pk|a|sig|{sym}|{group_id}|{page}"),
                InlineKeyboardButton("💲 Цена", callback_data=f"pk|a|price|{sym}|{group_id}|{page}"),
            ],
            [
                InlineKeyboardButton("⭐ В watchlist", callback_data=f"pk|a|watch|{sym}|{group_id}|{page}"),
                InlineKeyboardButton("🧾 В dashboard", callback_data=f"pk|a|dash|{sym}|{group_id}|{page}"),
            ],
            [InlineKeyboardButton("🔙 К списку", callback_data=f"pk|c|{group_id}|{page}")],
        ]
    )


async def cmd_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "Выберите категорию тикеров:",
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
        await query.edit_message_text("Выберите категорию тикеров:", reply_markup=_pick_categories_markup())
        return
    if action == "c" and len(parts) >= 4:
        group_id = parts[2]
        page = int(parts[3]) if parts[3].isdigit() else 0
        title, symbols = _PICK_GROUPS.get(group_id, ("Категория", []))
        if not symbols:
            await query.edit_message_text("Список пуст.")
            return
        await query.edit_message_text(
            f"{title}\nВыберите тикер:",
            reply_markup=_pick_tickers_markup(group_id, page),
        )
        return
    if action == "t" and len(parts) >= 5:
        sym = parts[2]
        group_id = parts[3]
        page = int(parts[4]) if parts[4].isdigit() else 0
        await query.edit_message_text(
            f"Тикер: <b>{_esc(sym)}</b>\nЧто сделать?",
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
            await _cmd_signal_message_with_args(msg, [sym])
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

        await query.edit_message_reply_markup(reply_markup=_pick_actions_markup(sym, group_id, page))
        return


async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    if not merged:
        await message.reply_text(
            "Нет тикеров: добавьте <code>/watchlist add SBER.ME AAPL</code> "
            "или укажите: <code>/dashboard AAPL MSFT</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    await message.reply_chat_action(action=ChatAction.TYPING)
    loop = asyncio.get_running_loop()

    def _sync():
        b = build_dashboard(
            merged,
            volume_tape_ws=tape,
            use_finnhub_ws=ws,
            ws_seconds=8.0,
        )
        o = scan_strong_outside_watchlist(merged, prefs.strong_threshold)
        return b, o

    try:
        bundle, outside = await loop.run_in_executor(None, _sync)
    except Exception as e:
        log.exception("dashboard")
        await message.reply_text(_esc(f"Ошибка: {e}"), parse_mode=ParseMode.HTML)
        return
    mset = {normalize_symbol(x) for x in merged}
    outside = [(s, r) for s, r in outside if normalize_symbol(s) not in mset]
    html_text = format_dashboard_bundle(bundle, outside)
    for chunk in split_telegram_html(html_text):
        await message.reply_text(chunk, parse_mode=ParseMode.HTML)


async def cmd_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _cmd_dashboard_message_with_args(update.message, _uid(update), context.args or [])


async def cmd_dashboard_ru(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _cmd_dashboard_message_with_args(update.message, _uid(update), _args_from_text_command(update))


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
    """Тикеры для сбора: watchlist + default, уникальные."""
    prefs = load_prefs(uid)
    seen: set[str] = set()
    result: list[str] = []
    for s in list(prefs.watchlist) + _DEFAULT_COLLECT_TICKERS:
        n = normalize_symbol(s)
        if n and n not in seen:
            seen.add(n)
            result.append(n)
    return result


def _collect_signals_sync(tickers: list[str]) -> tuple[int, int, list[str]]:
    """
    Анализирует каждый тикер через build_report (который сам пишет в SSA_SIGNAL_LOG).
    Возвращает (ok_count, err_count, errors_list).
    """
    ok = 0
    errors: list[str] = []
    for sym in tickers:
        try:
            build_report(sym)
            ok += 1
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

    loop = asyncio.get_running_loop()
    ok, errs, err_list = await loop.run_in_executor(
        None, lambda: _collect_signals_sync(tickers),
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


async def autocollect_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Автоматический сбор сигналов по расписанию (JobQueue)."""
    log_p = log_path_from_env()
    if not log_p:
        return

    tickers = list(_DEFAULT_COLLECT_TICKERS)

    for uid in all_user_ids():
        prefs = load_prefs(uid)
        for s in prefs.watchlist:
            n = normalize_symbol(s)
            if n and n not in tickers:
                tickers.append(n)

    log.info("autocollect: запуск, %d тикеров", len(tickers))
    ok, errs, _ = _collect_signals_sync(tickers)
    log.info("autocollect: ok=%d, err=%d", ok, errs)

    try:
        with open(log_p, encoding="utf-8") as f:
            total = sum(1 for line in f if line.strip())
    except OSError:
        total = 0

    if total >= 50:
        bot = context.application.bot
        for uid in all_user_ids():
            try:
                await bot.send_message(
                    chat_id=uid,
                    text=f"📊 Автосбор завершён: +{ok} сигналов (всего {total}).\n"
                         f"🎯 Набралось 50+ — выгрузите /export для анализа.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass


async def notify_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Периодически: сильный сигнал по бумаге не из списка пользователя."""
    bot = context.application.bot
    for uid in all_user_ids():
        prefs = load_prefs(uid)
        if not prefs.notify_strong_outside or not prefs.watchlist:
            continue
        try:
            strong = scan_strong_outside_watchlist(prefs.watchlist, prefs.strong_threshold)
        except Exception:
            log.exception("scan outside uid=%s", uid)
            continue
        changed = False
        min_tier = (os.environ.get("NOTIFY_MIN_TIER") or "").strip().upper()
        for sym, rep in strong:
            if not can_notify_again(prefs, sym):
                continue
            if min_tier == "A" and getattr(rep, "signal_tier", "") != "A":
                continue
            text = format_outside_notification(sym, rep)
            try:
                for chunk in split_telegram_html(text):
                    await bot.send_message(
                        chat_id=uid,
                        text=chunk,
                        parse_mode=ParseMode.HTML,
                    )
                mark_notified(prefs, sym)
                changed = True
            except Exception:
                log.exception("send notify uid=%s sym=%s", uid, sym)
        if changed:
            save_prefs(uid, prefs)


async def post_init(application: Application) -> None:
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

    collect_sec = int(os.environ.get("COLLECT_INTERVAL_SEC", "0"))
    if collect_sec > 0:
        jq.run_repeating(autocollect_job, interval=collect_sec, first=180, name="autocollect")
        log.info("JobQueue: автосбор сигналов каждые %s с", collect_sec)


def main() -> int:
    import signal as _sig

    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
    if not token:
        log.error("Задайте TELEGRAM_BOT_TOKEN (или BOT_TOKEN)")
        return 1

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("menu", cmd_start))
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
