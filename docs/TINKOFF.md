# Т-Инвестиции (Т-Банк): котировки в анализе

В отчёте **нет торговых сигналов от брокера**. Используется **официальный Python SDK** для запроса **последней цены** по API (как в [документации T-Bank](https://developer.tbank.ru/invest/sdk/python_sdk/faq_python/)), чтобы **дополнить** блок «онлайн» для тикеров `.ME` вместе с MOEX ISS.

## Установка SDK (рекомендуется)

Команда из [портала разработчиков](https://developer.tbank.ru/invest/sdk/python_sdk/faq_python/):

```bash
pip install t-tech-investments --index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple
```

В репозитории есть файл **`requirements-tbank.txt`** (тот же индекс + запасной PyPI):

```bash
pip install -r requirements-tbank.txt
```

- Публичный репозиторий и примеры: [opensource.tbank.ru — invest-python](https://opensource.tbank.ru/invest/invest-python)
- [Примеры подключения и получения данных](https://opensource.tbank.ru/invest/invest-python/-/tree/main/examples)

## Запасной вариант (PyPI)

Если установка с индекса Т-Банка недоступна (сеть, версия Python):

```bash
pip install tinkoff-investments
```

В коде используется модуль **`tinkoff.invest`** — он обычно совпадает у обоих пакетов.

## Токен

1. [Т-Инвестиции](https://www.tinkoff.ru/invest/) → настройки → выпуск **токена для T-Invest API** (формулировка может отличаться).
2. В окружении:

```bash
export TINKOFF_INVEST_TOKEN="t.…"
```

Допустимо также имя **`TINKOFF_TOKEN`**. Удобно хранить в **`.env`** в корне проекта (подхватывается при запуске `main.py` и `telegram_bot.py`).

## Поведение в коде

- Реализация: `stock_signal_analyzer/tbank_invest.py`, старый импорт `tinkoff_quotes` — алиас.
- Для **`.ME`**: MOEX + при наличии SDK и токена — **Т-Инвестиции**, смешивание в `intraday.py`.
- Тикеры в T-Invest обычно **без** `.ME` (например `SBER`); код сам убирает суффикс.

## Примеры роботов на портале T-API

На странице [Примеры готовых роботов](https://developer.tbank.ru/invest/sdk/python_sdk/robots) перечислены сторонние репозитории (объёмный профиль, интервальные стратегии, MA, Telegram) и фрагмент с **`MovingAverageStrategy`** из SDK — это **торговые** сценарии со счётом.

В этом проекте используется только **рынок-данные** (котировки и свечи), без заявок. Идея **объёмного якоря** (VWAP / упрощённый POC по свечам, как в духе volume-profile роботов) встроена в блок онлайна для `.ME`:

- реализация: `tbank_invest.fetch_session_volume_context`, лёгкая подстройка интрадей-скора в `intraday.py`;
- отключить дополнительный запрос свечей: `SSA_TBANK_VOLUME=0` (по умолчанию включено).

## Ограничения

- Нужен действующий токен и соблюдение правил API Т-Банка.
- Без SDK и токена анализ **работает** за счёт Yahoo + MOEX.
