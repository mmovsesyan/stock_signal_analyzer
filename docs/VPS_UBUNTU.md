# Установка на VPS Ubuntu (пошагово)

Инструкция для чистого сервера **Ubuntu 20.04 / 22.04 / 24.04 LTS**.

---

## Шаг 1. Подключение по SSH

На своём компьютере:

```bash
ssh ubuntu@IP_ВАШЕГО_VPS
```

Или:

```bash
ssh root@IP_ВАШЕГО_VPS
```

Если используете ключ:

```bash
ssh -i ~/.ssh/id_rsa ubuntu@IP_ВАШЕГО_VPS
```

Подставьте реальный IP и пользователя (часто `ubuntu`, `root` или имя из панели хостинга).

---

## Шаг 2. Обновление системы (рекомендуется)

На сервере:

```bash
sudo apt update && sudo apt upgrade -y
```

---

## Шаг 3. Установка Git (если ещё нет)

```bash
sudo apt install -y git
```

Проверка:

```bash
git --version
```

---

## Шаг 4. Клонирование репозитория

Замените `URL_РЕПОЗИТОРИЯ` на адрес вашего Git (HTTPS или SSH):

```bash
cd ~
git clone URL_РЕПОЗИТОРИЯ stock_signal_analyzer
cd stock_signal_analyzer
```

Пример с HTTPS:

```bash
git clone https://github.com/ВАШ_ЛОГИН/stock_signal_analyzer.git stock_signal_analyzer
cd stock_signal_analyzer
```

---

## Шаг 5. Запуск интерактивной установки

```bash
bash install.sh
```

В меню выберите **1** — полная установка.

Скрипт сам:

- поставит `python3`, `venv`, `pip` (нужен **Python 3.9+**, см. `scripts/install_linux.sh`);
- создаст виртуальное окружение `.venv` и зависимости;
- предложит установить T-Bank SDK (для российских акций);
- запросит токены (Telegram, при необходимости T-Bank и Finnhub);
- настроит systemd-сервис `ssa-bot`;
- при согласии включит автозапуск и запустит бота.

**Другие сценарии из репозитория**

| Способ | Когда удобно |
|--------|----------------|
| `bash deploy.sh` из каталога с клоном на сервере | Копия в `/opt/stock-signal-analyzer` (переменная `APP_DIR`), тот же сервис **`ssa-bot`**, что и в `install.sh` |
| `deploy/ubuntu/SETUP.txt` + `stock-signal-telegram-bot.service` | Ручной деплой, отдельный пользователь `stockbot`, другое **имя** unit-файла — не путать с `ssa-bot` |
| `scripts/install_linux.sh` | Интерактивно без systemd: venv, зависимости, опционально `.env` |

**Что подготовить заранее:**

| Что | Зачем |
|-----|--------|
| Токен Telegram от [@BotFather](https://t.me/BotFather) | Обязательно для бота |
| Токен T-Инвестиций ([настройки API](https://www.tbank.ru/invest/settings/api/)) | Для РФ-тикеров и истории через T-Bank |
| Ключ [Finnhub](https://finnhub.io/) (бесплатный) | Новости, макро-календарь, US-данные |

---

## Шаг 6. Если установка без `install.sh` (вручную)

```bash
cd ~/stock_signal_analyzer
sudo apt install -y python3 python3-venv python3-pip
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r requirements-tbank.txt
```

Создайте `.env` (скопируйте пример и заполните):

```bash
nano .env
```

Минимум для бота:

```env
TELEGRAM_BOT_TOKEN=ваш_токен_от_BotFather
```

Для РФ и лога сигналов добавьте:

```env
TINKOFF_INVEST_TOKEN=ваш_токен_tbank
FINNHUB_API_KEY=ваш_ключ
SSA_SIGNAL_LOG=/home/ubuntu/stock_signal_analyzer/data/signals.jsonl
COLLECT_INTERVAL_SEC=14400
```

Сохраните: `Ctrl+O`, Enter, `Ctrl+X`.

Проверка CLI:

```bash
.venv/bin/python main.py AAPL
```

---

## Шаг 7. Firewall (опционально)

Если включён UFW и бот не получает обновления от Telegram — для **polling** входящие порты для бота **не нужны**. Если позже включите webhook — откройте нужный порт под ваш прокси.

Проверка UFW:

```bash
sudo ufw status
```

---

## Шаг 8. Управление ботом после установки

```bash
sudo systemctl status ssa-bot
sudo systemctl restart ssa-bot
sudo journalctl -u ssa-bot -f
```

Повторный запуск мастера настроек:

```bash
cd ~/stock_signal_analyzer
bash install.sh
```

Пункты **3** (токены), **5** (старт/стоп/логи), **6** (проверка) — без полной переустановки.

---

## Частые проблемы

**`Permission denied` при `git clone` по SSH**  
Настройте SSH-ключ в GitHub/GitLab или используйте HTTPS с токеном доступа.

**`sudo: command not found`**  
Вы под root без sudo — выполняйте команды без `sudo` или установите: `apt install sudo`.

**Бот не отвечает**  
Проверьте токен в `.env`, логи: `journalctl -u ssa-bot -n 50`.

**РФ-тикеры без данных**  
Нужны `TINKOFF_INVEST_TOKEN` и успешная установка `requirements-tbank.txt`.

**Мало RAM**  
Минимум около **512 MB–1 GB**; при OOM увеличьте swap или тариф VPS.

---

## Краткая шпаргалка

```text
1. ssh ubuntu@IP
2. sudo apt update && sudo apt upgrade -y
3. sudo apt install -y git
4. git clone <URL> stock_signal_analyzer && cd stock_signal_analyzer
5. bash install.sh   → выбрать «1», ввести токены
6. sudo journalctl -u ssa-bot -f
```

Готово: бот работает как сервис `ssa-bot` и перезапускается при сбоях.
