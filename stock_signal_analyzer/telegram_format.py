"""Форматирование отчёта для Telegram (HTML, разбиение длинных сообщений)."""

from __future__ import annotations

import html
import re

from .dashboard import DashboardBundle, section_order
from .engine import SignalReport
from .market_segments import SECTION_TITLES, format_tags_ru


def esc_html(s: str) -> str:
    return html.escape(s, quote=False)


# Алиас для обратной совместимости внутри модуля
_esc = esc_html


def format_quick_quote(symbol: str, company: str, last: float, currency: str, instrument_label: str, live_price: float | None = None) -> str:
    if live_price and live_price > 0:
        change_pct = (live_price / last - 1.0) * 100 if last > 0 else 0
        arrow = "📈" if change_pct > 0 else ("📉" if change_pct < 0 else "➡️")
        return (
            f"📊 <b>{_esc(symbol)}</b> — {_esc(company)}\n"
            f"Тип: {_esc(instrument_label)}\n"
            f"Цена: <b>{live_price:.4f}</b> {_esc(currency)} {arrow} {change_pct:+.2f}%\n"
            f"Закрытие: {last:.4f} {_esc(currency)}"
        )
    return (
        f"📊 <b>{_esc(symbol)}</b> — {_esc(company)}\n"
        f"Тип: {_esc(instrument_label)}\n"
        f"Цена: <b>{last:.4f}</b> {_esc(currency)}"
    )


def _format_trade_plan_html(r: SignalReport) -> str:
    """Блок ТОРГОВЫЙ ПЛАН для Telegram HTML."""
    tp = r.trade_plan
    if tp is None or tp.direction == "none":
        reason = getattr(tp, "plan_text", "") if tp else ""
        if reason:
            return f"<i>{_esc(reason)}</i>"
        tier = r.signal_tier
        if tier == "C":
            return "<i>Нет торгового плана (класс C — наблюдение).</i>"
        return "<i>Нет торгового плана (|score| &lt; порога или нет ATR).</i>"
    bold = r.signal_tier == "A"
    tier = r.signal_tier
    if tier == "C":
        d_icon = "⚪"
        d_label = "НАБЛЮДЕНИЕ"
        head = f"{d_icon} {d_label} {_esc(r.symbol)} @ {tp.entry_price:.2f}"
        lines = [
            "ТОРГОВЫЙ ПЛАН (класс C — наблюдение, не вход)",
            head,
            f"Стоп-ориентир: {tp.stop_price:.2f} ({tp.stop_pct:+.2f}%)",
            f"Цель 1 (ориентир): {tp.target1_price:.2f} ({tp.target1_pct:+.2f}%)  R:R {tp.risk_reward_1:.1f}",
            f"Цель 2 (ориентир): {tp.target2_price:.2f} ({tp.target2_pct:+.2f}%)  R:R {tp.risk_reward_2:.1f}",
            f"Удержание: до {tp.max_hold_days} дней  |  Позиция: {tp.position_size_pct:.0f}%",
            f"Класс: <b>C</b> (не входить — только наблюдать)",
        ]
        return "\n".join(lines)
    d_icon = "🟢" if tp.direction == "long" else "🔴"
    d_label = "ПОКУПКА" if tp.direction == "long" else "ПРОДАЖА"
    head = f"{d_icon} <b>{d_label} {_esc(r.symbol)} @ {tp.entry_price:.2f}</b>" if bold else (
        f"{d_icon} {d_label} {_esc(r.symbol)} @ {tp.entry_price:.2f}"
    )
    lines = [
        f"{'<b>' if bold else ''}ТОРГОВЫЙ ПЛАН{'</b>' if bold else ''}",
        head,
        f"Стоп: {tp.stop_price:.2f} ({tp.stop_pct:+.2f}%)",
        f"Цель 1: {tp.target1_price:.2f} ({tp.target1_pct:+.2f}%)  R:R {tp.risk_reward_1:.1f} — закрыть {tp.partial_exit_pct:.0f}%",
        f"Цель 2: {tp.target2_price:.2f} ({tp.target2_pct:+.2f}%)  R:R {tp.risk_reward_2:.1f} — остаток",
        f"Трейлинг: после +{tp.trailing_activation_pct:.1f}% стоп → безубыток",
        f"Удержание: до {tp.max_hold_days} дней  |  Позиция: {tp.position_size_pct:.0f}%",
        f"Класс: <b>{_esc(r.signal_tier)}</b>",
    ]
    return "\n".join(lines)


def _plain_language_summary(r: SignalReport) -> str:
    """Вывод простым языком в конце отчёта."""
    parts: list[str] = []
    parts.append("📝 <b>Вывод простым языком</b>")

    sym = _esc(r.symbol)
    company = _esc(r.company)
    score = r.score
    tier = r.signal_tier
    conf = r.confidence
    ref = r.ref_price

    # ── Цена и волатильность ──
    currency = "₽" if r.symbol.endswith(".ME") else "$"
    price_str = f"{ref:,.2f}" if ref >= 1 else f"{ref:.4f}"
    parts.append(f"💰 Цена сейчас: <b>{price_str} {currency}</b>")

    if r.atr_pct is not None:
        atr = r.atr_pct
        if atr < 1.0:
            vol_word = "низкая"
        elif atr < 2.5:
            vol_word = "умеренная"
        elif atr < 4.0:
            vol_word = "повышенная"
        else:
            vol_word = "высокая"
        atr_abs = ref * atr / 100
        parts.append(
            f"📊 Волатильность: {vol_word} ({atr:.1f}% в день ≈ {atr_abs:,.2f} {currency})"
        )

    # ── Уровни ──
    levels = getattr(r, "levels_detail", "") or ""
    if levels:
        # Парсим из строки вида "Pivot=319.38, поддержка: 316.92 (-0.9%), сопротивление: 322.36 (+0.8%)"
        import re as _re
        sup_m = _re.search(r"поддержка:\s*([\d.]+)\s*\(([^)]+)\)", levels)
        res_m = _re.search(r"сопротивление:\s*([\d.]+)\s*\(([^)]+)\)", levels)
        if sup_m and res_m:
            sup_price = sup_m.group(1)
            sup_pct = sup_m.group(2)
            res_price = res_m.group(1)
            res_pct = res_m.group(2)
            parts.append(
                f"📐 Ближайшие уровни:\n"
                f"  🟢 Поддержка: {sup_price} {currency} ({sup_pct}) — возможная остановка падения\n"
                f"  🔴 Сопротивление: {res_price} {currency} ({res_pct}) — рост может замедлиться"
            )

    # ── Направление ──
    if score > 0.35:
        direction = "заметный сигнал на рост"
    elif score > 0.15:
        direction = "слабый сигнал на рост"
    elif score < -0.35:
        direction = "заметный сигнал на снижение"
    elif score < -0.15:
        direction = "слабый сигнал на снижение"
    else:
        direction = "нет выраженного направления"

    parts.append(f"\n📈 Направление: <b>{direction}</b>")

    # ── Согласованность ──
    if conf >= 0.80:
        parts.append("✅ Индикаторы хорошо согласованы между собой.")
    elif conf >= 0.60:
        parts.append("⚠️ Индикаторы в целом согласованы, но есть расхождения.")
    else:
        parts.append("❌ Индикаторы противоречат друг другу — сигнал ненадёжный.")

    # ── Что говорят компоненты ──
    drivers: list[str] = []
    if r.technical_score > 0.15:
        drivers.append("📊 техника за рост")
    elif r.technical_score < -0.15:
        drivers.append("📊 техника за снижение")

    if r.momentum_score > 0.15:
        drivers.append("🚀 импульс вверх")
    elif r.momentum_score < -0.15:
        drivers.append("🚀 импульс вниз")

    if r.news_score > 0.15:
        drivers.append("📰 новости позитивные")
    elif r.news_score < -0.15:
        drivers.append("📰 новости негативные")

    if r.volume_score > 0.15:
        drivers.append("📦 объём подтверждает покупки")
    elif r.volume_score < -0.15:
        drivers.append("📦 объём указывает на продажи")

    if drivers:
        parts.append("Ключевые факторы: " + ", ".join(drivers) + ".")

    # ── Тренд ──
    weekly = (r.weekly_regime or "").lower()
    if "up" in weekly:
        parts.append("📈 Недельный тренд: растущий.")
    elif "down" in weekly:
        parts.append("📉 Недельный тренд: падающий.")
    elif "flat" in weekly:
        parts.append("➡️ Недельный тренд: боковик.")

    # ── Макро ──
    if r.macro_dampening < 0.75:
        parts.append("⚠️ Впереди важные макро-события (ЦБ, инфляция) — сигнал ослаблен, лучше подождать.")
    elif r.macro_dampening < 0.90:
        parts.append("Макро-фон умеренно неопределённый — учитывайте риск.")

    # ── Совет по риск-менеджменту ──
    parts.append("")
    parts.append("🛡️ <b>Совет по риск-менеджменту</b>")
    if r.atr_pct is not None and r.atr_pct > 3.0:
        parts.append("Высокая волатильность — уменьшите размер позиции или подождите успокоения.")
    if conf < 0.50:
        parts.append("Низкая согласованность компонентов — не входите на полную позицию.")
    if r.macro_dampening < 0.85:
        parts.append("Макро-фон неблагоприятный — сократите риск или отложите вход.")
    tp = r.trade_plan
    if tp and tp.direction != "none" and tp.position_size_pct > 0 and tier != "C":
        parts.append(
            f"Рекомендуемый размер позиции: <b>{tp.position_size_pct:.0f}%</b> от капитала. "
            f"Не превышайте — это защита от серии убытков."
        )

    # ── Класс и рекомендация ──
    if tier == "A":
        if tp and tp.direction != "none":
            d = "покупку" if tp.direction == "long" else "продажу"
            stop_abs = tp.stop_price
            t1_abs = tp.target1_price
            parts.append(
                f"\n✅ <b>Класс A — сильный сигнал</b>\n"
                f"<b>Рекомендация:</b> рассмотреть {d} при пробое текущего уровня.\n"
                f"  Вход: ~{ref:,.2f} {currency}\n"
                f"  Стоп-лосс: {stop_abs:,.2f} {currency} ({tp.stop_pct:+.1f}%)\n"
                f"  Цель 1: {t1_abs:,.2f} {currency} ({tp.target1_pct:+.1f}%) — закрыть {tp.partial_exit_pct:.0f}%\n"
                f"  Цель 2: {tp.target2_price:,.2f} {currency} ({tp.target2_pct:+.1f}%) — остаток\n"
                f"  Удержание: до {tp.max_hold_days} дней\n"
                f"  R:R = {tp.risk_reward_1:.1f} (минимум 1.5)"
            )
        else:
            parts.append("✅ <b>Класс A</b> — сильный сигнал по всем критериям.")
    elif tier == "B":
        if tp and tp.direction != "none":
            d = "покупку" if tp.direction == "long" else "продажу"
            parts.append(
                f"\n🔶 <b>Класс B — средний сигнал</b>\n"
                f"<b>Рекомендация:</b> можно рассмотреть {d}, но уменьшите позицию вдвое.\n"
                f"  Стоп: {tp.stop_price:,.2f} {currency} ({tp.stop_pct:+.1f}%)\n"
                f"  Цель: {tp.target1_price:,.2f} {currency} ({tp.target1_pct:+.1f}%)\n"
                f"  R:R = {tp.risk_reward_1:.1f}"
            )
        else:
            parts.append("🔶 <b>Класс B</b> — средний сигнал. Можно рассмотреть, но с осторожностью.")
    else:
        parts.append("⚪ <b>Класс C</b> — слабый или противоречивый сигнал. <b>Лучше наблюдать и не торговать.</b>")

    # ── Educational: почему этот сигнал ──
    parts.append("")
    parts.append("📚 <b>Почему такая оценка?</b>")
    if r.technical_score > 0.2:
        parts.append("Технический анализ показывает бычьи паттерны или выход из консолидации.")
    elif r.technical_score < -0.2:
        parts.append("Технический анализ показывает медвежьи паттерны или пробой поддержки.")
    if abs(r.momentum_score) > 0.15:
        parts.append("Импульс подтверждает направление — ускорение или замедление тренда.")
    if abs(r.news_score) > 0.15:
        parts.append("Новостной фон влияет на сентимент и может усилить движение.")
    if r.volume_score > 0.1:
        parts.append("Объём выше среднего — подтверждение интереса участников.")
    elif r.volume_score < -0.1:
        parts.append("Объём ниже среднего — движение может быть слабым и быстро развернуться.")
    parts.append(
        "Все компоненты объединяются взвешенно. Класс A требует согласованности, "
        "сильного тренда (ADX>20) и благоприятного макро-фона."
    )

    return "\n".join(parts)


def format_signal_report(r: SignalReport) -> str:
    lines: list[str] = []
    lines.append(f"📈 <b>{_esc(r.symbol)}</b> — {_esc(r.company)}")
    lines.append(f"Тип: {_esc(r.instrument_label)}")
    lines.append("")
    lines.append(_format_trade_plan_html(r))
    lines.append("")
    lines.append(
        f"Итог: <b>{r.score:+.3f}</b> (−1…+1), до макро: {r.score_before_macro:+.3f}"
    )
    lines.append(
        f"Согласованность компонентов: <b>{r.confidence:.2f}</b> (1.0 — все в одну сторону; "
        f"ниже — больше противоречий). ADX14≈{r.adx14:.1f}, режим: {_esc(r.regime_label)}"
    )
    atr_line = f"ATR(14)≈{r.atr_pct:.2f}% от цены" if r.atr_pct is not None else "ATR(14): н/д"
    stop_line = (
        f"ориентир стопа ≈1.5×ATR: <b>{r.stop_hint_pct:.2f}%</b> от цены"
        if r.stop_hint_pct is not None
        else "стоп-ориентир: н/д"
    )
    lines.append(
        f"Класс качества: <b>{_esc(r.signal_tier)}</b> ({atr_line}; {stop_line}). "
        f"{_esc(r.tier_rationale)}"
    )
    lines.append(f"<b>Время и контекст</b> ({_esc(r.weekly_regime)}): {_esc(r.timing_detail)}")
    lines.append(_esc(r.verdict))
    lines.append("")
    lines.append("<b>Компоненты</b>")
    lines.append(f"Техника: {r.technical_score:+.3f}")
    lines.append(f"  {_esc(r.technical_detail)}")
    if r.pattern_summary:
        lines.append(f"  🕯️ <b>Паттерны:</b> {_esc(r.pattern_summary)}")
    lines.append(f"Импульс: {r.momentum_score:+.3f}")
    lines.append(f"  {_esc(r.momentum_detail)}")
    lines.append(f"Новости: {r.news_score:+.3f}")
    lines.append(f"  {_esc(r.news_detail)}")
    lines.append(f"Объём: {r.volume_score:+.3f}")
    lines.append(f"  {_esc(r.volume_detail)}")
    if r.intraday_score is not None:
        lines.append(f"Онлайн: {r.intraday_score:+.3f}")
        lines.append(f"  {_esc(r.intraday_detail or '')}")
    else:
        lines.append(_esc(getattr(r, "online_hint", "") or "Онлайн: — (MOEX / Finnhub)"))
    lines.append("")
    # ── Quant Models (институциональные) ──
    quant_lines: list[str] = []
    if getattr(r, "mtf_momentum_detail", ""):
        quant_lines.append(f"  {_esc(r.mtf_momentum_detail)}")
    if getattr(r, "trend_detail", ""):
        quant_lines.append(f"  {_esc(r.trend_detail)}")
    if getattr(r, "zscore_detail", ""):
        quant_lines.append(f"  {_esc(r.zscore_detail)}")
    if getattr(r, "vol_regime_detail", ""):
        quant_lines.append(f"  {_esc(r.vol_regime_detail)}")
    if getattr(r, "cross_asset_detail", ""):
        quant_lines.append(f"  {_esc(r.cross_asset_detail)}")
    if quant_lines:
        qs = getattr(r, "quant_score", 0.0) or 0.0
        lines.append(f"<b>Квант-модели</b>: {qs:+.3f}")
        lines.extend(quant_lines)
    if getattr(r, "position_size_detail", ""):
        lines.append(f"💰 {_esc(r.position_size_detail)}")
    lines.append("")

    lines.append(f"Макро ×{r.macro_dampening:.2f}")
    lines.append(_esc(r.macro_summary))
    lines.append("")
    # ── Аналитика ──
    analyst = getattr(r, "analyst_detail", "") or ""
    earnings = getattr(r, "earnings_detail", "") or ""
    if analyst or earnings:
        if r.symbol.endswith(".ME"):
            lines.append("🏦 <b>Сводная оценка индикаторов</b>")
        else:
            lines.append("🏦 <b>Мнение аналитиков и индикаторов</b>")
        if analyst:
            lines.append(f"  {_esc(analyst)}")
        if earnings:
            lines.append(f"  {_esc(earnings)}")
        lines.append("")
    lines.append(_plain_language_summary(r))
    lines.append("")
    lines.append(_esc(r.risk_note))
    return "\n".join(lines)


def split_telegram_html(text: str, max_len: int = 3900) -> list[str]:
    """Telegram лимит ~4096; оставляем запас."""
    text = text.strip()
    if len(text) <= max_len:
        return [text]
    parts: list[str] = []
    rest = text
    while rest:
        if len(rest) <= max_len:
            parts.append(rest)
            break
        cut = rest.rfind("\n", 0, max_len)
        if cut < max_len // 2:
            cut = max_len
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    return [p for p in parts if p]


def format_dashboard_bundle(
    bundle: DashboardBundle,
    outside: list[tuple[str, SignalReport]],
) -> str:
    """Полный HTML: секции по рынку + сильные сигналы вне списка."""
    lines: list[str] = []
    lines.append("<b>📑 Сводный отчёт</b>")
    lines.append("Каждая бумага — полный анализ (техника, импульс, новости, объём, онлайн, макро).")
    lines.append("")
    any_section = False
    for key in section_order():
        reps = bundle.sections.get(key) or []
        if not reps:
            continue
        any_section = True
        lines.append(f"<b>{SECTION_TITLES.get(key, key)}</b>")
        for r in reps:
            lines.append(f"Метки: {_esc(format_tags_ru(r.symbol))}")
            lines.append(format_signal_report(r))
            lines.append("")
    if not any_section and not bundle.errors:
        lines.append("Нет успешно загруженных тикеров.")
        lines.append("")
    if bundle.errors:
        lines.append("<b>Ошибки</b>")
        for e in bundle.errors:
            lines.append(_esc(e))
        lines.append("")
    if outside:
        lines.append("<b>⚠️ Сильный сигнал по бумаге не из вашего списка</b>")
        lines.append(
            "Ниже — полный отчёт по таким тикерам (пул ликвидных РФ/иностранных/дивидендных)."
        )
        lines.append("")
        for sym, r in outside:
            lines.append(f"🔔 <b>{_esc(sym)}</b> |score|={abs(r.score):.3f}")
            lines.append(f"Метки: {_esc(format_tags_ru(sym))}")
            lines.append(format_signal_report(r))
            lines.append("")
    return "\n".join(lines)


def format_outside_notification(sym: str, r: SignalReport) -> str:
    """Короткое уведомление + полный отчёт (как просили «полная информация»)."""
    head = (
        f"⚠️ <b>Сильный сигнал вне вашего списка</b>\n"
        f"{_esc(sym)} — {_esc(r.company)} | класс <b>{_esc(r.signal_tier)}</b>\n"
        f"Итог: <b>{r.score:+.3f}</b> — {_esc(r.verdict)}\n"
    )
    return head + "\n" + format_signal_report(r)


def parse_dash_args(args: list[str]) -> tuple[list[str], bool, bool]:
    """Список тикеров и флаги tape/ws."""
    syms: list[str] = []
    tape = False
    ws = False
    for a in args:
        lo = a.lower().strip()
        if lo == "tape":
            tape = True
            continue
        if lo == "ws":
            ws = True
            continue
        cleaned = re.sub(r"[^A-Za-z0-9.\-]", "", a).strip().upper()
        if cleaned and len(cleaned) <= 12 and re.fullmatch(r"[A-Z0-9]{1,10}(?:\.[A-Z]{1,4})?(?:-[A-Z])?", cleaned):
            syms.append(cleaned)
    return syms, tape, ws


def sanitize_command_args(args: list[str]) -> tuple[str, bool, bool, bool]:
    """
    Возвращает (symbol, volume_tape, finnhub_ws, help_flag).
    Допустимые хвосты: tape, ws (регистр не важен).
    """
    if not args:
        return "", False, False, True
    sym = re.sub(r"[^A-Za-z0-9.\-]", "", args[0]).strip().upper()
    if not sym or len(sym) > 12:
        return "", False, False, True
    # Строгая валидация формата тикера: буквы/цифры, опционально .XX суффикс
    if not re.fullmatch(r"[A-Z0-9]{1,10}(?:\.[A-Z]{1,4})?(?:-[A-Z])?", sym):
        return "", False, False, True
    tail = {a.lower() for a in args[1:]}
    return sym, "tape" in tail, "ws" in tail, False


# ── Tier-based Telegram formatters ────────────────────────────────────────────


def format_screen_results(results: list[dict], max_results: int = 10) -> str:
    """Краткий список результатов скринера для Telegram."""
    if not results:
        return "📊 Нет сигналов по заданным критериям."
    lines = [f"📊 <b>Топ сигналов</b> (показано {min(len(results), max_results)} из {len(results)})"]
    for r in results[:max_results]:
        tier = r.get("signal_tier", "C")
        direction = r.get("direction", "none")
        score = r.get("score", 0)
        sym = _esc(r.get("symbol", "?"))
        comp = _esc(r.get("company", ""))
        conf = r.get("confidence", 0)
        dir_icon = "🟢" if direction == "long" else ("🔴" if direction == "short" else "⚪")
        lines.append(
            f"{dir_icon} <b>{sym}</b> {comp}\n"
            f"   Score: <b>{score:+.2f}</b> | Tier: <b>{tier}</b> | Conf: {conf:.0%}"
        )
    return "\n".join(lines)


def render_ascii_equity_curve(equity_values: list[float], width: int = 40, height: int = 10) -> str:
    """Нарисовать ASCII equity curve."""
    if not equity_values:
        return ""
    min_v = min(equity_values)
    max_v = max(equity_values)
    if max_v == min_v:
        return ""
    rows: list[str] = []
    for h in range(height, -1, -1):
        thresh = min_v + (max_v - min_v) * (h / height)
        line = ""
        for i, v in enumerate(equity_values):
            x = int(i / max(len(equity_values) - 1, 1) * (width - 1))
            # simple: plot every point but align to width
            pass
        # simpler approach: sample width points
        if h == height:
            rows.append(f"{max_v:>8.1f} ┤")
        elif h == 0:
            rows.append(f"{min_v:>8.1f} ┤")
        else:
            rows.append("         ┤")
    # Build actual markers
    sampled: list[float] = []
    n = len(equity_values)
    for i in range(width):
        idx = int(i / max(width - 1, 1) * (n - 1))
        sampled.append(equity_values[idx])
    grid = [[" " for _ in range(width)] for _ in range(height + 1)]
    for i, v in enumerate(sampled):
        row = height - int((v - min_v) / (max_v - min_v) * height)
        row = max(0, min(height, row))
        grid[row][i] = "*"
    out_lines: list[str] = []
    for h in range(height + 1):
        v_label = ""
        if h == 0:
            v_label = f"{min_v:>8.1f} "
        elif h == height:
            v_label = f"{max_v:>8.1f} "
        else:
            v_label = "         "
        out_lines.append(v_label + "┤" + "".join(grid[h]))
    out_lines.append("         └" + "─" * width)
    return "\n".join(out_lines)


def format_backtest_telegram(report: dict, tier: str = "free") -> str:
    """Форматировать backtest отчёт для Telegram с учётом tier."""
    if not report or not report.get("total_signals"):
        return "📈 <b>Бэктест</b>\nНет данных для отчёта."

    lines = ["📈 <b>Бэктест</b>"]
    total = report["total_signals"]
    wr = report.get("win_rate", 0) * 100
    pf = report.get("profit_factor", 0)
    avg_win = report.get("avg_win_pct", 0)
    avg_loss = report.get("avg_loss_pct", 0)
    total_pnl = report.get("total_pnl_pct", 0)
    lines.append(
        f"Сделок: <b>{total}</b> | Win rate: <b>{wr:.1f}%</b> | PF: <b>{pf:.2f}</b>\n"
        f"Avg win: +{avg_win:.2f}% | Avg loss: −{avg_loss:.2f}% | Total PnL: <b>{total_pnl:+.2f}%</b>"
    )

    # Tier/direction breakdown for pro+
    if tier in ("pro", "premium"):
        breakdown = report.get("breakdown", {})
        if breakdown:
            lines.append("")
            lines.append("<b>Разбивка</b>")
            for key, stats in breakdown.items():
                lines.append(
                    f"  {key}: {stats.get('win_rate', 0)*100:.1f}% WR, "
                    f"PF {stats.get('profit_factor', 0):.2f} ({stats.get('count', 0)} сделок)"
                )

    # ASCII equity curve + Sharpe/max DD for premium
    if tier == "premium":
        equity = report.get("equity_curve", [])
        if equity:
            lines.append("")
            lines.append("<b>Equity curve</b>")
            lines.append(f"<pre>{render_ascii_equity_curve(equity, width=36, height=8)}</pre>")
        sharpe = report.get("sharpe_like")
        if sharpe is not None:
            lines.append(f"Sharpe-like: <b>{sharpe:.2f}</b>")
        max_dd = report.get("max_drawdown_pct")
        if max_dd is not None:
            lines.append(f"Max drawdown: <b>{max_dd:.2f}%</b>")

    return "\n".join(lines)


def format_clusters_telegram(result) -> str:
    """Форматировать анализ объёмных кластеров для Telegram.

    Принимает dict или VolumeClusterResult (dataclass).
    """
    if not result:
        return "🔬 Кластеры: нет данных."

    def _get(name: str):
        if isinstance(result, dict):
            return result.get(name)
        return getattr(result, name, None)

    def _prices(levels, limit: int = 5):
        """Извлечь цены из списка float или списка tuple (price, volume)."""
        out: list[float] = []
        for item in levels[:limit]:
            if isinstance(item, (list, tuple)):
                out.append(float(item[0]))
            else:
                out.append(float(item))
        return out

    lines = ["🔬 <b>Volume Clusters</b>"]
    poc = _get("poc")
    if poc is not None:
        lines.append(f"POC (точка максимального объёма): <b>{float(poc):.2f}</b>")
    va_low = _get("value_area_low")
    va_high = _get("value_area_high")
    if va_low is not None and va_high is not None:
        lines.append(f"Value Area (70%): <b>{float(va_low):.2f}</b> — <b>{float(va_high):.2f}</b>")
    hvn = _get("hvn_levels") or []
    lvn = _get("lvn_levels") or []
    if hvn:
        prices = _prices(hvn)
        lines.append(f"HVN (высокий объём): {', '.join(f'{v:.2f}' for v in prices)}")
    if lvn:
        prices = _prices(lvn)
        lines.append(f"LVN (низкий объём): {', '.join(f'{v:.2f}' for v in prices)}")
    return "\n".join(lines)


def format_mlscore_telegram(ensemble) -> str:
    """Форматировать ML scoring info для Telegram (premium)."""
    if ensemble is None:
        return "🧠 ML Score: модель недоступна."
    fi = ensemble.feature_importances()
    if not fi:
        return "🧠 ML Score: ещё не обучено."
    lines = ["🧠 <b>ML RankEnsemble</b>"]
    lines.append("<b>Важность фич</b>")
    for name, val in sorted(fi.items(), key=lambda x: x[1], reverse=True):
        bar = "█" * int(val * 40)
        lines.append(f"  {name}: <b>{val:.3f}</b> {bar}")
    last = getattr(ensemble, "_last_fit_at", None)
    if last:
        lines.append(f"Последнее обучение: {last}")
    lines.append(f"Сэмплов: {getattr(ensemble, '_trained_count', 0)}")
    return "\n".join(lines)


def format_portfolio_telegram(signals: list[dict]) -> str:
    """Форматировать открытые позиции для Telegram."""
    if not signals:
        return "📁 <b>Портфель</b>\nНет открытых позиций."
    lines = ["📁 <b>Портфель</b>"]
    total_pnl = 0.0
    for s in signals:
        sym = _esc(s.get("symbol", "?"))
        dir_ = s.get("direction", "none")
        entry = s.get("entry_price", 0)
        current = s.get("current_price", 0)
        pnl = s.get("pnl_pct", 0)
        total_pnl += pnl or 0
        icon = "🟢" if dir_ == "long" else "🔴"
        lines.append(
            f"{icon} <b>{sym}</b> {dir_} | Entry: {entry:.2f} | Now: {current:.2f} | PnL: {pnl:+.2f}%"
        )
    lines.append(f"\nОбщий PnL: <b>{total_pnl:+.2f}%</b>")
    return "\n".join(lines)
