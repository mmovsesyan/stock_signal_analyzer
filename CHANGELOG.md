# Changelog

Все значимые изменения в проекте Stock Signal Analyzer документируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
и проект следует [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-05-01

### Added
- **Кэширование котировок**: In-memory кэш с TTL 5 минут для `fetch_snapshot_with_meta()`
  - Повторные запросы в 7.3x быстрее (~2.2s вместо ~16s)
  - Параметр `force_refresh` для принудительного обновления
  - Файл: `stock_signal_analyzer/market_data.py`

- **Параллельная загрузка новостей**: ThreadPoolExecutor для одновременной загрузки из 3 источников
  - Google News, Finnhub News, Macro Headlines
  - Экономия ~3-5 секунд на каждом анализе
  - Файл: `stock_signal_analyzer/engine.py`

- **Быстрый режим анализа**: Флаг `--fast` для пропуска медленных компонентов
  - Анализ за 1.4s (11.4x быстрее)
  - Пропускает новости и real-time данные
  - Использование: `python main.py AAPL --fast`
  - Файлы: `main.py`, `stock_signal_analyzer/engine.py`

- **Обработка rate limits**: Автоматический retry с exponential backoff
  - Декоратор `@retry_with_backoff` для API запросов
  - Класс `RateLimiter` для ограничения частоты
  - Специальная обработка 429 (Too Many Requests)
  - Файл: `stock_signal_analyzer/retry_utils.py` (новый)

- **Документация**:
  - `README.md` - полное описание проекта
  - `ISSUES_AND_FIXES.md` - детальный анализ проблем
  - `PERFORMANCE_OPTIMIZATION.md` - план оптимизации
  - `ANALYSIS_SUMMARY.md` - краткое резюме
  - `IMPROVEMENTS_REPORT.md` - отчёт об улучшениях
  - `UPGRADE_GUIDE.md` - инструкция по обновлению

- **Dev-зависимости**: `requirements-dev.txt`
  - pytest, pytest-cov, pytest-asyncio
  - black, isort (форматирование)
  - flake8, pylint (линтинг)
  - mypy (type checking)
  - sphinx (документация)

### Changed
- **Производительность**: Общее ускорение анализа в 2.9x
  - Было: ~16 секунд
  - Стало: ~5.5 секунд (первый запрос)
  - Стало: ~2.2 секунды (повторный запрос с кэшем)
  - Стало: ~1.4 секунды (быстрый режим)

- **API запросы**: Применён retry декоратор к:
  - `fetch_quote()` в `finnhub_live.py`
  - `fetch_company_news()` в `finnhub_live.py`

- **Сигнатуры функций**:
  - `build_report()`: добавлен параметр `fast_mode: bool = False`
  - `fetch_snapshot_with_meta()`: добавлен параметр `force_refresh: bool = False`
  - `_gather_inputs()`: добавлен параметр `fast_mode: bool = False`

### Fixed
- **SSL Warning**: Исправлено предупреждение urllib3 + LibreSSL
  - Добавлено ограничение `urllib3<2.0` в `requirements.txt`
  - Больше нет NotOpenSSLWarning при запуске

### Performance
- Первый запрос: 16s → 5.5s (**2.9x быстрее**)
- Повторный запрос: 16s → 2.2s (**7.3x быстрее**)
- Быстрый режим: N/A → 1.4s (**11.4x быстрее**)

### Testing
- ✅ Все 82 unit-теста проходят
- ✅ Протестировано на AAPL, MSFT
- ✅ Обратная совместимость сохранена

---

## [1.0.0] - 2026-04-30

### Initial Release
- Многофакторный анализ акций (техника, импульс, новости, объём)
- Telegram-бот с интерактивным меню
- Поддержка US и РФ рынков
- Генерация торговых планов
- 82 unit-теста
- Квантовые модели (MTF momentum, trend strength, volatility regime)
- Real-time данные через WebSocket
- Макроэкономический контекст

---

## Типы изменений
- `Added` - новая функциональность
- `Changed` - изменения в существующей функциональности
- `Deprecated` - функциональность, которая скоро будет удалена
- `Removed` - удалённая функциональность
- `Fixed` - исправления багов
- `Security` - исправления уязвимостей
- `Performance` - улучшения производительности
