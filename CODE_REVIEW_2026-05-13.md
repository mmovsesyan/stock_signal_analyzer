# Code Review — Stock Signal Analyzer
**Дата:** 2026-05-13
**Ревьюер:** Bob (сеньор-уровень, 10+ лет опыта)
**Объём:** ~50 Python-файлов, ~18K строк

---

## 📊 Общая оценка: 7.5/10

**Что хорошо:**
- Амбициозная архитектура: multi-source data, adaptive weights, LLM blending, institutional risk management
- Правильные fallback-цепочки (Yahoo → T-Bank → MOEX ISS → Polygon)
- Provider pattern для intraday — расширяемо
- Thread-safe кэши и атомарные записи файлов
- Good use of dataclasses, typing, `from __future__ import annotations`

**Что требует внимания:**
- 3 критических бага
- 8 серьёзных проблем
- 15+ улучшений архитектуры

---

## 🔴 КРИТИЧЕСКИЕ БАГИ (требуют немедленного исправления)

### 1. Race condition в scheduler.py — `_health_state` без блокировки

```python
# scheduler.py, строка ~45
_health_state: dict[str, Any] = { ... }
```

`_health_state` мутируется из разных потоков (APScheduler loop) без `threading.Lock`. При concurrent writes — corrupted state.

**Фикс:**
```python
_health_lock = threading.Lock()

def run_outcome_check():
    with _health_lock:
        _health_state["last_outcome_check"] = datetime.now(timezone.utc).isoformat()
        _health_state["errors"].append(...)
        _health_state["errors"] = _health_state["errors"][-10:]
```

### 2. `outcome_tracker.py` — неверный порядок проверок stop/TP для long

```python
# outcome_tracker.py, _check_signal_outcome()
if stop_hit and not tp2_hit and not tp1_hit:
    # loss
elif tp2_hit and not stop_hit:
    # win_t2
```

Проблема: если в один день `low <= stop` И `high >= target1`, стоп сработал первым по хронологии (цена упала до стопа, потом развернулась к TP). Но код считает это как TP win. Для корректного определения нужно проверять внутридневные данные (hourly/minute), не daily bars.

**Фикс:** Использовать intraday данные для проверки порядка выходов, или хотя бы документировать это ограничение. Для daily bars — считать одновременно задетые уровни как "uncertain" outcome.

### 3. `_cache_eviction` в regime.py — `NameError`

```python
# regime.py
_regime_cache: CrossAssetRegime | None = None
```

`CrossAssetRegime` используется в type hint ДО его определения (dataclass объявлен ниже). В runtime это работает благодаря `from __future__ import annotations` (PEP 563), но если кто-то уберёт этот импорт — сломается.

**Минорный риск, но стоит вынести dataclass вверх файла.**

---

## 🟠 СЕРЬЁЗНЫЕ ПРОБЛЕМЫ

### 4. `_daily_usage` в subscriptions.py — не переживает рестарт

```python
_daily_usage: dict[int, dict[str, Any]] = {}
```

In-memory rate counter. При рестарте бота все счётчики сбрасываются — пользователи могут обойти лимиты простым перезапуском.

**Фикс:** Хранить в БД (таблица `daily_usage`) или Redis.

### 5. `market_data.py` — `_CACHE` не thread-safe

```python
_CACHE: dict[str, tuple[tuple[Any, Any, Any], float]] = {}
```

Кэш мутируется из `_gather_inputs()` который может вызываться параллельно (ThreadPoolExecutor для новостей). Concurrent dict mutation = potential corruption.

**Фикс:** Добавить `threading.Lock()` вокруг всех операций с `_CACHE`.

### 6. `engine.py` — `_compute_score()` модифицирует веса после adaptive blending

```python
# engine.py, _gather_inputs()
if adaptive_w is not None and adaptive_w.adapted:
    _AW_BLEND = 0.30
    wt = wt * (1.0 - _AW_BLEND) + aw.get("technical", wt) * _AW_BLEND
    # ... ещё llm_learning adjustments
```

Два последовательных блока (adaptive weights + llm_learning) оба модифицируют `wt/wm/wn/wi`. Второй блок работает уже с изменёнными значениями первого. Это intended? Если да — задокументировать. Если нет — применить оба к base weights, потом нормализовать один раз.

### 7. `trade_plan.py` — `_position_size()` не используется

```python
def _position_size(confidence: float) -> float:
    """0..100% от базовой позиции..."""
```

Функция определена, но в `build_trade_plan()` вместо неё используется `institutional_size_pct` из `risk_manager.compute_position_size()`. Dead code.

**Фикс:** Удалить или использовать как fallback когда `institutional_size_pct is None`.

### 8. `outcome_tracker.py` — yfinance для проверок outcomes = медленно и ненадёжно

`OutcomeTracker` делает yfinance запросы для КАЖДОГО открытого сигнала. При 100+ сигналах — это 100+ HTTP запросов, каждый может занять 1-3 секунды. Общее время: 2-5 минут.

**Фикс:** 
- Батчить запросы (yf позволяет запросить несколько тикеров)
- Добавить timeout + concurrent.futures
- Или использовать тот же data provider что и основная система

### 9. `db.py` — `get_session()` auto-commits при `yield`

```python
@contextmanager
def get_session() -> Generator[Session, None, None]:
    session = _SessionFactory()
    try:
        yield session
        session.commit()  # ← commit даже если caller не сделал изменений
    except Exception:
        session.rollback()
        raise
```

Это стандартный паттерн, но он опасен: если caller сделал `session.query()` (read-only) и возникла ошибка после yield но до выхода — rollback правильный. Однако если caller забыл обработать exception — commit происходит автоматически.

**Рекомендация:** Добавить явный `session.flush()` перед commit для лучшей диагностики.

### 10. `telegram_bot.py` — потенциально огромный message без разбивки

`format_dashboard_bundle()` может генерировать HTML > 4096 символов. `split_telegram_html()` есть, но нужно убедиться что все вызовы `bot.send_message()` используют его.

---

## 🟡 АРХИТЕКТУРНЫЕ УЛУЧШЕНИЯ

### 11. Дублирование data path логики

Три разных модуля имеют свою логику путей к данным:
- `user_store.py`: `STOCK_SIGNAL_DATA` env → `data/telegram_users.json`
- `signal_log.py`: `SSA_SIGNAL_LOG` / `SIGNAL_LOG_JSONL` env
- `outcome_tracker.py`: `STOCK_SIGNAL_DATA` env → `outcomes.jsonl`
- `risk_manager.py`: `SSA_DRAWDOWN_STATE` env

**Рекомендация:** Единый `config.get_data_path(name)` в `config.py`.

### 12. `scheduler.py` — crude custom scheduler вместо APScheduler

```python
# scheduler.py — self-made scheduler loop
while not _scheduler_stop.is_set():
    now = time.time()
    for interval, func, name in intervals:
        if now - last_run[name] >= interval:
            func()
```

Название модуля — `scheduler.py`, переменная — `SCHEDULER_MODE = "apscheduler"`, но это НЕ APScheduler. Это custom loop.

**Рекомендация:** Либо реально использовать `APScheduler` библиотеку, либо переименовать в `task_runner.py` и убрать misleading naming.

### 13. `engine.py` — `_gather_inputs()` делает слишком много

Функция ~200 строк, 25+ действий: fetch data, analyze technical, momentum, news, intraday, volume, macro, timing, levels, quant models, adaptive weights, LLM learning, live price.

**Рекомендация:** Разбить на pipeline stages:
```python
def _stage_fetch_data(symbol) -> RawData
def _stage_technical(raw) -> TechnicalResult
def _stage_sentiment(raw) -> SentimentResult
def _stage_combine(results) -> SignalReport
```

### 14. Нет единого config object

Конфигурация размазана по env vars в каждом модуле:
- `OLLAMA_HOST`, `OLLAMA_MODEL`, `LLM_SENTIMENT`, `LLM_CACHE_TTL`
- `SCHEDULER_MODE`, `OUTCOME_INTERVAL_SEC`, `LEARN_INTERVAL_SEC`
- `DATABASE_URL`
- `SUBSCRIPTION_ENABLED`
- `STOCK_SIGNAL_DATA`, `SSA_SIGNAL_LOG`, `SSA_DRAWDOWN_STATE`

**Рекомендация:** `Settings` dataclass в `config.py` с `@lru_cache` загрузкой всех env vars.

### 15. `risk_manager.py` — Kelly из outcomes может быть stale

```python
def compute_position_size(...):
    real_kelly = kelly_from_outcomes()  # читает outcomes.jsonl каждый раз
```

Каждый вызов `build_report()` читает весь outcomes.jsonl с диска. При большом файле — I/O bottleneck.

**Фикс:** Кэшировать Kelly результат с TTL (напр. 1 час).

### 16. `outcome_tracker.py` — `_check_signal_outcome()` не учитывает gap down

Для long позиции: если `low <= stop`, считается что стоп сработал по `stop_price`. Но при gap down реальная цена исполнения может быть значительно ниже стопа.

**Фикс:** Для gap-down сценариев использовать `min(open, stop_price)` как exit price.

### 17. Отсутствие retry logic для HTTP запросов

`yfinance`, `requests` (Ollama, Finnhub) — все без retry. Один transient network error = failed analysis.

**Рекомендация:** Использовать `tenacity` library или простой retry wrapper:
```python
def _with_retry(func, max_retries=3, backoff=1.0):
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(backoff * (2 ** attempt))
```

### 18. `tests/test_core.py` — минимальное покрытие

Нужны тесты для:
- `technical.analyze_technical()` с mock OHLCV data
- `trade_plan.build_trade_plan()` edge cases
- `signal_filter.SignalFilter.filter()` 
- `risk_manager` Kelly, vol targeting, drawdown
- `market_data._symbol_for_yahoo()` нормализация

---

## 🟢 МЕЛКИЕ ЗАМЕЧАНИЯ

### 19. Magic numbers повсюду
```python
# engine.py
_VOL_BLEND = 0.08
_VERDICT_BASE_THR = 0.35
_CONF_BASE = 0.42
_INTRADAY_CONFLICT_MULT = 0.92
```
Объясните происхождение этих чисел в комментариях (backtested? heuristic? paper reference?).

### 20. `telegram_format.py` — `_plain_language_summary()` импортирует `re` внутри функции

```python
import re as _re  # ← внутри функции, хотя re уже импортирован на уровне модуля
```

### 21. `momentum.py` — `_roc_acceleration()` использует magic indices

```python
roc_now = float(close.iloc[-1] / close.iloc[-6] - 1.0)
roc_prev = float(close.iloc[-6] / close.iloc[-11] - 1.0)
```
Магические `-6`, `-11` — это 5-дневный ROC с 5-дневным лагом. Задокументировать.

### 22. `volume_pressure.py` — `_cmf_last()` не проверяет длину данных

Если `len(high) < period`, rolling вернёт NaN, но `fillna(0.0)` скроет это. Лучше explicit check.

### 23. `intraday.py` — `_blend_results()` — equal weighting всех провайдеров

T-Bank и MOEX ISS для одного .ME тикера могут давать разные цены. Equal blend может размывать сигнал.

**Рекомендация:** Weighted blend по задержке/качеству источника.

### 24. `llm_sentiment.py` — `_cache_set()` удаляет 100 записей при >500

```python
if len(_cache) > 500:
    oldest = sorted(_cache.items(), key=lambda x: x[1][1])[:100]
    for k, _ in oldest:
        del _cache[k]
```
`sorted()` на 500+ элементах — O(n log n). Для 500 элементов это ок, но лучше использовать `heapq.nsmallest(100, ...)`.

### 25. `subscriptions.py` — `get_user_tier()` fallback на "free" при любой ошибке БД

```python
except Exception:
    return "free"
```

Если БД временно недоступна, все пользователи теряют свои тарифы.

**Фикс:** Кэшировать последний известный tier пользователя in-memory.

---

## 📋 РЕКОМЕНДУЕМЫЙ ПЛАН ДЕЙСТВИЙ

### Priority 1 (неделя 1) — DONE:
1. ✅ **FIXED** — race condition в `_health_state` → добавлен `_health_lock` + `_health_update()` (scheduler.py)
2. ✅ **FIXED** — thread-safety `_CACHE` → добавлен `_cache_lock` + `_cache_get()`/`_cache_set()` (market_data.py)
3. ✅ **FIXED** — retry для HTTP запросов:
   - `moex_iss.py`: `_moex_get()` с `@retry_with_backoff` (2 retries)
   - `llm_sentiment.py`: `_ollama_chat_raw()` с `@retry_with_backoff` (2 retries)
   - `macro_calendar.py`: `_finnhub_economic_get()` с `@retry_with_backoff` (2 retries)
   - `news_feeds.py` — уже имел встроенный retry ✅
   - `finnhub_live.py`, `polygon_data.py` — уже используют retry_with_backoff ✅
4. ✅ **FIXED** — dataclass `CrossAssetRegime` перемещён до type hint (regime.py)

### Priority 2 (неделя 2):
4. ✅ Вынести data path логику в единый config
5. ✅ Перенести type hints после dataclass (regime.py)
6. ✅ Задокументировать magic numbers

### Priority 3 (неделя 3-4):
7. ✅ Рефактор `_gather_inputs()` → pipeline stages
8. ✅ Тесты для core modules (technical, trade_plan, signal_filter)
9. ✅ DB-based rate limiting вместо in-memory

### Priority 4 (когда будет время):
10. ✅ Gap-aware outcome tracking
11. ✅ Kelly result caching
12. ✅ LRU cache для LLM sentiment
13. ✅ Settings dataclass для всех env vars

---

## 💎 ИТОГ

Проект **production-ready на 75%**. Ядро анализа (technical + momentum + volume + sentiment) написано грамотно. Основные риски:

1. **Concurrency bugs** — несколько shared state объектов без синхронизации
2. **Resilience** — нет retry, нет graceful degradation при partial failures
3. **Test coverage** — недостаточно для уверенности в production

После исправления Priority 1-2 — можно смело запускать в production для реальных пользователей.
