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


def format_quick_quote(symbol: str, company: str, last: float, currency: str, instrument_label: str) -> str:
    return (
        f"📊 <b>{_esc(symbol)}</b> — {_esc(company)}\n"
        f"Тип: {_esc(instrument_label)}\n"
        f"Цена (посл. закрытие в истории): <b>{last:.4f}</b> {_esc(currency)}"
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
    d_icon = "🟢" if tp.direction == "long" else "🔴"
    d_label = "LONG" if tp.direction == "long" else "SHORT"
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
    if hasattr(r, "levels_detail") and r.levels_detail:
        lines.append(f"📐 Уровни: {_esc(r.levels_detail)}")
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
        cleaned = re.sub(r"[^A-Za-z0-9.\-]", "", a).strip()
        if cleaned:
            syms.append(cleaned.upper())
    return syms, tape, ws


def sanitize_command_args(args: list[str]) -> tuple[str, bool, bool, bool]:
    """
    Возвращает (symbol, volume_tape, finnhub_ws, help_flag).
    Допустимые хвосты: tape, ws (регистр не важен).
    """
    if not args:
        return "", False, False, True
    sym = re.sub(r"[^A-Za-z0-9.\-]", "", args[0]).strip().upper()
    if not sym:
        return "", False, False, True
    tail = {a.lower() for a in args[1:]}
    return sym, "tape" in tail, "ws" in tail, False
