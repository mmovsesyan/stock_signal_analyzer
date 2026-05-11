FROM python:3.9-slim

# Метаданные
LABEL maintainer="Stock Signal Analyzer"
LABEL description="Automated stock signal analysis and backtesting"
LABEL version="1.2.0"

# Установить системные зависимости
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Создать рабочую директорию
WORKDIR /app

# Копировать requirements
COPY requirements.txt .
COPY requirements-api.txt .
COPY requirements-scale.txt .
COPY requirements-tbank.txt .

# Установить Python зависимости
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -r requirements-api.txt && \
    pip install --no-cache-dir -r requirements-scale.txt

# T-Bank SDK (отдельно, может быть недоступен при сборке)
RUN pip install --no-cache-dir tinkoff-investments 2>/dev/null || \
    pip install --no-cache-dir --index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple --extra-index-url https://pypi.org/simple t-tech-investments 2>/dev/null || \
    echo "T-Bank SDK not available at build time - will retry at startup"

# Скрипт автоустановки SDK при старте (если не установился при сборке)
RUN echo '#!/bin/bash\n\
if ! python -c "import tinkoff.invest" 2>/dev/null; then\n\
  echo "Installing T-Bank SDK..."\n\
  pip install -q tinkoff-investments 2>/dev/null || \\\n\
  pip install -q --index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple --extra-index-url https://pypi.org/simple t-tech-investments 2>/dev/null || true\n\
fi\n\
exec "$@"' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# Копировать код приложения
COPY . .

# Создать директории для данных
RUN mkdir -p /data/signals /data/outcomes /app/logs

# Переменные окружения по умолчанию
ENV SSA_SIGNAL_LOG=/data/signals/signals.jsonl
ENV STOCK_SIGNAL_DATA=/data
ENV COLLECT_INTERVAL_SEC=14400
ENV PYTHONUNBUFFERED=1

# Volumes для персистентных данных
VOLUME ["/data", "/app/logs"]

# Healthcheck
HEALTHCHECK --interval=5m --timeout=3s \
    CMD python -c "import os; exit(0 if os.path.exists('/data/signals/signals.jsonl') else 1)"

# Точка входа (автоустановка SDK + запуск)
ENTRYPOINT ["/app/entrypoint.sh", "python"]

# По умолчанию запускать Telegram бота
CMD ["telegram_bot.py"]
