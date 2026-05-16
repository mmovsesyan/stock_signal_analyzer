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

import yfinance as yf

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
_log = logging.getLogger(__name__)

# ── yfinance retry helpers ───────────────────────────────────────────────────


def _yf_retry(func, max_retries: int = 3, backoff: float = 2.0):
    """Выполнить функцию с exponential backoff при rate limit."""
    last_err = None
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if "too many requests" in msg or "rate limited" in msg:
                wait = backoff * (2 ** attempt)
                _log.warning("yfinance rate limit (%s), retry in %.1fs (attempt %d/%d)",
                             type(e).__name__, wait, attempt + 1, max_retries)
                time.sleep(wait)
            else:
                raise
    raise last_err


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
        direction = signal.get('tp_direction') or signal.get('direction', '')
        if not direction or direction in ('none', 'neutral'):
            return {}
        entry = self._safe_price(signal.get('tp_entry')) or self._safe_price(signal.get('ref_price'))
        if not entry:
            return {}  # без цены входа трекать невозможно
        return {
            'direction': direction,
            'entry_price': entry,
            'stop_price': self._safe_price(signal.get('tp_stop')) or 0.0,
            'target1_price': self._safe_price(signal.get('tp_target1')) or 0.0,
            'target2_price': self._safe_price(signal.get('tp_target2')) or 0.0,
            'max_hold_days': int(signal.get('tp_max_hold_days') or 5),
        }

    def _get_signal_id(self, signal: dict[str, Any]) -> str:
        """Получить уникальный ID сигнала."""
        return f"{signal['symbol']}_{signal['ts_utc']}"

    def _get_current_price(self, symbol: str) -> float | None:
        """Получить текущую цену через yfinance с fallback и retry."""
        try:
            def _fetch():
                ticker = yf.Ticker(symbol)
                data = ticker.history(period='5d', interval='1d')
                if data.empty:
                    _log.warning("Нет данных для %s", symbol)
                    return None
                return float(data['Close'].iloc[-1])

            return _yf_retry(_fetch)

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
        max_hold_days = trade_plan.get('max_hold_days', 5)

        # Проверить, не истёк ли срок
        now = datetime.now(timezone.utc)
        days_since = (now - entry_date).days

        if days_since > max_hold_days:
            # Получить цену на момент истечения
            timeout_date = entry_date + timedelta(days=max_hold_days)
            try:
                def _fetch_timeout():
                    ticker = yf.Ticker(symbol)
                    return ticker.history(start=timeout_date, end=timeout_date + timedelta(days=5))

                hist = _yf_retry(_fetch_timeout)

                if not hist.empty:
                    exit_price = float(hist['Close'].iloc[0])
                    actual_exit_date = hist.index[0]
                    # Приводим дату к datetime aware
                    if hasattr(actual_exit_date, 'to_pydatetime'):
                        actual_exit_date = actual_exit_date.to_pydatetime()
                    pnl_pct = self._calculate_pnl(entry_price, exit_price, direction)

                    return SignalOutcome(
                        signal_id=signal_id,
                        symbol=symbol,
                        outcome='timeout',
                        exit_price=exit_price,
                        exit_date=actual_exit_date if isinstance(actual_exit_date, datetime) else timeout_date,
                        pnl_pct=pnl_pct,
                        hold_days=max_hold_days
                    )
            except Exception as e:
                _log.error("Ошибка получения цены для timeout %s: %s", symbol, e)

        # Получить историю с момента входа
        try:
            def _fetch_hist():
                ticker = yf.Ticker(symbol)
                return ticker.history(start=entry_date, end=now, interval='1d')

            hist = _yf_retry(_fetch_hist)

            if hist.empty:
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

        with open(self.outcomes_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

        # Добавить в checked если закрыт
        if outcome.outcome != 'open':
            self.checked_signals.add(outcome.signal_id)

    def check_all_outcomes(self, max_workers: int = 2):
        """Проверить все открытые сигналы (параллельно через ThreadPoolExecutor)."""
        signals = self._load_open_signals()

        if not signals:
            _log.info("Нет открытых сигналов для проверки")
            return

        _log.info(f"Проверка {len(signals)} сигналов (workers={max_workers})...")

        # Параллельно проверяем каждый сигнал
        outcomes_map: dict[str, SignalOutcome] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_signal = {
                executor.submit(self._check_signal_outcome, sig): sig
                for sig in signals
            }
            for future in as_completed(future_to_signal, timeout=300):
                sig = future_to_signal[future]
                try:
                    outcome = future.result(timeout=30)
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

        total = len(outcomes)
        win_count = len(wins)
        loss_count = len(losses)

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
