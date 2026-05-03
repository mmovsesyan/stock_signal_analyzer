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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yfinance as yf

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


class OutcomeTracker:
    """Отслеживание исходов открытых торговых сигналов."""

    def __init__(self, signals_log_path: str | None = None, outcomes_path: str | None = None):
        self.signals_log_path = signals_log_path or self._get_signals_log_path()
        self.outcomes_path = outcomes_path or self._get_outcomes_path()

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
    def _extract_trade_plan(signal: dict[str, Any]) -> dict[str, Any]:
        """Собрать trade_plan из плоских tp_* ключей signal log."""
        direction = signal.get('tp_direction') or signal.get('direction', '')
        if not direction or direction in ('none', 'neutral'):
            return {}
        return {
            'direction': direction,
            'entry_price': float(signal.get('tp_entry') or signal.get('ref_price', 0)),
            'stop_price': float(signal.get('tp_stop', 0)),
            'target1_price': float(signal.get('tp_target1', 0)),
            'target2_price': float(signal.get('tp_target2', 0)),
            'max_hold_days': int(signal.get('tp_max_hold_days', 5)),
        }

    def _get_signal_id(self, signal: dict[str, Any]) -> str:
        """Получить уникальный ID сигнала."""
        return f"{signal['symbol']}_{signal['ts_utc']}"

    def _get_current_price(self, symbol: str) -> float | None:
        """Получить текущую цену."""
        try:
            ticker = yf.Ticker(symbol)
            data = ticker.history(period='1d', interval='1m')

            if data.empty:
                _log.warning(f"Нет данных для {symbol}")
                return None

            return float(data['Close'].iloc[-1])

        except Exception as e:
            _log.error(f"Ошибка получения цены для {symbol}: {e}")
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
                ticker = yf.Ticker(symbol)
                hist = ticker.history(start=timeout_date, end=timeout_date + timedelta(days=1))

                if not hist.empty:
                    exit_price = float(hist['Close'].iloc[0])
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
                _log.error(f"Ошибка получения цены для timeout {symbol}: {e}")

        # Получить историю с момента входа
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(start=entry_date, end=now, interval='1d')

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

            # Проверить каждый день
            for i, (date, row) in enumerate(hist.iterrows()):
                if i == 0:
                    continue  # Пропустить день входа

                high = float(row['High'])
                low = float(row['Low'])

                if direction == 'long':
                    # Проверить стоп
                    if low <= stop_price:
                        pnl_pct = self._calculate_pnl(entry_price, stop_price, direction)
                        return SignalOutcome(
                            signal_id=signal_id,
                            symbol=symbol,
                            outcome='loss',
                            exit_price=stop_price,
                            exit_date=date,
                            pnl_pct=pnl_pct,
                            hold_days=i
                        )

                    # Проверить target2
                    if high >= target2_price:
                        pnl_pct = self._calculate_pnl(entry_price, target2_price, direction)
                        return SignalOutcome(
                            signal_id=signal_id,
                            symbol=symbol,
                            outcome='win_t2',
                            exit_price=target2_price,
                            exit_date=date,
                            pnl_pct=pnl_pct,
                            hold_days=i
                        )

                    # Проверить target1
                    if high >= target1_price:
                        pnl_pct = self._calculate_pnl(entry_price, target1_price, direction)
                        return SignalOutcome(
                            signal_id=signal_id,
                            symbol=symbol,
                            outcome='win_t1',
                            exit_price=target1_price,
                            exit_date=date,
                            pnl_pct=pnl_pct,
                            hold_days=i
                        )

                elif direction == 'short':
                    # Проверить стоп
                    if high >= stop_price:
                        pnl_pct = self._calculate_pnl(entry_price, stop_price, direction)
                        return SignalOutcome(
                            signal_id=signal_id,
                            symbol=symbol,
                            outcome='loss',
                            exit_price=stop_price,
                            exit_date=date,
                            pnl_pct=pnl_pct,
                            hold_days=i
                        )

                    # Проверить target2
                    if low <= target2_price:
                        pnl_pct = self._calculate_pnl(entry_price, target2_price, direction)
                        return SignalOutcome(
                            signal_id=signal_id,
                            symbol=symbol,
                            outcome='win_t2',
                            exit_price=target2_price,
                            exit_date=date,
                            pnl_pct=pnl_pct,
                            hold_days=i
                        )

                    # Проверить target1
                    if low <= target1_price:
                        pnl_pct = self._calculate_pnl(entry_price, target1_price, direction)
                        return SignalOutcome(
                            signal_id=signal_id,
                            symbol=symbol,
                            outcome='win_t1',
                            exit_price=target1_price,
                            exit_date=date,
                            pnl_pct=pnl_pct,
                            hold_days=i
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
        """Рассчитать PnL в процентах."""
        if direction == 'long':
            return (exit - entry) / entry * 100
        else:
            return (entry - exit) / entry * 100

    def _save_outcome(self, outcome: SignalOutcome, signal: dict[str, Any]):
        """Сохранить результат в файл и обогатить signal log для IC."""
        record = {
            'signal_id': outcome.signal_id,
            'symbol': outcome.symbol,
            'outcome': outcome.outcome,
            'exit_price': outcome.exit_price,
            'exit_date': outcome.exit_date.isoformat() if outcome.exit_date else None,
            'pnl_pct': outcome.pnl_pct,
            'hold_days': outcome.hold_days,
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

    def check_all_outcomes(self):
        """Проверить все открытые сигналы."""
        signals = self._load_open_signals()

        if not signals:
            _log.info("Нет открытых сигналов для проверки")
            return

        _log.info(f"Проверка {len(signals)} сигналов...")

        closed_count = 0
        for signal in signals:
            outcome = self._check_signal_outcome(signal)
            self._save_outcome(outcome, signal)

            if outcome.outcome != 'open':
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

        wins = [o for o in outcomes if o['outcome'] in ['win_t1', 'win_t2']]
        losses = [o for o in outcomes if o['outcome'] == 'loss']

        total = len(outcomes)
        win_count = len(wins)
        loss_count = len(losses)

        win_rate = win_count / total if total > 0 else 0.0

        avg_win = sum(o['pnl_pct'] for o in wins) / len(wins) if wins else 0.0
        avg_loss = sum(abs(o['pnl_pct']) for o in losses) / len(losses) if losses else 0.0

        total_win = sum(o['pnl_pct'] for o in wins)
        total_loss = sum(abs(o['pnl_pct']) for o in losses)

        profit_factor = total_win / total_loss if total_loss > 0 else 0.0

        return {
            'total_signals': total,
            'winning_trades': win_count,
            'losing_trades': loss_count,
            'win_rate': win_rate,
            'avg_win_pct': avg_win,
            'avg_loss_pct': avg_loss,
            'profit_factor': profit_factor,
            'total_pnl_pct': sum(o['pnl_pct'] for o in outcomes)
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
