# Рекомендации по оптимизации производительности

## Текущая проблема
Анализ одного тикера занимает ~16 секунд, что медленно для интерактивного использования.

## Предложенные улучшения

### 1. Кэширование котировок (приоритет: ВЫСОКИЙ)

**Файл:** `stock_signal_analyzer/market_data.py`

Добавить простое in-memory кэширование с TTL:

```python
import time
from functools import lru_cache

_CACHE = {}
_CACHE_TTL = 300  # 5 минут

def fetch_snapshot_with_meta(symbol: str, force_refresh: bool = False):
    cache_key = symbol.upper()
    now = time.time()
    
    if not force_refresh and cache_key in _CACHE:
        data, timestamp = _CACHE[cache_key]
        if now - timestamp < _CACHE_TTL:
            return data
    
    # Существующий код загрузки...
    result = _fetch_from_yahoo(symbol)
    _CACHE[cache_key] = (result, now)
    return result
```

**Ожидаемый эффект:** Снижение времени с 16s до 2-3s при повторных запросах.

---

### 2. Параллельные API запросы (приоритет: ВЫСОКИЙ)

**Файл:** `stock_signal_analyzer/engine.py`

Использовать `concurrent.futures` для параллельной загрузки:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def _gather_inputs_parallel(symbol, key, ...):
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(fetch_snapshot_with_meta, symbol): 'snapshot',
            executor.submit(fetch_company_news, symbol, key): 'news',
            executor.submit(fetch_ticker_news_google, symbol): 'google_news',
            executor.submit(fetch_macro_headlines): 'macro'
        }
        
        results = {}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result(timeout=10)
            except Exception as e:
                results[name] = None
                
        return results
```

**Ожидаемый эффект:** Снижение времени с 16s до 8-10s.

---

### 3. Опциональный "быстрый режим" (приоритет: СРЕДНИЙ)

**Файл:** `main.py`, `telegram_bot.py`

Добавить флаг `--fast` для пропуска медленных компонентов:

```python
def build_report(symbol, fast_mode=False, ...):
    # Пропустить:
    if fast_mode:
        news_score = 0.0  # Пропустить новости
        intraday_score = None  # Пропустить real-time
        # Использовать только технику + импульс + объём
```

**Ожидаемый эффект:** Снижение времени до 3-5s в fast mode.

---

### 4. Async/await для сетевых запросов (приоритет: НИЗКИЙ)

Переписать на `aiohttp` + `asyncio` для истинной асинхронности:

```python
import aiohttp
import asyncio

async def fetch_all_data(symbol):
    async with aiohttp.ClientSession() as session:
        tasks = [
            fetch_snapshot_async(session, symbol),
            fetch_news_async(session, symbol),
            fetch_macro_async(session)
        ]
        return await asyncio.gather(*tasks)
```

**Ожидаемый эффект:** Снижение времени до 5-7s.

**Недостаток:** Требует переписывания большой части кода.

---

### 5. Предзагрузка популярных тикеров (приоритет: НИЗКИЙ)

**Файл:** `telegram_bot.py`

При старте бота предзагрузить данные для популярных тикеров:

```python
async def preload_popular_tickers(context):
    popular = ['AAPL', 'MSFT', 'GOOGL', 'SBER.ME', 'GAZP.ME']
    for symbol in popular:
        try:
            build_report(symbol)  # Заполнит кэш
        except:
            pass
```

---

## Приоритетный план внедрения

1. **Неделя 1:** Кэширование (простое, большой эффект)
2. **Неделя 2:** Параллельные запросы (средняя сложность, хороший эффект)
3. **Неделя 3:** Быстрый режим (простое, опциональное)
4. **Будущее:** Async/await (сложно, требует рефакторинга)

---

## Метрики для отслеживания

```python
import time

def benchmark_analysis(symbol):
    start = time.time()
    report = build_report(symbol)
    elapsed = time.time() - start
    
    print(f"Анализ {symbol}: {elapsed:.2f}s")
    
    # Целевые метрики:
    # - Первый запрос: < 10s
    # - Повторный запрос (кэш): < 3s
    # - Fast mode: < 5s
```
