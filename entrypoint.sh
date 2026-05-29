#!/bin/bash
set -e

# gRPC timeout для T-Bank API (предотвращает зависания при UNAVAILABLE)
export GRPC_PYTHON_DEFAULT_TIMEOUT_SECONDS=5

if ! python -c "import tinkoff.invest" 2>/dev/null; then
  if python -c "import t_tech.invest" 2>/dev/null; then
    # t-tech-investments установлен, но импортируется как t_tech.invest
    # Создаём symlink tinkoff → t_tech для обратной совместимости
    SITE=$(python -c "import site; print(site.getsitepackages()[0])")
    if [ -d "$SITE/t_tech" ] && [ ! -d "$SITE/tinkoff" ]; then
      ln -s "$SITE/t_tech" "$SITE/tinkoff"
      echo "T-Bank SDK linked: tinkoff → t_tech"
    fi
  else
    echo "Installing T-Bank SDK..."
    # Попытка 1: pip install tinkoff-investments (PyPI fallback)
    if pip install -q tinkoff-investments 2>/dev/null; then
      echo "T-Bank SDK installed from PyPI (tinkoff-investments)"
    else
      # Попытка 2: официальный репозиторий T-Bank (t-tech-investments)
      echo "PyPI fallback failed, trying T-Bank official repository..."
      if pip install -q --index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple --extra-index-url https://pypi.org/simple t-tech-investments; then
        echo "T-Bank SDK installed from official repository (t-tech-investments)"
      else
        echo "WARNING: T-Bank SDK installation failed. Russian tickers (.ME) may not load."
      fi
    fi
    # Повторная попытка symlink
    if python -c "import t_tech.invest" 2>/dev/null; then
      SITE=$(python -c "import site; print(site.getsitepackages()[0])")
      if [ -d "$SITE/t_tech" ] && [ ! -d "$SITE/tinkoff" ]; then
        ln -s "$SITE/t_tech" "$SITE/tinkoff"
        echo "T-Bank SDK linked: tinkoff → t_tech"
      fi
    fi
  fi
fi

# Run DB migrations
python /app/scripts/migrate_add_last_notify.py || true

exec "$@"
