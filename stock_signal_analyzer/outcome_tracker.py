"""
Outcome Tracker - автоматическое отслеживание исходов открытых сигналов.

Проверяет открытые сигналы, определяет достижение целей/стопов,
записывает результаты, обновляет статистику.

Использование:
    # Разовая проверка
    python -m stock_signal_analyzer.outcome_tracker

    # Запуск как cron job (каждый час)
    0 * * * * cd /path/to/project && python -m stock_signal_analyzer.outcome_tracker
"""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .price_fetcher import fetch_current_price, fetch_price_for_outcome

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
_log = logging.getLogger(__name__)


@dataclass
class SignalOutcome:
    """Результат проверки сигнала."""
    signal_id: str
    symbol: str
    outcome: str  # 'win_t1' | 'win_t2' | 'loss' | 'timeout' | 'open'
    exit_price: float | None
    exit_date: datetime | None
    pnl_pct: float | None
    hold_days: int | None


# Конфигурация расходов на торговлю
_SLIPPAGE_PCT = 0.001  # 0.1% slippage на вход/выход
_COMMISSION_PCT = 0.0015  # 0.15% комиссия (типично для российских брокеров)


class OutcomeTracker:
    """Отслеживание исходов открытых торговых сигналов."""

    def __init__(
        self,
        signals_log_path: str | None = None,
        outcomes_path: str | None = None,
        slippage_pct: float = _SLIPPAGE_PCT,
        commission_pct: float = _COMMISSION_PCT,
    ):
        self.signals_log_path = signals_log_path or self._get_signals_log_path()
        self.outcomes_path = outcomes_path or self._get_outcomes_path()
        self.slippage_pct = slippage_pct
        self.commission_pct = commission_pct

        if not self.signals_log_path:
            raise ValueError("SSA_SIGNAL_LOG не задан. Установите переменную окружения.")

        self.signals_log = Path(self.signals_log_path)
        self.outcomes_file = Path(self.outcomes_path)

        # Загрузить уже проверенные сигналы
        self.checked_signals = self._load_checked_signals()
        # Track all (signal_id, outcome) pairs to prevent duplicate records across restarts
        self._seen_keys = self._load_seen_keys()

    def _get_signals_log_path(self) -> str | None:
        """Получить путь к логу сигналов из переменных окружения."""
        return os.environ.get("SSA_SIGNAL_LOG") or os.environ.get("SIGNAL_LOG_JSONL")

    def _get_outcomes_path(self) -> str:
        """Получить путь к файлу с результатами."""
        base_path = os.environ.get("STOCK_SIGNAL_DATA", "/var/lib/stock_signal_analyzer")
        return os.path.join(base_path, "outcomes.jsonl")

    def _load_checked_signals(self) -> set[str]:
        """Загрузить ID уже проверенных сигналов."""
        if not self.outcomes_file.exists():
            return set()

        checked = set()
        with open(self.outcomes_file, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    outcome = json.loads(line.strip())
                    if outcome.get('outcome') != 'open':
                        checked.add(outcome['signal_id'])
                except json.JSONDecodeError:
                    continue

        _log.info(f"Загружено {len(checked)} уже проверенных сигналов")
        return checked

    def _load_seen_keys(self) -> set[tuple[str, str]]:
        """Загрузить все (signal_id, outcome) пары для дедупликации."""
        if not self.outcomes_file.exists():
            return set()
        seen = set()
        with open(self.outcomes_file, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    rec = json.loads(line.strip())
                    seen.add((rec.get('signal_id', ''), rec.get('outcome', '')))
                except json.JSONDecodeError:
                    continue
        return seen

    def _load_open_signals(self) -> list[dict[str, Any]]:
        """Загрузить открытые сигналы из лога."""
        if not self.signals_log.exists():
            _log.warning(f"Файл сигналов не найден: {self.signals_log}")
            return []

        signals = []
        with open(self.signals_log, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    signal = json.loads(line.strip())

                    # Пропустить уже проверенные
                    signal_id = self._get_signal_id(signal)
                    if signal_id in self.checked_signals:
                        continue

                    # Signal log хранит торговый план в плоских ключах (tp_direction, tp_entry, ...)
                    # Собираем их во вложенный dict для единообразия.
                    trade_plan = signal.get('trade_plan')
                    if not trade_plan:
                        trade_plan = self._extract_trade_plan(signal)
                    if not trade_plan or trade_plan.get('direction') in ('none', '', None):
                        continue

                    signal['trade_plan'] = trade_plan
                    signals.append(signal)

                except json.JSONDecodeError:
                    continue

        _log.info(f"Найдено {len(signals)} открытых сигналов")
        return signals

    @staticmethod
    def _safe_price(value: Any) -> float | None:
        """Конвертировать в float > 0, иначе None."""
        if value is None:
            return None
        try:
            f = float(value)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None

    def _extract_trade_plan(self, signal: dict[str, Any]) -> dict[str, Any]:
        """Собрать trade_plan из плоских tp_* ключей signal log."""
        tp_dir = signal.get('tp_direction')
        # Если tp_direction задан (не None) и не 'none'/'neutral' — используем его
        if tp_dir is not None and tp_dir not in ('none', 'neutral'):
            direction = tp_dir
        else:
            # Иначе используем direction из сигнала
            direction = signal.get('direction', '')
            if not direction or direction in ('none', 'neutral'):
                return {}  # без направления трекать невозможно

        # Проверка цены входа
        entry = self._safe_price(signal.get('tp_entry')) or self._safe_price(signal.get('ref_price'))
        if not entry:
            return {}  # без цены входа трекать невозможно

        # Собрать все уровни из tp_* ключей (могут быть None для старых сигналов)
        stop_price = self._safe_price(signal.get('tp_stop'))
        target1_price = self._safe_price(signal.get('tp_target1'))
        target2_price = self._safe_price(signal.get('tp_target2'))

        # Если уровней нет, сгенерируем их из ATR
        if stop_price is None or target1_price is None:
            atr_pct = signal.get('atr_pct')
            if atr_pct and atr_pct > 0:
                ref_price = signal.get('ref_price', entry)
                atr_abs = ref_price * atr_pct / 100.0
                sign = 1.0 if direction == 'long' else -1.0
                # Консервативные множители
                stop_price = ref_price - sign * (1.5 * atr_abs)
                target1_price = ref_price + sign * (2.5 * atr_abs)
                target2_price = ref_price + sign * (4.0 * atr_abs)

        return {
            'direction': direction,
            'entry_price': entry,
            'stop_price': stop_price or 0.0,
            'target1_price': target1_price or 0.0,
            'target2_price': target2_price or 0.0,
            'max_hold_days': int(signal.get('tp_max_hold_days') or 15),
        }

    def _get_signal_id(self, signal: dict[str, Any]) -> str:
        """Получить уникальный ID сигнала."""
        return f"{signal['symbol']}_{signal['ts_utc']}"

    def _get_current_price(self, symbol: str) -> float | None:
        """Получить текущую цену через unified price fetcher."""
        try:
            return fetch_current_price(symbol)
        except Exception as e:
            _log.error("Ошибка получения цены для %s: %s", symbol, e)
            return None

    def _check_signal_outcome(self, signal: dict[str, Any]) -> SignalOutcome:
        """Проверить исход сигнала."""
        signal_id = self._get_signal_id(signal)
        symbol = signal['symbol']
        entry_date = datetime.fromisoformat(signal['ts_utc'].replace('Z', '+00:00'))

        trade_plan = signal['trade_plan']
        direction = trade_plan['direction']
        entry_price = trade_plan['entry_price']
        stop_price = trade_plan['stop_price']
        target1_price = trade_plan['target1_price']
        target2_price = trade_plan['target2_price']
        max_hold_days = trade_plan.get('max_hold_days', 15)

        # Проверить, не истёк ли срок
        now = datetime.now(timezone.utc)
        days_since = (now - entry_date).days

        if days_since > max_hold_days:
            # Получить цену на момент истечения
            timeout_date = entry_date + timedelta(days=max_hold_days)
            try:
                exit_price = fetch_price_for_outcome(symbol, timeout_date)
                if exit_price is not None:
                    pnl_pct = self._calculate_pnl(entry_price, exit_price, direction)
                    return SignalOutcome(
                        signal_id=signal_id,
                        symbol=symbol,
                        outcome='timeout',
                        exit_price=exit_price,
                        exit_date=timeout_date,
                        pnl_pct=pnl_pct,
                        hold_days=max_hold_days
                    )
            except Exception as e:
                _log.error("Ошибка получения цены для timeout %s: %s", symbol, e)

        # Получить историю с момента входа
        try:
            from .price_fetcher import fetch_history
            hist = fetch_history(symbol, entry_date, now)

            if hist is None or hist.empty:
                return SignalOutcome(
                    signal_id=signal_id,
                    symbol=symbol,
                    outcome='open',
                    exit_price=None,
                    exit_date=None,
                    pnl_pct=None,
                    hold_days=days_since
                )

            # Проверить каждый день — правильное определение порядка выходов
            for i, (date, row) in enumerate(hist.iterrows()):
                if i == 0:
                    continue  # Пропустить день входа

                high = float(row['High'])
                low = float(row['Low'])

                if direction == 'long':
                    stop_hit = stop_price > 0 and low <= stop_price
                    tp2_hit = target2_price > 0 and high >= target2_price
                    tp1_hit = target1_price > 0 and high >= target1_price

                    # Gap-aware: если открытие ниже стопа (gap down), реальный выход по open
                    open_price = float(row.get('Open', entry_price))
                    if open_price <= stop_price:
                        exit_p = min(open_price, stop_price)
                        pnl_pct = self._calculate_pnl(entry_price, exit_p, direction)
                        return SignalOutcome(
                            signal_id=signal_id, symbol=symbol, outcome='loss',
                            exit_price=exit_p, exit_date=date, pnl_pct=pnl_pct, hold_days=i
                        )

                    if stop_hit and not tp1_hit:
                        # Только стоп — loss
                        pnl_pct = self._calculate_pnl(entry_price, stop_price, direction)
                        return SignalOutcome(
                            signal_id=signal_id, symbol=symbol, outcome='loss',
                            exit_price=stop_price, exit_date=date, pnl_pct=pnl_pct, hold_days=i
                        )
                    elif tp2_hit and not stop_hit:
                        pnl_pct = self._calculate_pnl(entry_price, target2_price, direction)
                        return SignalOutcome(
                            signal_id=signal_id, symbol=symbol, outcome='win_t2',
                            exit_price=target2_price, exit_date=date, pnl_pct=pnl_pct, hold_days=i
                        )
                    elif tp1_hit and not stop_hit:
                        pnl_pct = self._calculate_pnl(entry_price, target1_price, direction)
                        return SignalOutcome(
                            signal_id=signal_id, symbol=symbol, outcome='win_t1',
                            exit_price=target1_price, exit_date=date, pnl_pct=pnl_pct, hold_days=i
                        )
                    elif stop_hit and tp1_hit:
                        # И стоп, и TP задеты в один день: используем open для определения порядка.
                        # Консервативный подход: если open ближе к стопу — loss, иначе win.
                        open_price = float(row.get('Open', entry_price))
                        if abs(open_price - stop_price) <= abs(open_price - target1_price):
                            pnl_pct = self._calculate_pnl(entry_price, stop_price, direction)
                            return SignalOutcome(
                                signal_id=signal_id, symbol=symbol, outcome='loss',
                                exit_price=stop_price, exit_date=date, pnl_pct=pnl_pct, hold_days=i
                            )
                        else:
                            # Open был ближе к TP — сначала достигнут TP
                            if tp2_hit:
                                pnl_pct = self._calculate_pnl(entry_price, target2_price, direction)
                                return SignalOutcome(
                                    signal_id=signal_id, symbol=symbol, outcome='win_t2',
                                    exit_price=target2_price, exit_date=date, pnl_pct=pnl_pct, hold_days=i
                                )
                            else:
                                pnl_pct = self._calculate_pnl(entry_price, target1_price, direction)
                                return SignalOutcome(
                                    signal_id=signal_id, symbol=symbol, outcome='win_t1',
                                    exit_price=target1_price, exit_date=date, pnl_pct=pnl_pct, hold_days=i
                                )

                elif direction == 'short':
                    stop_hit = stop_price > 0 and high >= stop_price
                    tp2_hit = target2_price > 0 and low <= target2_price
                    tp1_hit = target1_price > 0 and low <= target1_price

                    # Gap-aware: если открытие выше стопа (gap up), реальный выход по open
                    open_price = float(row.get('Open', entry_price))
                    if open_price >= stop_price:
                        exit_p = max(open_price, stop_price)
                        pnl_pct = self._calculate_pnl(entry_price, exit_p, direction)
                        return SignalOutcome(
                            signal_id=signal_id, symbol=symbol, outcome='loss',
                            exit_price=exit_p, exit_date=date, pnl_pct=pnl_pct, hold_days=i
                        )

                    if stop_hit and not tp1_hit:
                        # Стоп сработал — loss
                        pnl_pct = self._calculate_pnl(entry_price, stop_price, direction)
                        return SignalOutcome(
                            signal_id=signal_id, symbol=symbol, outcome='loss',
                            exit_price=stop_price, exit_date=date, pnl_pct=pnl_pct, hold_days=i
                        )
                    elif tp2_hit and not stop_hit:
                        pnl_pct = self._calculate_pnl(entry_price, target2_price, direction)
                        return SignalOutcome(
                            signal_id=signal_id, symbol=symbol, outcome='win_t2',
                            exit_price=target2_price, exit_date=date, pnl_pct=pnl_pct, hold_days=i
                        )
                    elif tp1_hit and not stop_hit:
                        pnl_pct = self._calculate_pnl(entry_price, target1_price, direction)
                        return SignalOutcome(
                            signal_id=signal_id, symbol=symbol, outcome='win_t1',
                            exit_price=target1_price, exit_date=date, pnl_pct=pnl_pct, hold_days=i
                        )
                    elif stop_hit and tp1_hit:
                        # Тот же консервативный подход для short
                        open_price = float(row.get('Open', entry_price))
                        if abs(open_price - stop_price) <= abs(open_price - target1_price):
                            pnl_pct = self._calculate_pnl(entry_price, stop_price, direction)
                            return SignalOutcome(
                                signal_id=signal_id, symbol=symbol, outcome='loss',
                                exit_price=stop_price, exit_date=date, pnl_pct=pnl_pct, hold_days=i
                            )
                        else:
                            if tp2_hit:
                                pnl_pct = self._calculate_pnl(entry_price, target2_price, direction)
                                return SignalOutcome(
                                    signal_id=signal_id, symbol=symbol, outcome='win_t2',
                                    exit_price=target2_price, exit_date=date, pnl_pct=pnl_pct, hold_days=i
                                )
                            else:
                                pnl_pct = self._calculate_pnl(entry_price, target1_price, direction)
                                return SignalOutcome(
                                    signal_id=signal_id, symbol=symbol, outcome='win_t1',
                                    exit_price=target1_price, exit_date=date, pnl_pct=pnl_pct, hold_days=i
                                )

            # Всё ещё открыт
            return SignalOutcome(
                signal_id=signal_id,
                symbol=symbol,
                outcome='open',
                exit_price=None,
                exit_date=None,
                pnl_pct=None,
                hold_days=days_since
            )

        except Exception as e:
            _log.error(f"Ошибка проверки исхода для {symbol}: {e}")
            return SignalOutcome(
                signal_id=signal_id,
                symbol=symbol,
                outcome='open',
                exit_price=None,
                exit_date=None,
                pnl_pct=None,
                hold_days=days_since
            )

    def _calculate_pnl(self, entry: float, exit: float, direction: str) -> float:
        """Рассчитать PnL в процентах с учётом slippage и комиссии.

        Slippage: цена входа хуже на slippage_pct, цена выхода хуже на slippage_pct
        Commission: комиссия на вход + комиссия на выход (commission_pct каждый)
        """
        if entry <= 0 or exit <= 0:
            return 0.0

        # Gross PnL
        if direction == 'long':
            gross_pct = (exit - entry) / entry * 100
        else:
            gross_pct = (entry - exit) / entry * 100

        # Costs: slippage on entry + exit (2x), commission on entry + exit (2x)
        total_cost_pct = (self.slippage_pct * 2 + self.commission_pct * 2) * 100

        return gross_pct - total_cost_pct

    def _save_outcome(self, outcome: SignalOutcome, signal: dict[str, Any]):
        """Сохранить результат в файл и обогатить signal log для IC."""
        # Цена входа: trade_plan.entry_price или ref_price из сигнала
        trade_plan = signal.get('trade_plan') or {}
        entry_price = (
            trade_plan.get('entry_price')
            or signal.get('tp_entry')
            or signal.get('ref_price')
        )

        record = {
            'signal_id': outcome.signal_id,
            'symbol': outcome.symbol,
            'outcome': outcome.outcome,
            'entry_price': entry_price,
            'exit_price': outcome.exit_price,
            'exit_date': outcome.exit_date.isoformat() if outcome.exit_date else None,
            'pnl_pct': outcome.pnl_pct,
            'hold_days': outcome.hold_days,
            'direction': trade_plan.get('direction') or signal.get('direction') or signal.get('tp_direction'),
            'signal_tier': signal.get('signal_tier'),
            'confidence': signal.get('confidence'),
            'entry_date': signal['ts_utc'],
            'checked_at': datetime.now(timezone.utc).isoformat(),
            # Компонентные scores для IC-анализа в adaptive_weights
            'technical_score': signal.get('technical_score'),
            'momentum_score': signal.get('momentum_score'),
            'news_score': signal.get('news_score'),
            'volume_score': signal.get('volume_score'),
            'score': signal.get('score'),
            'outcome_pnl': outcome.pnl_pct,
        }

        # Создать директорию если нужно
        self.outcomes_file.parent.mkdir(parents=True, exist_ok=True)

        # Skip duplicate entries by (signal_id, outcome)
        key = (outcome.signal_id, outcome.outcome)
        if key in self._seen_keys:
            pass
        else:
            with open(self.outcomes_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
            self._seen_keys.add(key)

        # Добавить в checked если закрыт
        if outcome.outcome != 'open':
            self.checked_signals.add(outcome.signal_id)

    def check_all_outcomes(self, max_workers: int = None) -> dict[str, int]:
        """Проверить все открытые сигналы (параллельно через ThreadPoolExecutor)."""
        signals = self._load_open_signals()

        if not signals:
            _log.info("Нет открытых сигналов для проверки")
            return {'closed': 0, 'pending': 0, 'total': 0}

        if max_workers is None:
            max_workers = int(os.environ.get("OUTCOME_MAX_WORKERS", "3"))
        total_timeout = int(os.environ.get("OUTCOME_TOTAL_TIMEOUT", "900"))
        per_future_timeout = int(os.environ.get("OUTCOME_PER_FUTURE_TIMEOUT", "60"))
        _log.info(f"Проверка {len(signals)} сигналов (workers={max_workers}, total_timeout={total_timeout})...")

        # Параллельно проверяем каждый сигнал
        outcomes_map: dict[str, SignalOutcome] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_signal = {
                executor.submit(self._check_signal_outcome, sig): sig
                for sig in signals
            }
            try:
                for future in as_completed(future_to_signal, timeout=total_timeout):
                    sig = future_to_signal[future]
                    try:
                        outcome = future.result(timeout=per_future_timeout)
                        outcomes_map[self._get_signal_id(sig)] = outcome
                    except Exception as e:
                        _log.error("Outcome check failed for %s: %s", sig['symbol'], e)
                        # Fallback: оставить открытым
                        outcomes_map[self._get_signal_id(sig)] = SignalOutcome(
                            signal_id=self._get_signal_id(sig),
                            symbol=sig['symbol'],
                            outcome='open',
                            exit_price=None,
                            exit_date=None,
                            pnl_pct=None,
                            hold_days=None,
                        )
            except TimeoutError:
                unfinished = sum(1 for f in future_to_signal if not f.done())
                _log.warning("Outcome check total timeout (%ss) reached, %d futures unfinished — leaving as open", total_timeout, unfinished)
                for future, sig in future_to_signal.items():
                    if future.done():
                        try:
                            outcome = future.result(timeout=per_future_timeout)
                            outcomes_map[self._get_signal_id(sig)] = outcome
                        except Exception as e:
                            _log.error("Outcome check failed for %s: %s", sig['symbol'], e)
                            outcomes_map[self._get_signal_id(sig)] = SignalOutcome(
                                signal_id=self._get_signal_id(sig),
                                symbol=sig['symbol'],
                                outcome='open',
                                exit_price=None,
                                exit_date=None,
                                pnl_pct=None,
                                hold_days=None,
                            )

        # Последовательно сохраняем результаты (файловая запись — не thread-safe)
        # Сохраняем только закрытые сигналы, чтобы не раздувать файл.
        closed_count = 0
        for signal in signals:
            signal_id = self._get_signal_id(signal)
            outcome = outcomes_map.get(signal_id)
            if outcome and outcome.outcome != 'open':
                self._save_outcome(outcome, signal)
                closed_count += 1
                _log.info(
                    f"✓ {outcome.symbol}: {outcome.outcome} "
                    f"(PnL: {outcome.pnl_pct:+.2f}%, {outcome.hold_days} дней)"
                )

        _log.info(f"Закрыто сигналов: {closed_count}/{len(signals)}")
        return {'closed': closed_count, 'pending': len(signals) - closed_count, 'total': len(signals)}

    def get_statistics(self, tier: str | None = None) -> dict[str, Any]:
        """Получить статистику по результатам."""
        if not self.outcomes_file.exists():
            return {}

        outcomes = []
        with open(self.outcomes_file, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    outcome = json.loads(line.strip())
                    if outcome['outcome'] == 'open':
                        continue
                    if tier and outcome.get('signal_tier') != tier:
                        continue
                    outcomes.append(outcome)
                except json.JSONDecodeError:
                    continue

        if not outcomes:
            return {}

        # Фильтруем записи с нулевым pnl_pct чтобы избежать TypeError
        wins = [o for o in outcomes if o['outcome'] in ['win_t1', 'win_t2'] and o.get('pnl_pct') is not None]
        losses = [o for o in outcomes if o['outcome'] == 'loss' and o.get('pnl_pct') is not None]
        all_with_pnl = [o for o in outcomes if o.get('pnl_pct') is not None]

        win_count = len(wins)
        loss_count = len(losses)
        total = win_count + loss_count  # исключаем timeout из расчета win_rate

        win_rate = win_count / total if total > 0 else 0.0

        avg_win = sum(o['pnl_pct'] for o in wins) / len(wins) if wins else 0.0
        avg_loss = sum(abs(o['pnl_pct']) for o in losses) / len(losses) if losses else 0.0

        total_win = sum(o['pnl_pct'] for o in wins)
        total_loss = sum(abs(o['pnl_pct']) for o in losses)

        # ZeroDivisionError guard
        profit_factor = total_win / total_loss if total_loss > 0 else 0.0

        return {
            'total_signals': total,
            'winning_trades': win_count,
            'losing_trades': loss_count,
            'win_rate': win_rate,
            'avg_win_pct': avg_win,
            'avg_loss_pct': avg_loss,
            'profit_factor': profit_factor,
            'total_pnl_pct': sum(o['pnl_pct'] for o in all_with_pnl)
        }


def main():
    """Главная функция для запуска из командной строки."""
    tracker = OutcomeTracker()

    _log.info("=== Outcome Tracker ===")
    tracker.check_all_outcomes()

    _log.info("\n=== Статистика ===")
    for tier in ['A', 'B', 'C', None]:
        stats = tracker.get_statistics(tier)
        if not stats:
            continue

        tier_label = f"Класс {tier}" if tier else "Все сигналы"
        _log.info(f"\n{tier_label}:")
        _log.info(f"  Всего: {stats['total_signals']}")
        _log.info(f"  Win rate: {stats['win_rate']*100:.1f}%")
        _log.info(f"  Profit factor: {stats['profit_factor']:.2f}")
        _log.info(f"  Avg win: {stats['avg_win_pct']:.2f}%")
        _log.info(f"  Avg loss: {stats['avg_loss_pct']:.2f}%")
        _log.info(f"  Total PnL: {stats['total_pnl_pct']:.2f}%")


if __name__ == '__main__':
    main()
