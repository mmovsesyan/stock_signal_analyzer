"""
Backtest Validator — проверка исторической прибыльности сигналов перед советом.

Загружает outcomes.jsonl, группирует по тиру/market/direction,
считает win rate, profit factor, avg PnL для каждой группы.

Бот использует этот модуль чтобы:
- Не советовать сигналы, которые исторически убыточны
- Показывать советнику уверенность на основе исторических данных
- Автоматически обновлять валидацию после каждого learning cycle

Использование:
    from stock_signal_analyzer.backtest_validator import validate_signal, BacktestValidator

    validator = BacktestValidator()
    result = validator.validate_signal(tier='A', direction='long', market='us')
    if result.should_advice:
        send_advice_to_user(report)
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Пороги для валидации сигнала
_MIN_TRADES = 15           # минимум сделок для статистики
_MIN_WIN_RATE = 0.55       # минимальный win rate (55%)
_MIN_PROFIT_FACTOR = 1.0   # минимальный profit factor (убыточные стратегии блокируем)
_TARGET_WIN_RATE = 0.65    # целевой win rate (65% — проф. стандарт)
_TARGET_PROFIT_FACTOR = 2.0  # целевой profit factor


@dataclass
class GroupStats:
    """Статистика по группе сигналов."""
    tier: str
    direction: str  # long, short
    market: str     # us, ru
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    timeouts: int = 0
    win_rate: float = 0.0
    avg_pnl_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0
    total_pnl_pct: float = 0.0
    sharpe_approx: float = 0.0  # Sharpe approximation (avg/std)


@dataclass
class ValidationResult:
    """Результат валидации сигнала."""
    should_advice: bool       # можно ли советовать этот сигнал
    confidence: float         # 0-1, уверенность на основе истории
    reason: str               # причина решения
    group_stats: GroupStats | None  # историческая статистика
    overall_stats: GroupStats | None  # общая статистика


@dataclass
class BacktestReport:
    """Полный отчёт бэктеста."""
    overall: GroupStats
    by_tier: dict[str, GroupStats]
    by_market: dict[str, GroupStats]
    by_direction: dict[str, GroupStats]
    by_tier_direction: dict[str, GroupStats]
    monthly_pnl: dict[str, float]
    max_drawdown_pct: float
    total_return_pct: float
    total_trades: int
    date_range: str


class BacktestValidator:
    """
    Валидатор сигналов на основе исторических outcomes.

    Загружает outcomes.jsonl и строит статистику по группам:
    - По тиру (A, B, C)
    - По направлению (long, short)
    - По рынку (US, RU)
    - По комбинации тир + направление + рынок
    """

    def __init__(self, outcomes_path: str | None = None):
        self.outcomes_path = outcomes_path or os.path.join(
            os.environ.get("STOCK_SIGNAL_DATA", "/var/lib/stock_signal_analyzer"),
            "outcomes.jsonl"
        )
        self._groups: dict[str, GroupStats] = {}
        self._loaded = False

    def _load_outcomes(self) -> list[dict[str, Any]]:
        """Загрузить outcomes из файла."""
        if not os.path.exists(self.outcomes_path):
            return []

        outcomes = []
        try:
            with open(self.outcomes_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                        outcome = r.get("outcome", "")
                        if outcome in ("win_t1", "win_t2", "loss", "timeout"):
                            outcomes.append(r)
                    except json.JSONDecodeError:
                        continue
        except OSError as e:
            _log.warning("BacktestValidator: cannot read %s: %s", self.outcomes_path, e)

        return outcomes

    def _detect_market(self, symbol: str) -> str:
        """Определить рынок по символу."""
        sym = symbol.upper()
        if sym.endswith(".ME"):
            return "ru"
        return "us"

    def _group_key(self, record: dict[str, Any]) -> str:
        """Создать ключ группировки."""
        tier = record.get("signal_tier", "C")
        direction = record.get("direction", "long")
        symbol = record.get("symbol", "")
        market = self._detect_market(symbol)
        return f"{tier}_{direction}_{market}"

    def _compute_group_stats(self, records: list[dict[str, Any]]) -> GroupStats:
        """Вычислить статистику для группы записей."""
        if not records:
            return GroupStats(tier="", direction="", market="")

        wins = [r for r in records if r.get("outcome") in ("win_t1", "win_t2")]
        losses = [r for r in records if r.get("outcome") == "loss"]
        timeouts = [r for r in records if r.get("outcome") == "timeout"]

        pnl_values = []
        for r in records:
            pnl = r.get("pnl_pct")
            if pnl is not None:
                try:
                    pnl_values.append(float(pnl))
                except (ValueError, TypeError):
                    pass

        win_pnls = [r.get("pnl_pct", 0) for r in wins if r.get("pnl_pct") is not None]
        loss_pnls = [abs(r.get("pnl_pct", 0)) for r in losses if r.get("pnl_pct") is not None]

        n_wins = len(wins)
        n_losses = len(losses)
        decisive = n_wins + n_losses
        win_rate = n_wins / decisive if decisive > 0 else 0.0

        avg_pnl = sum(pnl_values) / len(pnl_values) if pnl_values else 0.0
        avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0.0
        avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0.0

        total_win = sum(win_pnls)
        total_loss = sum(loss_pnls)
        pf = total_win / total_loss if total_loss > 0 else (total_win if total_win > 0 else 0.0)

        # Sharpe approximation: avg_pnl / std_pnl
        std_pnl = 0.0
        if len(pnl_values) > 1:
            mean_pnl = sum(pnl_values) / len(pnl_values)
            variance = sum((p - mean_pnl) ** 2 for p in pnl_values) / (len(pnl_values) - 1)
            std_pnl = variance ** 0.5
        sharpe = avg_pnl / std_pnl if std_pnl > 0 else 0.0

        # Determine tier/direction/market from first record
        first = records[0]
        tier = first.get("signal_tier", "C")
        direction = first.get("direction", "long")
        market = self._detect_market(first.get("symbol", ""))

        return GroupStats(
            tier=tier,
            direction=direction,
            market=market,
            total_trades=total,
            wins=n_wins,
            losses=n_losses,
            timeouts=len(timeouts),
            win_rate=win_rate,
            avg_pnl_pct=avg_pnl,
            avg_win_pct=avg_win,
            avg_loss_pct=avg_loss,
            profit_factor=pf,
            total_pnl_pct=sum(pnl_values),
            sharpe_approx=sharpe,
        )

    def _build_groups(self) -> dict[str, GroupStats]:
        """Построить все группы статистики."""
        outcomes = self._load_outcomes()
        if not outcomes:
            return {}

        # Group by tier_direction_market
        grouped: dict[str, list[dict[str, Any]]] = {}
        for r in outcomes:
            key = self._group_key(r)
            grouped.setdefault(key, []).append(r)

        groups = {}
        for key, records in grouped.items():
            groups[key] = self._compute_group_stats(records)

        return groups

    def get_groups(self, force_refresh: bool = False) -> dict[str, GroupStats]:
        """Получить все группы статистики (кэшируется)."""
        if not self._loaded or force_refresh:
            self._groups = self._build_groups()
            self._loaded = True
        return self._groups

    def validate_signal(
        self,
        tier: str,
        direction: str,
        market: str | None = None,
        min_trades: int = _MIN_TRADES,
    ) -> ValidationResult:
        """
        Проверить, можно ли советовать сигнал данного типа.

        Args:
            tier: Класс сигнала (A, B, C)
            direction: Направление (long, short)
            market: Рынок (us, ru) — если None, проверяет оба
            min_trades: Минимум сделок для статистики

        Returns:
            ValidationResult с решением и статистикой
        """
        groups = self.get_groups()
        key = f"{tier}_{direction}_{market}" if market else f"{tier}_{direction}"

        # Найти соответствующие группы
        matching_groups = []
        for gk, gs in groups.items():
            if gs.tier != tier or gs.direction != direction:
                continue
            if market and gs.market != market:
                continue
            matching_groups.append(gs)

        if not matching_groups:
            return ValidationResult(
                should_advice=False,
                confidence=0.0,
                reason=f"Нет исторических данных для {tier}/{direction}"
                       + (f"/{market}" if market else ""),
                group_stats=None,
                overall_stats=None,
            )

        # Объединить статистику
        total_trades = sum(g.total_trades for g in matching_groups)
        total_wins = sum(g.wins for g in matching_groups)
        total_losses = sum(g.losses for g in matching_groups)

        all_pnls = []
        for g in matching_groups:
            all_pnls.append(g.total_pnl_pct)

        win_rate = total_wins / total_trades if total_trades > 0 else 0.0
        total_pnl = sum(all_pnls)
        avg_pnl = total_pnl / total_trades if total_trades > 0 else 0.0

        total_win_sum = sum(g.wins * g.avg_win_pct for g in matching_groups)
        total_loss_sum = sum(g.losses * g.avg_loss_pct for g in matching_groups)
        pf = total_win_sum / total_loss_sum if total_loss_sum > 0 else 0.0

        combined = GroupStats(
            tier=tier,
            direction=direction,
            market=market or "all",
            total_trades=total_trades,
            wins=total_wins,
            losses=total_losses,
            win_rate=win_rate,
            avg_pnl_pct=avg_pnl,
            profit_factor=pf,
            total_pnl_pct=total_pnl,
        )

        # Проверка достаточности данных
        if total_trades < min_trades:
            return ValidationResult(
                should_advice=False,
                confidence=0.0,
                reason=f"Недостаточно данных: {total_trades}/{min_trades} сделок. "
                       f"Совет будет возможен после {min_trades - total_trades} ещё сделок.",
                group_stats=combined,
                overall_stats=self._get_overall_stats(),
            )

        # Проверка прибыльности
        reasons = []
        confidence = 1.0

        if win_rate < _MIN_WIN_RATE:
            confidence -= 0.4
            reasons.append(f"Win rate {win_rate:.0%} ниже минимума {_MIN_WIN_RATE:.0%}")

        if pf < _MIN_PROFIT_FACTOR:
            confidence -= 0.3
            reasons.append(f"Profit factor {pf:.2f} ниже минимума {_MIN_PROFIT_FACTOR:.2f}")

        if avg_pnl < 0:
            confidence -= 0.3
            reasons.append(f"Средний PnL {avg_pnl:+.2f}% — убыточный")

        confidence = max(0.0, min(1.0, confidence))

        # Бонус за высокую прибыльность
        if win_rate >= _TARGET_WIN_RATE and pf >= _TARGET_PROFIT_FACTOR:
            confidence = min(1.0, confidence + 0.2)
            reasons.append(f"✅ Высокая историческая эффективность: WR={win_rate:.0%}, PF={pf:.2f}")

        should_advice = confidence >= 0.6 and total_trades >= min_trades

        reason_text = " | ".join(reasons) if reasons else "Историческая статистика положительна"

        return ValidationResult(
            should_advice=should_advice,
            confidence=confidence,
            reason=reason_text,
            group_stats=combined,
            overall_stats=self._get_overall_stats(),
        )

    def _get_overall_stats(self) -> GroupStats | None:
        """Получить общую статистику по всем сделкам."""
        groups = self.get_groups()
        if not groups:
            return None

        total_trades = sum(g.total_trades for g in groups.values())
        total_wins = sum(g.wins for g in groups.values())
        total_pnl = sum(g.total_pnl_pct for g in groups.values())

        return GroupStats(
            tier="all",
            direction="all",
            market="all",
            total_trades=total_trades,
            wins=total_wins,
            win_rate=total_wins / total_trades if total_trades > 0 else 0.0,
            total_pnl_pct=total_pnl,
            avg_pnl_pct=total_pnl / total_trades if total_trades > 0 else 0.0,
        )

    def generate_report(self) -> BacktestReport | None:
        """Сгенерировать полный отчёт бэктеста."""
        groups = self.get_groups()
        if not groups:
            return None

        # Overall
        overall_trades = sum(g.total_trades for g in groups.values())
        overall_wins = sum(g.wins for g in groups.values())
        overall_pnl = sum(g.total_pnl_pct for g in groups.values())

        overall = GroupStats(
            tier="all", direction="all", market="all",
            total_trades=overall_trades,
            wins=overall_wins,
            win_rate=overall_wins / overall_trades if overall_trades > 0 else 0.0,
            total_pnl_pct=overall_pnl,
        )

        # By tier
        by_tier: dict[str, GroupStats] = {}
        for gk, gs in groups.items():
            by_tier.setdefault(gs.tier, []).append(gs)
        by_tier_stats = {}
        for tier, gs_list in by_tier.items():
            t_trades = sum(g.total_trades for g in gs_list)
            t_wins = sum(g.wins for g in gs_list)
            by_tier_stats[tier] = GroupStats(
                tier=tier, direction="all", market="all",
                total_trades=t_trades, wins=t_wins,
                win_rate=t_wins / t_trades if t_trades > 0 else 0.0,
                total_pnl_pct=sum(g.total_pnl_pct for g in gs_list),
            )

        # By market
        by_market: dict[str, list[GroupStats]] = {}
        for gk, gs in groups.items():
            by_market.setdefault(gs.market, []).append(gs)
        by_market_stats = {}
        for market, gs_list in by_market.items():
            m_trades = sum(g.total_trades for g in gs_list)
            m_wins = sum(g.wins for g in gs_list)
            by_market_stats[market] = GroupStats(
                tier="all", direction="all", market=market,
                total_trades=m_trades, wins=m_wins,
                win_rate=m_wins / m_trades if m_trades > 0 else 0.0,
                total_pnl_pct=sum(g.total_pnl_pct for g in gs_list),
            )

        # By direction
        by_direction: dict[str, list[GroupStats]] = {}
        for gk, gs in groups.items():
            by_direction.setdefault(gs.direction, []).append(gs)
        by_direction_stats = {}
        for direction, gs_list in by_direction.items():
            d_trades = sum(g.total_trades for g in gs_list)
            d_wins = sum(g.wins for g in gs_list)
            by_direction_stats[direction] = GroupStats(
                tier="all", direction=direction, market="all",
                total_trades=d_trades, wins=d_wins,
                win_rate=d_wins / d_trades if d_trades > 0 else 0.0,
                total_pnl_pct=sum(g.total_pnl_pct for g in gs_list),
            )

        # By tier+direction
        by_tier_direction: dict[str, GroupStats] = {}
        for gk, gs in groups.items():
            td_key = f"{gs.tier}_{gs.direction}"
            by_tier_direction[td_key] = gs

        # Max drawdown: считаем по кумулятивной кривой equity из outcomes
        # (хронологически, не по группам)
        outcomes_raw = self._load_outcomes()
        outcomes_raw_sorted = sorted(
            outcomes_raw,
            key=lambda r: r.get("exit_date") or r.get("entry_date") or ""
        )
        max_dd = 0.0
        peak = 0.0
        cumulative = 0.0
        for r in outcomes_raw_sorted:
            pnl = r.get("pnl_pct")
            if pnl is None:
                continue
            try:
                cumulative += float(pnl)
            except (TypeError, ValueError):
                continue
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        return BacktestReport(
            overall=overall,
            by_tier=by_tier_stats,
            by_market=by_market_stats,
            by_direction=by_direction_stats,
            by_tier_direction=by_tier_direction,
            monthly_pnl={},
            max_drawdown_pct=max_dd,
            total_return_pct=overall_pnl,
            total_trades=overall_trades,
            date_range="",
        )


# ── Convenience functions ────────────────────────────────────────────────────

_default_validator: BacktestValidator | None = None


def get_validator() -> BacktestValidator:
    """Get or create the default validator."""
    global _default_validator
    if _default_validator is None:
        _default_validator = BacktestValidator()
    return _default_validator


def validate_signal(tier: str, direction: str, market: str | None = None) -> ValidationResult:
    """Проверить, можно ли советовать сигнал данного типа."""
    return get_validator().validate_signal(tier, direction, market)


def format_validation_result(result: ValidationResult) -> str:
    """Форматировать результат валидации для Telegram."""
    lines = []

    if result.should_advice:
        lines.append("✅ <b>Сигнал валидирован</b>")
        lines.append(f"Уверенность: <b>{result.confidence:.0%}</b>")
    else:
        lines.append("⚠️ <b>Сигнал не валидирован</b>")
        lines.append(f"Уверенность: <b>{result.confidence:.0%}</b>")

    lines.append(f"Причина: {result.reason}")

    if result.group_stats:
        gs = result.group_stats
        lines.append("")
        lines.append(f"📊 <b>История: {gs.tier}/{gs.direction}/{gs.market}</b>")
        lines.append(f"  Сделок: {gs.total_trades}")
        lines.append(f"  Доля прибыльных: {gs.win_rate:.0%}")
        lines.append(f"  Средний результат: {gs.avg_pnl_pct:+.2f}%")
        if gs.profit_factor > 0:
            lines.append(f"  Прибыльность: {gs.profit_factor:.2f}")

    if result.overall_stats:
        oa = result.overall_stats
        lines.append("")
        lines.append(f"📈 <b>Общая статистика</b>")
        lines.append(f"  Всего сделок: {oa.total_trades}")
        lines.append(f"  Доля прибыльных: {oa.win_rate:.0%}")
        lines.append(f"  Общий результат: {oa.total_pnl_pct:+.2f}%")

    return "\n".join(lines)


def format_backtest_report(report: BacktestReport) -> str:
    """Форматировать полный отчёт бэктеста для Telegram."""
    lines = []
    lines.append("📊 <b>Бэктест отчёт</b>")
    lines.append("")

    o = report.overall
    lines.append(f"Всего сделок: <b>{report.total_trades}</b>")
    lines.append(f"Прибыльных: <b>{o.win_rate:.0%}</b>")
    lines.append(f"Общая доходность: <b>{report.total_return_pct:+.2f}%</b>")
    lines.append(f"Максимальная просадка: {report.max_drawdown_pct:.1f}%")
    lines.append("")

    lines.append("<b>По классам сигналов:</b>")
    for tier, gs in sorted(report.by_tier.items()):
        emoji = "✅" if gs.win_rate >= 0.6 else "⚠️"
        lines.append(f"  {emoji} Класс {tier}: прибыльных={gs.win_rate:.0%}, доходность={gs.total_pnl_pct:+.2f}%")
    lines.append("")

    lines.append("<b>По рынкам:</b>")
    for market, gs in sorted(report.by_market.items()):
        label = "🇷🇺 РФ" if market == "ru" else "🇺🇸 US"
        lines.append(f"  {label}: прибыльных={gs.win_rate:.0%}, доходность={gs.total_pnl_pct:+.2f}%")
    lines.append("")

    lines.append("<b>По направлениям:</b>")
    for direction, gs in sorted(report.by_direction.items()):
        label = "📈 Покупка" if direction == "long" else "📉 Продажа"
        lines.append(f"  {label}: прибыльных={gs.win_rate:.0%}, доходность={gs.total_pnl_pct:+.2f}%")

    return "\n".join(lines)
